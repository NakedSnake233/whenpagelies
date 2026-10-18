"""Repair only agent read-back fields in closed, idle published episodes.

No queue access. record.json is the runner's completion publication; active
markers, incomplete traces, recent or changing directory artifacts are skipped.
Re-publication appends under the runner's existing aggregate lock, preserving
its inode and concurrent worker appends. Every record is atomically replaced.
"""
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import time

from proxy.catalog import load_catalog
from runner.metrics import evidence
from runner.storage import write_json
from tools.analyze import ROOT, json_lines, manifest

KEYS = ('readback_agent', 'n_readbacks_agent')


def active_ids(root):
    active = set()
    for path in (root / 'runs').glob('active_*.json'):
        try:
            marker = json.loads(path.read_bytes())
        except FileNotFoundError:
            continue
        except ValueError:
            # Fail closed if an active marker is unreadable.
            return None
        if marker.get('baseline_path'):
            active.add(Path(marker['baseline_path']).parent.name)
    return active


def closed_manifest(root, directory, idle_seconds):
    active = active_ids(root)
    if active is None or directory.name in active:
        return None
    try:
        state = manifest(directory)
        cutoff = time.time_ns() - int(idle_seconds * 1e9)
        dirs = [directory, *(p for p in directory.rglob('*') if p.is_dir())]
        if any(max(p.stat().st_mtime_ns, p.stat().st_ctime_ns) > cutoff for p in dirs):
            return None
        if ('record.json' not in state or 'trace.jsonl' not in state
                or any(max(s[2], s[3]) > cutoff for s in state.values())):
            return None
        _, partial = json_lines((directory / 'trace.jsonl').read_bytes())
        if partial:
            return None
        return state
    except FileNotFoundError:
        return None


def recompute(root=ROOT, idle_seconds=600):
    root = Path(root)
    tasks = {}
    for path in (root / 'tasks').glob('*_*.json'):
        if path.name == 'webarena_candidates.json':
            continue
        task = json.loads(path.read_bytes())
        if isinstance(task, dict) and 'id' in task:
            tasks[task['id']] = task
    catalogs = {site: {e['id']: e for e in load_catalog(root / f'catalog/{site}.yaml').entries}
                for site in {t['site'] for t in tasks.values()}}
    aggregate = root / 'runs/records.jsonl'
    with aggregate.open('rb') as source:
        published, partial = json_lines(source.read())
    latest = {r['episode']: r for r in published}
    skipped, changes = Counter(), []
    for path in sorted((root / 'runs').glob('*/record.json')):
        before = closed_manifest(root, path.parent, idle_seconds)
        if before is None:
            skipped['active_recent_or_incomplete'] += 1
            continue
        raw = path.read_bytes()
        record = json.loads(raw)
        episode = record.get('episode')
        if episode != path.parent.name or latest.get(episode) != record:
            skipped['unpublished_or_mismatching'] += 1
            continue
        task = tasks.get(record.get('task'))
        entry = catalogs.get((task or {}).get('site'), {}).get(record.get('family'))
        if task is None or entry is None or not (path.parent / 'proxy.jsonl').exists():
            skipped['unavailable'] += 1
            continue
        values = evidence(path.parent, task, entry, record.get('final_message_at'), record['condition'])
        updated = dict(record, **{k: values[k] for k in KEYS})
        if updated == record:
            skipped['unchanged'] += 1
            continue
        # Keep the worker's append inode; never rename or truncate the aggregate.
        with aggregate.open('a+b') as output:
            fcntl.flock(output, fcntl.LOCK_EX)
            output.seek(0)
            current_rows, torn = json_lines(output.read())
            current = {r['episode']: r for r in current_rows}
            if torn or current.get(episode) != record:
                skipped['aggregate_changed_or_torn'] += 1
                continue
            if closed_manifest(root, path.parent, idle_seconds) != before or path.read_bytes() != raw:
                skipped['changed_during_recompute'] += 1
                continue
            backup = root / 'runs/backup_rbfix' / episode / 'record.json'
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                if backup.read_bytes() != raw:
                    raise RuntimeError('existing backup differs; refusing overwrite')
            else:
                with backup.open('xb') as saved:
                    saved.write(raw)
                    saved.flush()
                    os.fsync(saved.fileno())
            write_json(path, updated)
            output.seek(0, os.SEEK_END)
            output.write((json.dumps(updated, ensure_ascii=False)+'\n').encode())
            output.flush()
            os.fsync(output.fileno())
            change = dict(episode=episode, condition=record['condition'], family=record['family'],
                          old_readback_agent=record.get('readback_agent'),
                          new_readback_agent=updated['readback_agent'],
                          old_n_readbacks_agent=record.get('n_readbacks_agent'),
                          new_n_readbacks_agent=updated['n_readbacks_agent'])
            changes.append(change)
            with (root / 'logs/rbfix_changes.jsonl').open('a') as journal:
                journal.write(json.dumps(change)+'\n')
                journal.flush()
                os.fsync(journal.fileno())
    summary = dict(changed=len(changes), flipped_by_condition=dict(Counter(
        c['condition'] for c in changes if c['old_readback_agent'] != c['new_readback_agent'])),
        skipped=dict(skipped), aggregate_partial_at_start=partial,
        idle_seconds=idle_seconds)
    write_json(root / 'logs/rbfix_summary.json', summary)
    return summary


if __name__ == '__main__':
    print(json.dumps(recompute()))
