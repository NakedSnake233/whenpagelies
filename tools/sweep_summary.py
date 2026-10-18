"""Compact per-model summary: python -m tools.sweep_summary [queue_db] [name]."""
import collections
import json
from pathlib import Path
import sqlite3
import statistics
import sys

from runner.superseded import superseded_ids

ROOT = Path(__file__).resolve().parents[1]


def records_for_sweep(root, db, name):
    with sqlite3.connect(root / db) as connection:
        episodes = {r[0] for r in connection.execute(
            'SELECT episode_id FROM queue WHERE name=? AND episode_id IS NOT NULL', (name,))}
    episodes -= superseded_ids(root)
    with (root / 'runs/records.jsonl').open() as source:
        return [record for line in source if line.strip()
                for record in [json.loads(line)] if record.get('episode') in episodes]


def main():
    db = Path(sys.argv[1] if len(sys.argv) > 1 else 'runs/m5_queue.sqlite')
    name = sys.argv[2] if len(sys.argv) > 2 else 'm5-main'
    records = records_for_sweep(ROOT, db, name)
    by_model = collections.defaultdict(list)
    for record in records:
        by_model[record['model']].append(record)
    for model, rows in sorted(by_model.items()):
        claims = collections.Counter(r.get('claim') for r in rows)
        c0 = [r for r in rows if r.get('condition') == 'C0' and not r.get('error')]
        harness_errors = sum(bool(r.get('error')) for r in rows)
        steps = action_errors = 0
        for record in rows:
            trace = ROOT / 'runs' / record['episode'] / 'trace.jsonl'
            if trace.exists():
                for line in trace.read_text().splitlines():
                    if line.strip():
                        row = json.loads(line)
                        steps += 1
                        action_errors += bool(row.get('error') or row.get('action_error'))
        def mean(key):
            return round(statistics.mean(r.get(key) or 0 for r in rows), 3)
        print(f"{model}: n={len(rows)} claims={dict(claims)} "
              f"C0_G={sum(bool(r.get('G')) for r in c0)}/{len(c0)} "
              f"steps={mean('steps')} usd={mean('cost')} sec={mean('seconds')} "
              f"harness_err={harness_errors} action_err_rate={action_errors}/{steps}")
    print('conditions done:', dict(collections.Counter(r.get('condition') for r in records)))


if __name__ == '__main__':
    main()
