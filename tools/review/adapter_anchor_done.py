"""Read-only anchor-step audit; output contains aggregate counts only."""
import gzip
import importlib.util
import json
from pathlib import Path
import sqlite3

FINAL = Path('analysis/review/proprietary')
spec = importlib.util.spec_from_file_location('adapter_audit', 'tools/review/adapter_audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
with gzip.open(Path('data/episodes_proprietary.jsonl.gz'), 'rt') as source:
    rows = [json.loads(line) for line in source if line.strip()]
details = json.loads((FINAL / 'adapter_audit_details.json').read_text())
for condition in ('F1', 'F3'):
    cell = [d for d in details if d['condition'] == condition]
    assert len(cell) == sum(r['condition'] == condition and bool(r['exposed']) for r in rows)
    count = 0
    for d in cell:
        assert d['anchor'] == 'action interval + matching mutating request'
        path = audit.RUNS / d['episode'] / 'trace.jsonl'
        blob, _ = audit.stable_read(path)
        trace = [json.loads(line) for line in blob.splitlines() if line.strip()]
        anchors = [s for s in trace if s['step'] == d['start_step']]
        assert len(anchors) == 1
        step = anchors[0]
        if step.get('lenient_parse') == 'first_of_many':
            actions = audit.calls(step['raw_model_text'])
            assert len(actions) == step['n_actions_in_reply']
            count += any(audit.done(a) for a in actions[1:])
    print(f'{condition}: anchor-step discarded DONE = {count}/{len(cell)} exposed episodes')
steps = sum(r['steps'] for r in rows)
adapted = sum(r['n_adapted_steps'] for r in rows)
print(f'Total steps: {steps}')
print(f'Total n_adapted_steps: {adapted}')
print(f'Adapted share: {adapted}/{steps} = {adapted/steps!r} = {100*adapted/steps!r}%')
print(f'Total n_parse_error_steps: {sum(r["n_parse_error_steps"] for r in rows)}')
print(f'Exported episode cost (255/256): {sum(r["cost"] for r in rows)!r}')
with sqlite3.connect('file:runs/budget.sqlite?mode=ro', uri=True) as db:
    db.execute('PRAGMA query_only=ON')
    count, cost = db.execute('SELECT COUNT(*), SUM(cost) FROM calls WHERE model = ?', ('gpt-5.6-luna',)).fetchone()
    print(f'Luna budget total (all calls, including smoke tests and failed attempts): {cost!r}; calls: {count}')
