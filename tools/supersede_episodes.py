"""Supersede episodes using stopped-sweep or scoped held-policy operations."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import time

from agent.browser import ROOT
from runner.sweep import Queue


def supersede_drift(queue, name, episode_ids, root=ROOT):
    """Supersede the audited retained IDs, once, preserving every old artifact.

    Several retained attempts can belong to one plan; requeue that plan once.
    The SQLite ledger and append-only exclusion journal make retries idempotent.
    """
    from collections import Counter
    with Path(queue.path + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with queue.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM queue WHERE status='running'").fetchone():
                raise RuntimeError('stop and recover sweep workers before superseding')
            db.execute('''CREATE TABLE IF NOT EXISTS superseded_episodes (
                episode_id TEXT PRIMARY KEY, queue_id INTEGER, reason TEXT, timestamp REAL)''')
            entries, requeue = [], set()
            for episode in sorted(set(episode_ids)):
                if Path(episode).name != episode:
                    raise ValueError('invalid episode ID')
                record = json.loads((root / 'runs' / episode / 'record.json').read_text())
                if record.get('dry_run') or record['episode'] != episode:
                    raise ValueError('audit ID is not a retained real episode')
                matches = db.execute('''SELECT DISTINCT q.* FROM history h JOIN queue q ON q.id=h.queue_id
                    WHERE h.episode_id=? AND q.name=?''', (episode, name)).fetchall()
                if not matches:
                    # M5c also retained standalone seed replicas. Materialize
                    # their exact plan in the main queue instead of omitting them.
                    from runner.run_episode import load_task
                    task_path = ROOT / 'tasks' / (record['task'].replace('-', '_', 1) + '.json')
                    task = load_task(task_path)
                    if task['id'] != record['task'] or task['site'] != record['site']:
                        raise ValueError('standalone episode task mismatch')
                    queue.require_enabled([record['model']])
                    db.execute('''INSERT OR IGNORE INTO queue
                        (name,task,site,model,policy,condition,seed) VALUES (?,?,?,?,?,?,?)''',
                        (name, str(task_path.relative_to(ROOT)), record['site'], record['model'],
                         record['policy'], record['condition'], record['seed']))
                    matches = db.execute('''SELECT * FROM queue WHERE name=? AND task=? AND model=?
                        AND policy=? AND condition=? AND seed=?''',
                        (name, str(task_path.relative_to(ROOT)), record['model'], record['policy'],
                         record['condition'], record['seed'])).fetchall()
                if len(matches) != 1:
                    raise ValueError('audit episode must map to exactly one queue plan')
                row = matches[0]
                if any(record[k] != row[k] for k in ('model', 'policy', 'condition', 'seed', 'site')):
                    raise ValueError('episode/queue plan mismatch')
                existing = db.execute('SELECT reason FROM superseded_episodes WHERE episode_id=?', (episode,)).fetchone()
                if existing:
                    if existing[0] != 'state drift':
                        raise ValueError('audit episode already superseded for another reason')
                    continue
                entry = dict(episode_id=episode, queue_id=row['id'], model=row['model'], condition=row['condition'],
                             name=name, reason='state drift', timestamp=time.time())
                db.execute('INSERT INTO superseded_episodes VALUES (?,?,?,?)',
                           (episode, row['id'], 'state drift', entry['timestamp']))
                entries.append(entry)
                requeue.add(row['id'])
            with (root / 'runs/superseded.jsonl').open('a+') as output:
                fcntl.flock(output, fcntl.LOCK_EX)
                output.seek(0)
                known = {json.loads(line)['episode_id'] for line in output if line.strip()}
                for entry in entries:
                    if entry['episode_id'] not in known:
                        output.write(json.dumps(entry) + '\n')
                output.flush()
                os.fsync(output.fileno())
            for queue_id in requeue:
                db.execute("""UPDATE queue SET status='pending',attempts=0,episode_id=NULL,worker=NULL,
                    started_at=0,available_at=0,error='',deferrals=0,retry_after=0 WHERE id=?""", (queue_id,))
            counts = Counter((e['model'], e['condition']) for e in entries)
            plans = Counter((r['model'], r['condition']) for r in db.execute('SELECT * FROM queue') if r['id'] in requeue)
            return dict(superseded=len(entries), requeued=len(requeue),
                        by_model_condition=[dict(model=m, condition=c, episodes=n, plans=plans[m, c])
                                            for (m, c), n in sorted(counts.items())])


def supersede(queue, name, models, before, root=ROOT):
    reason = 'M5a native action format adapter: rerun with corrected parsing'
    with Path(queue.path + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with queue.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM queue WHERE name=? AND status='running'", (name,)).fetchone():
                raise RuntimeError('stop and recover sweep workers before superseding')
            db.execute('''CREATE TABLE IF NOT EXISTS superseded_episodes (
                episode_id TEXT PRIMARY KEY, queue_id INTEGER, reason TEXT, timestamp REAL)''')
            candidates = db.execute('''SELECT h.episode_id, h.queue_id, q.model FROM history h
                JOIN queue q ON q.id=h.queue_id WHERE q.name=? AND h.timestamp<=?
                AND h.episode_id IS NOT NULL''', (name, before)).fetchall()
            entries, requeue = [], set()
            for row in candidates:
                episode = row['episode_id']
                if row['model'] not in models or not (root / 'runs' / episode / 'record.json').exists():
                    continue
                entry = dict(episode_id=episode, queue_id=row['queue_id'], model=row['model'],
                             name=name, reason=reason, timestamp=time.time(), before=before)
                inserted = db.execute('INSERT OR IGNORE INTO superseded_episodes VALUES (?,?,?,?)',
                    (episode, row['queue_id'], reason, entry['timestamp'])).rowcount
                if inserted:
                    entries.append(entry)
                    requeue.add(row['queue_id'])
            # Publish exclusions before committing requeues. A failed transaction
            # can safely be retried: the DB ledger and JSONL both deduplicate IDs.
            with (root / 'runs/superseded.jsonl').open('a+') as output:
                fcntl.flock(output, fcntl.LOCK_EX)
                output.seek(0)
                known = {json.loads(line)['episode_id'] for line in output if line.strip()}
                for entry in entries:
                    if entry['episode_id'] not in known:
                        output.write(json.dumps(entry) + '\n')
                        known.add(entry['episode_id'])
                output.flush()
                os.fsync(output.fileno())
            for queue_id in requeue:
                db.execute("""UPDATE queue SET status='pending', attempts=0, episode_id=NULL,
                    worker=NULL, started_at=0, available_at=0, error='', deferrals=0, retry_after=0
                    WHERE id=?""", (queue_id,))
            # runner.run_episode allocates a new UUID directory on each attempt.
            return dict(superseded=len(entries), requeued=len(requeue))



PROTOCOL_POLICIES = ('VRV', 'VRV-nocbr', 'VRV-samepage', 'VRV-modeltrigger', 'Oracle')
PROTOCOL_REASON = 'VRV/Oracle protocol fix'


def supersede_protocol(queue, name='m5-mitigation', root=ROOT, *,
                       policies=PROTOCOL_POLICIES, reason=PROTOCOL_REASON, hold_error=None):
    """Exclude completed held-policy episodes without stopping other policies.

    BEGIN IMMEDIATE serializes queue claims/finishes. Only held-policy running
    rows block this operation; the scheduler lock belongs to the live scheduler.
    Preserve the existing hold error and availability, never releasing the hold.
    """
    placeholders = ','.join('?' for _ in policies)
    scope = f"name=? AND policy IN ({placeholders}) AND status!='skipped'"
    params = (name, *policies)
    with queue.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        selected = db.execute(f'SELECT * FROM queue WHERE {scope}', params).fetchall()
        if not selected:
            raise ValueError('no held-policy queue rows found')
        if any(row['status'] == 'running' for row in selected):
            raise RuntimeError('held policies still running')
        hold_rows = [r for r in selected if r['status'] == 'deferred'
                     and (r['error'] == hold_error if hold_error is not None
                          else r['error'].startswith('held: ' + reason))]
        if not hold_rows:
            raise ValueError('protocol hold must already be installed')
        hold_error = hold_rows[0]['error']
        hold_until = max(r['available_at'] for r in hold_rows)
        if hold_until <= time.time():
            raise ValueError('hold availability has expired')
        db.execute('''CREATE TABLE IF NOT EXISTS superseded_episodes (
            episode_id TEXT PRIMARY KEY, queue_id INTEGER, reason TEXT, timestamp REAL)''')
        candidates = db.execute(f'''SELECT DISTINCT h.episode_id,h.queue_id,q.model FROM history h
            JOIN queue q ON q.id=h.queue_id WHERE {scope.replace('name', 'q.name').replace('policy', 'q.policy').replace('status', 'q.status')}
            AND h.episode_id IS NOT NULL''', params).fetchall()
        entries = []
        for row in candidates:
            episode = row['episode_id']
            if Path(episode).name != episode:
                raise ValueError('invalid episode ID')
            path = root / 'runs' / episode / 'record.json'
            if not path.exists():
                continue
            record = json.loads(path.read_text())
            if record.get('dry_run'):
                continue
            if record['episode'] != episode or record['policy'] not in policies:
                raise ValueError('episode/held-policy mismatch')
            entry = dict(episode_id=episode, queue_id=row['queue_id'], model=row['model'],
                         name=name, reason=reason, timestamp=time.time())
            existing = db.execute('SELECT reason FROM superseded_episodes WHERE episode_id=?', (episode,)).fetchone()
            if existing:
                continue
            db.execute('INSERT INTO superseded_episodes VALUES (?,?,?,?)',
                       (episode, row['queue_id'], reason, entry['timestamp']))
            entries.append(entry)
        # Append exclusion journal before changing queue rows, as in supersede().
        with (root / 'runs/superseded.jsonl').open('a+') as output:
            fcntl.flock(output, fcntl.LOCK_EX)
            output.seek(0)
            known = {json.loads(line)['episode_id'] for line in output if line.strip()}
            for entry in entries:
                if entry['episode_id'] not in known:
                    output.write(json.dumps(entry) + '\n')
                    known.add(entry['episode_id'])
            output.flush()
            os.fsync(output.fileno())
        db.execute(f"""UPDATE queue SET status='deferred',attempts=0,episode_id=NULL,worker=NULL,
            started_at=0,error=?,available_at=?,deferrals=0,retry_after=0 WHERE {scope}""",
            (hold_error, hold_until, *params))
        return dict(superseded=len(entries), episode_ids=[e['episode_id'] for e in entries],
                    held_counts={p: sum(r['policy'] == p for r in selected) for p in policies})

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--models', nargs='+', required=True)
    parser.add_argument('--before', type=float, required=True)
    args = parser.parse_args()
    print(json.dumps(supersede(Queue(args.db), args.name, set(args.models), args.before)))


if __name__ == '__main__':
    main()
