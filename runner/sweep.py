"""Resumable SQLite sweep: add, run --workers N, status. All paths are repository-local."""
from proxy.plan import CONDITIONS
import argparse
import fcntl
import itertools
import json
import multiprocessing as mp
import os
from pathlib import Path
import sqlite3
import subprocess
import uuid
import time

from agent.browser import ROOT
from agent.llm import CONFIG
from runner.run_episode import load_task, POLICIES
from runner.budget import Budget, MONTHLY_PAUSE
from runner.worker_proxy import WorkerProxy, die_with_parent

DEFAULT_DB = ROOT / 'runs/queue.sqlite'


class Queue:
    def __init__(self, path=DEFAULT_DB):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, task TEXT NOT NULL, site TEXT NOT NULL,
                model TEXT NOT NULL, policy TEXT NOT NULL, condition TEXT NOT NULL, seed INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                episode_id TEXT, worker INTEGER, started_at REAL DEFAULT 0, available_at REAL DEFAULT 0, error TEXT DEFAULT '',
                UNIQUE(name, task, model, policy, condition, seed))''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(queue)')}
            for column in ('deferrals', 'retry_after'):
                if column not in columns:
                    db.execute(f'ALTER TABLE queue ADD COLUMN {column} REAL NOT NULL DEFAULT 0')
            db.execute('''CREATE TABLE IF NOT EXISTS scheduler_events (
                timestamp REAL, name TEXT, worker INTEGER, error TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS history (
                queue_id INTEGER, episode_id TEXT, error TEXT, timestamp REAL)''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def add(self, name, tasks, models, policies, conditions, seeds):
        self.require_enabled(models)
        unknown = set(policies) - set(POLICIES)
        if unknown:
            raise ValueError('unknown policies: ' + ', '.join(sorted(unknown)))
        with self.connect() as db:
            before = db.total_changes
            for task, model, policy, condition, seed in itertools.product(tasks, models, policies, conditions, seeds):
                path = Path(task).resolve()
                if not path.is_relative_to(ROOT):
                    raise ValueError('task outside repository')
                data = load_task(path)
                db.execute('''INSERT OR IGNORE INTO queue
                    (name,task,site,model,policy,condition,seed) VALUES (?,?,?,?,?,?,?)''',
                    (name, str(path.relative_to(ROOT)), data['site'], model, policy, condition, seed))
            return db.total_changes - before

    @staticmethod
    def require_enabled(models):
        for model in models:
            if model not in CONFIG['models'] or not CONFIG['models'][model].get('enabled', True):
                raise ValueError(f'disabled or unknown sweep model: {model}')

    def paused(self):
        return Budget(ROOT / 'runs/budget.sqlite').paused()

    def resume(self):
        # Allow the previously rejected rows to retry immediately after upgrade.
        with self.connect() as db:
            db.execute("UPDATE queue SET available_at=0,retry_after=0 WHERE status='deferred' AND error LIKE ?",
                       ('%' + MONTHLY_PAUSE + '%',))
        Budget(ROOT / 'runs/budget.sqlite').resume()

    def claim(self, name, worker, site=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.paused():
                return None
            row = db.execute('''SELECT * FROM queue WHERE name=? AND status IN ('pending','budget_wait','deferred')
                AND available_at<=? AND (? IS NULL OR site=?)
                AND site NOT IN (SELECT site FROM queue WHERE status='running')
                ORDER BY id LIMIT 1''', (name, time.time(), site, site)).fetchone()
            if row:
                self.require_enabled([row['model']])
                db.execute("UPDATE queue SET status='running',attempts=attempts+1,worker=?,started_at=? WHERE id=?", (worker, time.time(), row['id']))
                return dict(row) | {'attempts': row['attempts'] + 1}

    def finish(self, row, record):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._finish(db, row, record)

    def _finish(self, db, row, record):
        error = record['error']
        if 'LLM HTTP 429' in error and MONTHLY_PAUSE in error:
            Budget(ROOT / 'runs/budget.sqlite').pause()
        status, available, delay = 'done', 0, 0
        attempts = row['attempts']
        deferrals = row.get('deferrals', 0)
        if error:
            kind = record.get('error_kind')
            if kind == 'budget':
                status, attempts = 'budget_wait', attempts - 1
                delay = max(1, record.get('retry_after') or 60)
            elif kind == 'rate_limit' or 'LLM HTTP 429' in error:
                status, attempts = 'deferred', attempts - 1
                delay = record.get('retry_after')
                if delay is None:
                    delay = min(7200, 600 * 2 ** min(int(deferrals), 4))
                delay = max(1, delay)
                deferrals += 1
            else:
                status = 'pending' if attempts < 2 else 'failed'
            if delay:
                available = time.time() + delay
        if (ROOT / f"runs/dirty_{row['site']}").exists() or 'dirty_state' in error:
            status = 'failed'
        db.execute('''UPDATE queue SET status=?,attempts=?,episode_id=?,error=?,available_at=?,worker=NULL,
            deferrals=?,retry_after=? WHERE id=?''',
            (status, attempts, record['episode'], error, available, deferrals, delay, row['id']))
        db.execute('INSERT INTO history VALUES (?,?,?,?)',
                   (row['id'], record['episode'], error, time.time()))
        return status

    def recover(self, name, worker=None):
        """Call only under the exclusive scheduler lock, after old workers exited."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            query = "SELECT * FROM queue WHERE name=? AND status='running'"
            params = [name]
            if worker is not None:
                query += ' AND worker=?'
                params.append(worker)
            for row in db.execute(query, params).fetchall():
                # If record.json was committed before the queue transaction, reconcile it.
                candidates = []
                for path in (ROOT / 'runs').glob('*/record.json'):
                    r = json.loads(path.read_text())
                    task_id = load_task(ROOT / row['task'])['id']
                    if (r['task'], r['model'], r['policy'], r['condition'], r['seed'], r.get('worker')) == (
                            task_id, row['model'], row['policy'], row['condition'], row['seed'], row['worker']):
                        if path.stat().st_mtime >= row['started_at']:
                            candidates.append((path.stat().st_mtime, r))
                # History already consumed records are not evidence of this attempt.
                used = {r[0] for r in db.execute('SELECT episode_id FROM history')}
                candidates = [(t, r) for t, r in candidates if r['episode'] not in used]
                if candidates:
                    record = max(candidates, key=lambda x: x[0])[1]
                    self._finish(db, dict(row), record)
                else:
                    db.execute("UPDATE queue SET status=?,error='worker_crash',worker=NULL WHERE id=?",
                               ('pending' if row['attempts'] < 2 else 'failed', row['id']))
                    db.execute('INSERT INTO history VALUES (?,NULL,?,?)', (row['id'], 'worker_crash', time.time()))


def worker_loop(path, name, worker, max_steps, site=None):
    die_with_parent()
    queue = Queue(path)
    proxy = WorkerProxy(worker)
    # One owner across sweep databases; episodes additionally own worker-N.lock.
    ownership = ROOT / f'runs/plans/owner-{worker}.lock'
    ownership.parent.mkdir(parents=True, exist_ok=True)
    with ownership.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            while True:
                if queue.paused():
                    return
                try:
                    proxy.ensure()
                except Exception as exc:
                    with queue.connect() as db:
                        db.execute('INSERT INTO scheduler_events VALUES (?,?,?,?)',
                                   (time.time(), name, worker, f'proxy startup failed: {type(exc).__name__}'))
                    print(f'worker {worker} proxy startup failed: {type(exc).__name__}; stopping without consuming pending rows', flush=True)
                    return
                row = queue.claim(name, worker, site)
                if row is None:
                    if queue.paused():
                        return
                    with queue.connect() as db:
                        left = db.execute("SELECT COUNT(*) FROM queue WHERE name=? AND (? IS NULL OR site=?) AND status IN ('pending','running','budget_wait','deferred')", (name, site, site)).fetchone()[0]
                        wake = db.execute("SELECT MIN(available_at) FROM queue WHERE name=? AND (? IS NULL OR site=?) AND status IN ('pending','budget_wait','deferred')", (name, site, site)).fetchone()[0]
                    if not left:
                        return
                    time.sleep(min(60, max(1, (wake or 0) - time.time())))
                    continue
                try:
                    proxy.ensure()
                    command = ['nohup', str(ROOT / '.venv/bin/python'), '-m', 'runner.run_episode',
                        '--task', row['task'], '--model', row['model'], '--policy', row['policy'],
                        '--condition', row['condition'], '--seed', str(row['seed']), '--worker', str(worker),
                        '--proxy-port', str(proxy.port), '--max-steps', str(max_steps)]
                    # Isolate Playwright/CDP state per episode. The worker still owns
                    # one persistent proxy; a crashed browser cannot poison later runs.
                    episode_log_name = f"sweep-episode-{row['id']}-a{row['attempts']}-{uuid.uuid4().hex[:8]}"
                    logpath = ROOT / f'logs/{episode_log_name}.log'
                    with logpath.open('w') as output:
                        process = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=output,
                            stdin=subprocess.DEVNULL, preexec_fn=die_with_parent)
                        (ROOT / f'logs/{episode_log_name}.pid').write_text(str(process.pid) + '\n')
                        try:
                            process.wait(timeout=1200)
                        except subprocess.TimeoutExpired:
                            process.terminate()
                            try:
                                process.wait(timeout=15)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
                            raise TimeoutError('episode exceeded 20 minute watchdog')
                    try:
                        record = json.loads(logpath.read_text().splitlines()[-1])
                        if not isinstance(record, dict) or 'episode' not in record:
                            raise ValueError
                    except (ValueError, IndexError):
                        raise RuntimeError('episode process failed before publishing a record') from None
                    if proxy.process.poll() is not None and not record['error']:
                        raise RuntimeError('worker proxy died')
                except Exception as exc:
                    # Do not log exception bodies from transports or third party code.
                    record = dict(episode=None, error=f'{type(exc).__name__}: worker infrastructure failure (see episode log)', error_kind='infrastructure')
                status = queue.finish(row, record)
                print(json.dumps({'queue_id': row['id'], 'status': status, 'episode': record['episode'],
                                  'error': record['error'], 'retry_after': record.get('retry_after')}), flush=True)
                if (ROOT / f"runs/dirty_{row['site']}").exists():
                    print(f"worker {worker} stopped: dirty site {row['site']}", flush=True)
                    with queue.connect() as db:
                        db.execute("UPDATE queue SET status='failed',error='dirty_site' WHERE site=? AND status IN ('pending','budget_wait','deferred')", (row['site'],))
                    return
                if status == 'budget_wait':
                    print(f"BudgetExceeded: deferring model {row['model']} until its window frees", flush=True)
        finally:
            proxy.close()


def run(args):
    queue = Queue(args.db)
    with Path(str(args.db) + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if getattr(args, 'command', None) == 'resume':
            queue.resume()
        if queue.paused():
            print('Workspace remains paused; use sweep resume after allowance is restored.', flush=True)
            return
        with queue.connect() as db:
            queue.require_enabled([row[0] for row in db.execute(
                "SELECT DISTINCT model FROM queue WHERE name=? AND status IN ('pending','running','budget_wait','deferred')", (args.name,))])
        queue.recover(args.name)
        context = mp.get_context('spawn')
        children = {}
        def spawn(worker):
            child = context.Process(target=worker_loop, args=(args.db, args.name, worker, args.max_steps,
                args.sites[worker] if args.sites else None))
            child.start()
            children[worker] = child
        for worker in range(args.workers):
            spawn(worker)
        try:
            while children:
                for worker, child in list(children.items()):
                    child.join(timeout=.2)
                    if child.is_alive():
                        continue
                    del children[worker]
                    if child.exitcode:
                        queue.recover(args.name, worker)
                        print(f'worker {worker} exited {child.exitcode}; recovered its interrupted queue row', flush=True)
                        with queue.connect() as db:
                            left = db.execute("SELECT COUNT(*) FROM queue WHERE name=? AND status IN ('pending','budget_wait','deferred')", (args.name,)).fetchone()[0]
                        if left and not queue.paused():
                            spawn(worker)
        finally:
            for child in children.values():
                if child.is_alive():
                    child.terminate()
                child.join()


def status(args):
    queue = Queue(args.db)
    with queue.connect() as db:
        for row in db.execute('''SELECT model,policy,condition,status,COUNT(*) AS n FROM queue
                WHERE name=? GROUP BY model,policy,condition,status ORDER BY model,condition,status''', (args.name,)):
            print(json.dumps(dict(row)))
        counts = dict(db.execute('SELECT status,COUNT(*) FROM queue WHERE name=? GROUP BY status', (args.name,)))
        print(json.dumps({'name': args.name, 'counts': counts}))
    budget = Budget(ROOT / 'runs/budget.sqlite')
    print(json.dumps({'workspace_spend': budget.workspace_spend(), 'workspace_paused': budget.paused()}))
    with budget.connect() as db:
        print(json.dumps({'spend_per_model': dict(db.execute('SELECT model,SUM(cost) FROM calls GROUP BY model'))}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, default=DEFAULT_DB)
    sub = p.add_subparsers(dest='command', required=True)
    add = sub.add_parser('add')
    add.add_argument('--tasks', nargs='+', required=True)
    add.add_argument('--models', nargs='+', choices=list(CONFIG['models']), required=True)
    add.add_argument('--policies', nargs='+', default=['B0'], choices=POLICIES)
    add.add_argument('--conditions', nargs='+', default=['C0'], choices=CONDITIONS)
    add.add_argument('--seeds', nargs='+', type=int, default=[0])
    execute = sub.add_parser('run')
    execute.add_argument('--workers', type=int, choices=range(1, 17), default=2)
    execute.add_argument('--max-steps', type=int, default=30)
    execute.add_argument('--sites', nargs='+', choices=['reddit', 'shopping_admin'],
                         help='Assign one site per worker, in worker order')
    resume = sub.add_parser('resume', parents=[execute], add_help=False,
                            help='Clear workspace pause and restart this sweep')
    attach = sub.add_parser('attach-worker', help='Attach an isolated worker without recovering or stopping a live scheduler')
    attach.add_argument('--worker', required=True, type=int)
    attach.add_argument('--site', choices=['reddit', 'shopping_admin'])
    attach.add_argument('--max-steps', type=int, default=30)
    report = sub.add_parser('status')
    for child in (add, execute, resume, attach, report):
        child.add_argument('--name', default='default')
    args = p.parse_args()
    if args.command == 'add':
        print(f'Added {Queue(args.db).add(args.name, args.tasks, args.models, args.policies, args.conditions, args.seeds)} episodes')
    elif args.command in ('run', 'resume'):
        if args.sites and len(args.sites) != args.workers:
            p.error('--sites must contain one site per worker')
        run(args)
    elif args.command == 'attach-worker':
        if args.worker < 0 or args.max_steps < 1:
            p.error('worker must be nonnegative and max-steps positive')
        worker_loop(args.db, args.name, args.worker, args.max_steps, args.site)
    else:
        status(args)


if __name__ == '__main__':
    main()
