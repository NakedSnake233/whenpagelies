"""Recompute readback evidence for all published records, including a live sweep.

Run: .venv/bin/python -m tools.recompute_metrics
Original records are preserved once. The aggregate's inode and append lock are
retained so running workers cannot lose an append during migration.
"""
import fcntl
import json
import os

from agent.browser import ROOT
from proxy.catalog import load_catalog
from runner.metrics import evidence
from runner.run_episode import load_task
from runner.storage import write_json
from runner.superseded import superseded_ids


def recompute(root=ROOT):
    (root / 'logs').mkdir(exist_ok=True)
    tasks = {t['id']: t for p in (root / 'tasks').glob('*_*.json')
             if p.name != 'webarena_candidates.json' for t in [load_task(p)]}
    catalogs = {site: {e['id']: e for e in load_catalog(root / f'catalog/{site}.yaml').entries}
                for site in {t['site'] for t in tasks.values()}}
    updates, unavailable = {}, []
    excluded = superseded_ids(root)
    paths = sorted((root / 'runs').glob('*/record.json'))
    for path in paths:
        record = json.loads(path.read_text())
        if record['episode'] in excluded:
            continue
        task = tasks.get(record.get('task'))
        family = record.get('family') or (task or {}).get('family')
        if task is None or family not in catalogs.get(task['site'], {}):
            # Pre-M3 fixture records have no resolvable backend family. Retain
            # their old metric, and explicitly mark unavailable attribution.
            updated = dict(record, readback_any=record.get('readback_after_write', False),
                           readback_after_write=record.get('readback_after_write', False),
                           n_readbacks=record.get('n_readbacks', 0),
                           readback_agent=False, n_readbacks_agent=0,
                           readback_provenance='unavailable: no resolvable task/family')
            unavailable.append(record['episode'])
        else:
            metrics = evidence(path.parent, task, catalogs[task['site']][family],
                               record.get('final_message_at'), record['condition'])
            keys = ('readback_after_write', 'readback_any', 'readback_agent', 'n_readbacks', 'n_readbacks_agent')
            updated = dict(record, **{k: metrics[k] for k in keys},
                           readback_provenance='trace action intervals + HAR document/redirect evidence + proxy request timestamps; legacy traces use step start')
            if not (path.parent / 'proxy.jsonl').exists():
                updated['readback_provenance'] = 'unavailable: no proxy flow log / matched-write evidence'
                unavailable.append(record['episode'])
        if updated != record:
            backup = path.with_name('record.before_m3b_metrics.json')
            if not backup.exists():
                write_json(backup, record)
            write_json(path, updated)
        updates[record['episode']] = updated
    aggregate_path = root / 'runs/records.jsonl'
    if aggregate_path.exists():
        with aggregate_path.open('r+') as output:
            fcntl.flock(output, fcntl.LOCK_EX)
            records = [json.loads(line) for line in output if line.strip()]
            output.seek(0)
            for record in records:
                # Preserve concurrent appends not present in our initial scan.
                output.write(json.dumps(updates.get(record['episode'], record), ensure_ascii=False) + '\n')
            output.truncate()
            output.flush()
            os.fsync(output.fileno())
    summary = dict(records=len(updates), readback_any=sum(r['readback_any'] for r in updates.values()),
                   readback_agent=sum(r['readback_agent'] for r in updates.values()), unavailable=unavailable)
    write_json(root / 'logs/m3b_metrics.json', summary)
    return summary


if __name__ == '__main__':
    print(json.dumps(recompute()))
