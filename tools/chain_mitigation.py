"""Fail-closed, one-shot handoff from the main sweep to mitigation."""
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

from tools import pristine_state

ROOT = Path(__file__).resolve().parents[1]
UNFINISHED = ('pending', 'running', 'budget_wait', 'deferred')


class Blocked(RuntimeError):
    pass


def process_identity(pid):
    """Start time distinguishes PID reuse; zombies have already exited."""
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] in ('Z', 'X') else fields[19]
    except FileNotFoundError:
        return None


def read_pid(path):
    pid = int(path.read_text().strip())
    if pid <= 1:
        raise Blocked('invalid scheduler PID')
    return pid


def wait_for_main():
    path = ROOT / 'logs/sweep-m5-main.pid'
    pid = read_pid(path)
    identity = process_identity(pid)
    if identity is not None:
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        if (b'runner.sweep' not in args or b'm5-main' not in args
                or Path(f'/proc/{pid}/cwd').resolve() != ROOT):
            raise Blocked('main PID does not identify the project scheduler')
    print(f'Waiting for m5-main scheduler {pid}', flush=True)
    while identity is not None and process_identity(pid) == identity:
        if read_pid(path) != pid:
            raise Blocked('main scheduler PID changed; re-arm the chain explicitly')
        time.sleep(10)
    if read_pid(path) != pid or process_identity(pid) is not None:
        raise Blocked('main scheduler PID changed or was reused')


def validate_queue():
    with sqlite3.connect(f'file:{ROOT}/runs/m5_queue.sqlite?mode=ro', uri=True) as db:
        counts = dict(db.execute("SELECT status,count(*) FROM queue WHERE name='m5-main' GROUP BY status"))
        if not counts:
            raise Blocked('m5-main queue is missing')
        remaining = {s: n for s, n in counts.items() if s in UNFINISHED}
        if remaining:
            raise Blocked('main scheduler exited with unfinished rows: ' + json.dumps(remaining, sort_keys=True))
        if set(counts) - {*UNFINISHED, 'done', 'failed', 'skipped'}:
            raise Blocked('main queue contains an unknown status')
        if db.execute("SELECT count(*) FROM queue WHERE status='running'").fetchone()[0]:
            raise Blocked('another sweep has running rows')
        if not db.execute("SELECT count(*) FROM queue WHERE name='m5-mitigation' AND status IN (?,?,?,?)",
                          UNFINISHED).fetchone()[0]:
            raise Blocked('mitigation has no unfinished rows')
    with sqlite3.connect(f'file:{ROOT}/runs/budget.sqlite?mode=ro', uri=True) as db:
        if db.execute('SELECT 1 FROM workspace_pause').fetchone():
            raise Blocked('workspace is paused')


def ensure_clean():
    if any((ROOT / 'runs').glob('dirty_*')):
        raise Blocked('dirty site marker present')


def launch_mitigation():
    env = dict(os.environ, TMPDIR=str(ROOT / '.tmp'), XDG_CACHE_HOME=str(ROOT / '.cache'),
               PLAYWRIGHT_BROWSERS_PATH=str(ROOT / '.cache/ms-playwright'), PYTHONUNBUFFERED='1')
    with (ROOT / 'logs/sweep-m5-mitigation.log').open('a') as log:
        process = subprocess.Popen(['nohup', 'setsid', '.venv/bin/python', '-m', 'runner.sweep',
            '--db', 'runs/m5_queue.sqlite', 'run', '--name', 'm5-mitigation', '--workers', '2'],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    (ROOT / 'logs/sweep-m5-mitigation.pid').write_text(f'{process.pid}\n')
    # Persist before checking startup: a later chain must never launch twice.
    (ROOT / 'logs/chain_mitigation.started.json').write_text(
        json.dumps({'at': time.time(), 'pid': process.pid}) + '\n')
    time.sleep(1)
    if process.poll() is not None or os.getsid(process.pid) != process.pid:
        raise Blocked('mitigation scheduler failed to start; inspect its log')
    print(f'Launched m5-mitigation scheduler {process.pid}', flush=True)


def handoff():
    if (ROOT / 'logs/chain_mitigation.started.json').exists():
        raise Blocked('mitigation was already launched by this chain')
    pidfile = ROOT / 'logs/sweep-m5-mitigation.pid'
    if pidfile.exists() and process_identity(read_pid(pidfile)) is not None:
        raise Blocked('mitigation scheduler is already alive')
    # Match the scheduler's own lock and hold all site locks during the audit.
    # Children do not inherit these descriptors. Release before scheduler launch.
    with ExitStack() as stack:
        lock = stack.enter_context((ROOT / 'runs/m5_queue.sqlite.lock').open('a'))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        validate_queue()
        ensure_clean()
        for site in pristine_state.audit.SITES:
            stack.enter_context(pristine_state.site_lock(site))
        results = {site: pristine_state.check(site) for site in pristine_state.audit.SITES}
        (ROOT / 'logs/chain_mitigation.drift.json').write_text(json.dumps(results) + '\n')
        if any(result['drift'] for result in results.values()):
            raise Blocked('pristine state drift check failed')
        validate_queue()
        ensure_clean()
    launch_mitigation()


def main():
    with (ROOT / 'logs/chain_mitigation.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('A mitigation chain is already running', flush=True)
            return 1
        try:
            if (ROOT / 'logs/chain_mitigation.blocked').exists():
                raise Blocked('previous blocked marker requires explicit review before re-arming')
            wait_for_main()
            handoff()
        except Exception as exc:
            # Never log arbitrary transport/DB error bodies.
            reason = str(exc) if isinstance(exc, Blocked) else type(exc).__name__
            (ROOT / 'logs/chain_mitigation.blocked').write_text(
                json.dumps({'at': time.time(), 'reason': reason}) + '\n')
            print('Mitigation chain blocked: ' + reason, flush=True)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
