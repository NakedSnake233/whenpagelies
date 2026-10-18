"""Report m3-curation seeds without counting harness retries as agent outcomes."""
import argparse
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
MODELS = ('kimi-k2.6', 'qwen3.7-plus')


def report(db_path, root=ROOT, name='m3-curation'):
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM queue WHERE name=? AND policy='B0' AND condition='C0' "
                          "AND model IN (?,?) AND seed IN (0,1,2) ORDER BY task,model,seed",
                          (name, *MODELS)).fetchall()
        history = db.execute('SELECT h.* FROM history h JOIN queue q ON q.id=h.queue_id WHERE q.name=?',
                             (name,)).fetchall()
    tasks = {}
    for row in rows:
        task = json.loads((root / row['task']).read_text())
        result = tasks.setdefault(row['task'], dict(task=task['id'], family=task['family'],
                    clean={m: 0 for m in MODELS}, solves={m: 0 for m in MODELS}, harness_errors=0))
        path = root / 'runs' / str(row['episode_id']) / 'record.json'
        record = json.loads(path.read_text()) if path.exists() else {}
        if (row['status'] == 'done' and record and not record.get('error')
                and record.get('restore_ok') is True and record.get('G') is not None
                and not record.get('browser_killed') and not record.get('dry_run')):
            result['clean'][row['model']] += 1
            result['solves'][row['model']] += int(record['G'] is True and record.get('D') == 0)
    by_id = {r['id']: r for r in rows}
    seen_errors = set()
    for h in history:
        if h['queue_id'] in by_id and h['error']:
            # History is authoritative; count each attempt, not aggregate copies.
            key = (h['queue_id'], h['episode_id'], h['timestamp'])
            if key not in seen_errors:
                tasks[by_id[h['queue_id']]['task']]['harness_errors'] += 1
                seen_errors.add(key)
    for r in rows:
        if r['error'] and not any(h['queue_id'] == r['id'] and h['error'] == r['error'] for h in history):
            tasks[r['task']]['harness_errors'] += 1
    result = sorted(tasks.values(), key=lambda r: (r['task'].rsplit('-', 1)[0], int(r['task'].rsplit('-', 1)[1])))
    for r in result:
        # Require the qualifying model's three clean seeds. Missing seeds are
        # pending evidence, never failures and never an early KEEP decision.
        r['selection'] = ('KEEP' if any(r['clean'][m] == 3 and r['solves'][m] >= 2 for m in MODELS)
                          else 'DROP' if all(r['clean'][m] == 3 for m in MODELS) else 'PENDING')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'runs/queue.sqlite')
    parser.add_argument('--name', default='m3-curation')
    args = parser.parse_args()
    print('Solve = G=true and D=0, empty error and verified restore; latest completed clean seed only.')
    print('KEEP requires either model to solve >=2 of its 3 clean seeds; missing clean runs remain PENDING.')
    print('Harness errors count historical infrastructure/budget/429 attempts, not agent failures.\n')
    print('| Task | Family | Kimi solves/3 | Qwen solves/3 | Clean K/Q | Harness errors | Selection |')
    print('|---|---|---:|---:|---|---:|---|')
    results = report(args.db, name=args.name)
    for r in results:
        k, q = MODELS
        print(f"| {r['task']} | {r['family']} | {r['solves'][k]}/3 | {r['solves'][q]}/3 | "
              f"{r['clean'][k]}/3, {r['clean'][q]}/3 | {r['harness_errors']} | {r['selection']} |")
    print('\n' + ', '.join(f'{label}: {sum(r["selection"] == label for r in results)}'
                          for label in ('KEEP', 'DROP', 'PENDING')))


if __name__ == '__main__':
    main()
