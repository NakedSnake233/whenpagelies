"""Correct pre-dispatch-fix M3 metrics from retained immutable action checkpoints.

Run after the pilot scheduler exits and before launching curation. Original
record.json files are retained as record.before_final_dispatch_fix.json.
"""
import fcntl
import json
import os
from agent.agent import final_message
from agent.browser import ROOT
from proxy.catalog import load_catalog
from runner.metrics import evidence, rows
from runner.run_episode import load_task
from runner.storage import write_json
from runner.sweep import Queue


def correct_record(record, directory, task, entry):
    checkpoint = directory / 'current_step.json'
    if not record.get('final_message_at') or not checkpoint.exists():
        return record
    last = json.loads(checkpoint.read_text())
    trace = rows(directory / 'trace.jsonl')
    assert trace and last['step'] == trace[-1]['step'] and last['action'] == trace[-1]['action']
    assert final_message(last['action']) == record['final_message'] and not trace[-1]['error']
    dispatch = last['started_at']
    if record['final_message_at'] == dispatch:
        return record
    updated = dict(record, final_message_observed_at=record['final_message_at'], final_message_at=dispatch,
                   timing_provenance='current_step.json final-action dispatch; recomputed by tools/m3_recompute_timing.py')
    updated.update(evidence(directory, task, entry, dispatch, record['condition']))
    return updated


def main():
    queue = Queue()
    with __import__('pathlib').Path(queue.path + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with queue.connect() as db:
            selected = [dict(r) for r in db.execute("SELECT * FROM queue WHERE name='m3-pilot-accepted'")]
        assert len(selected) == 43 and all(r['status']=='done' for r in selected)
        updates = {}
        changed_metrics = []
        for row in selected:
            directory = ROOT / 'runs' / row['episode_id']
            original = json.loads((directory / 'record.json').read_text())
            task = load_task(ROOT / row['task'])
            entry = next(e for e in load_catalog(ROOT / f"catalog/{task['site']}.yaml").entries if e['id']==task['family'])
            updated = correct_record(original, directory, task, entry)
            if updated != original:
                backup = directory / 'record.before_final_dispatch_fix.json'
                if not backup.exists():
                    write_json(backup, original)
                write_json(directory / 'record.json', updated)
                updates[row['episode_id']] = updated
                changed = {k: [original[k],updated[k]] for k in ('rollback_before_claim','readback_after_write','n_readbacks') if original[k]!=updated[k]}
                if changed:
                    changed_metrics.append({'episode': row['episode_id'], 'changes': changed})
        with (ROOT / 'runs/records.jsonl').open('r+') as aggregate:
            fcntl.flock(aggregate, fcntl.LOCK_EX)
            records = [json.loads(line) for line in aggregate if line.strip()]
            aggregate.seek(0)
            aggregate.writelines(json.dumps(updates.get(r['episode'],r),ensure_ascii=False)+'\n' for r in records)
            aggregate.truncate()
            aggregate.flush()
            os.fsync(aggregate.fileno())
        summary = dict(corrected_records=len(updates), changed_metrics=changed_metrics)
        write_json(ROOT / 'logs/m3_timing_correction.json',summary)
        print(json.dumps(summary))


if __name__ == '__main__':
    main()
