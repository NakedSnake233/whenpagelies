"""Read-only r3-repeat monitor; signal only the pinned scheduler using a pidfd."""
import argparse
import calendar
import ctypes
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'logs'
NAME = 'r3-repeat'
LIBC = ctypes.CDLL(None, use_errno=True)
# Linux x86_64 syscall ABI; fail closed on other architectures.
if os.uname().machine != 'x86_64':
    raise RuntimeError('unsupported_pidfd_architecture')
LIBC.syscall.restype = ctypes.c_long


def checked(result):
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def log(event, **values):
    with (OUT / 'stop_watcher.log').open('a') as stream:
        stream.write(json.dumps(dict(time=datetime.now(timezone.utc).isoformat(),
                                     event=event, **values)) + '\n')


def proc(pid):
    base = Path('/proc') / str(pid)
    fields = (base / 'stat').read_text().rsplit(')', 1)[1].split()
    return dict(start=fields[19], state=fields[0], parent=int(fields[1]),
                argv=(base / 'cmdline').read_bytes().decode().rstrip('\0').split('\0'),
                cwd=str((base / 'cwd').resolve()))


def alive(pid, start):
    try:
        p = proc(pid)
        return p['start'] == start and p['state'] != 'Z'
    except (OSError, ValueError):
        return False


def readonly(path):
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10)
    db.execute('PRAGMA query_only=ON')
    return db


def snapshot():
    # Match Budget.workspace_spend: provider month, all statuses and all models.
    import yaml
    anchor = yaml.safe_load((ROOT / 'config/budget.yaml').read_text())['provider_month_anchor']
    now = datetime.now(timezone.utc)
    def boundary(offset):
        year, month = divmod(now.year * 12 + now.month - 1 + offset, 12)
        fields = dict(anchor)
        fields['day'] = min(fields['day'], calendar.monthrange(year, month + 1)[1])
        return datetime(year, month + 1, **fields, tzinfo=timezone.utc)
    start = boundary(0)
    if now < start:
        start = boundary(-1)
    with readonly(ROOT / 'runs/budget.sqlite') as db:
        spend = db.execute('SELECT COALESCE(SUM(cost),0) FROM calls WHERE timestamp>=? AND timestamp<=?',
                           (start.timestamp(), now.timestamp())).fetchone()[0]
    with readonly(ROOT / 'runs/r2_queue.sqlite') as db:
        counts = dict(db.execute('SELECT status,COUNT(*) FROM queue WHERE name=? GROUP BY status', (NAME,)))
    return dict(counts=counts, month_spend_usd=spend, month_start_utc=start.isoformat())


def trigger(s):
    c = s['counts']
    if c.get('running', 0):
        return None
    if c.get('budget_wait', 0):
        return 'budget_wait_without_running'
    if s['month_spend_usd'] >= 134.9:
        return 'month_spend_at_least_134.9_without_running'
    if not any(c.get(k, 0) for k in ('pending', 'budget_wait', 'running')):
        return 'no_pending_budget_wait_running'
    return None


def validate(pid, start, guard):
    p = proc(pid)
    a = p['argv']
    expected = ['-m', 'runner.sweep', '--db', 'runs/r2_queue.sqlite', 'run',
                '--name', NAME, '--workers', '2', '--sites', 'reddit', 'shopping_admin', '--max-steps', '30']
    if (pid == guard or p['start'] != start or p['parent'] != guard or
            p['cwd'] != str(ROOT) or a[1:] != expected):
        raise RuntimeError('target_identity_mismatch')
    masks = dict(line.split(':', 1) for line in (Path('/proc') / str(pid) / 'status').read_text().splitlines() if ':' in line)
    if int(masks['SigIgn'], 16) & 2 or not int(masks['SigCgt'], 16) & 2:
        raise RuntimeError('SIGINT_handler_not_available')


def final_logs():
    # These two diagnostics contain guard metadata and pristine check results only.
    for relative in ('logs/r2_guard.json', 'logs/r2_pristine_after.log'):
        path = ROOT / relative
        log('final_artifact', path=str(path), text=path.read_text() if path.exists() else 'MISSING')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', action='store_true')
    args = parser.parse_args()
    if args.snapshot:
        print(json.dumps(snapshot()))
        return
    with (ROOT / 'logs/stop-watcher.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        guard = int((ROOT / 'logs/sweep-r3.pid').read_text())
        gp = proc(guard)
        if gp['cwd'] != str(ROOT) or gp['argv'][1:3] != ['-m', 'tools.r2_guard']:
            raise RuntimeError('guard_identity_mismatch')
        report = json.loads((ROOT / 'logs/r2_guard.json').read_text())
        pid = int(report['child_pid'])
        start = proc(pid)['start']
        validate(pid, start, guard)
        # Pin the process; a recycled numeric PID can never receive the signal.
        fd = checked(LIBC.syscall(434, pid, 0))
        try:
            validate(pid, start, guard)
            log('started', watcher_pid=os.getpid(), target_pid=pid, guard_pid=guard, snapshot=snapshot())
            while alive(guard, gp['start']):
                if not alive(pid, start):
                    log('target_exited_naturally', target_pid=pid)
                    break
                s = snapshot()
                log('poll', **s)
                if trigger(s):
                    validate(pid, start, guard)
                    # Fresh snapshot immediately before signal, no stale read transaction.
                    s = snapshot()
                    reason = trigger(s)
                    if reason:
                        try:
                            checked(LIBC.syscall(424, fd, signal.SIGINT, ctypes.c_void_p(), 0))
                            log('SIGINT_sent', target_pid=pid, reason=reason, **s)
                        except ProcessLookupError:
                            log('target_exited_before_signal', target_pid=pid)
                        break
                time.sleep(60)
            while alive(guard, gp['start']):
                report = json.loads((ROOT / 'logs/r2_guard.json').read_text())
                log('waiting_for_guard', guard_pid=guard, status=report.get('status'))
                if report.get('status') == 'quarantined':
                    log('guard_quarantined_no_intervention')
                time.sleep(60)
            log('guard_exited', guard_pid=guard)
            final_logs()
        finally:
            os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not log arbitrary exception bodies, command lines, or environments.
        log('watcher_failed_without_further_signals', error_type=type(exc).__name__)
        raise SystemExit(1)
