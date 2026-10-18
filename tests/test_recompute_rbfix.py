"""Offline repair tests: field scope, backups, publication, and idle guards."""
import json
from pathlib import Path
import shutil

from tools import recompute_rbfix as repair


def fixture(tmp_path):
    for name in ('tasks', 'catalog', 'runs', 'logs'):
        (tmp_path / name).mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'catalog/reddit.yaml',
                    tmp_path / 'catalog/reddit.yaml')
    shutil.copytree(Path(__file__).resolve().parents[1] / 'catalog/templates',
                    tmp_path / 'catalog/templates')
    task = dict(id=1, family='fam-12', site='reddit', start_url='http://local/')
    (tmp_path / 'tasks/reddit_1.json').write_text(json.dumps(task))
    (tmp_path / 'runs/records.jsonl').touch()
    return tmp_path


def episode(root, name):
    directory = root / 'runs' / name
    directory.mkdir()
    row = dict(episode=name, task=1, family='fam-12', condition='F4',
               final_message_at=5, readback_agent=True, n_readbacks_agent=2,
               readback_after_write=True, n_readbacks=3, restore_ok=True,
               arbitrary={'preserve': [1, 2]})
    (directory / 'record.json').write_text(json.dumps(row, indent=2))
    (directory / 'trace.jsonl').write_text('{}\n')
    (directory / 'proxy.jsonl').write_text('{}\n')
    with (root / 'runs/records.jsonl').open('a') as out:
        out.write(json.dumps(row)+'\n')
    return row


def test_repair_only_two_fields_preserves_backup_and_worker_append(tmp_path, monkeypatch):
    root = fixture(tmp_path)
    original = episode(root, 'closed')
    backup_bytes = (root / 'runs/closed/record.json').read_bytes()
    worker_row = dict(episode='concurrent', keep='worker')
    def evidence(*args):
        with (root / 'runs/records.jsonl').open('a') as out:
            out.write(json.dumps(worker_row)+'\n')
        return dict(readback_agent=False, n_readbacks_agent=0, readback_any=False)
    monkeypatch.setattr(repair, 'evidence', evidence)
    inode = (root / 'runs/records.jsonl').stat().st_ino
    result = repair.recompute(root, idle_seconds=0)
    updated = json.loads((root / 'runs/closed/record.json').read_bytes())
    assert {k for k in original if original[k] != updated[k]} == set(repair.KEYS)
    assert updated == original | dict(readback_agent=False, n_readbacks_agent=0)
    assert (root / 'runs/backup_rbfix/closed/record.json').read_bytes() == backup_bytes
    rows = [json.loads(line) for line in (root / 'runs/records.jsonl').read_text().splitlines()]
    assert rows == [original, worker_row, updated]
    assert (root / 'runs/records.jsonl').stat().st_ino == inode
    assert result['changed'] == 1 and result['flipped_by_condition'] == {'F4': 1}
    changes = [json.loads(line) for line in (root / 'logs/rbfix_changes.jsonl').read_text().splitlines()]
    assert changes[0]['episode'] == 'closed'
    assert changes[0]['old_readback_agent'] and not changes[0]['new_readback_agent']
    assert repair.recompute(root, idle_seconds=0)['changed'] == 0
    assert (root / 'runs/backup_rbfix/closed/record.json').read_bytes() == backup_bytes


def test_recent_active_torn_and_unpublished_are_skipped(tmp_path, monkeypatch):
    root = fixture(tmp_path)
    for name in ('recent', 'active', 'torn', 'unpublished'):
        episode(root, name)
    marker = dict(baseline_path=str(root / 'runs/active/baseline.json'))
    (root / 'runs/active_reddit.json').write_text(json.dumps(marker))
    (root / 'runs/torn/trace.jsonl').write_text('{}')
    (root / 'runs/unpublished/record.json').write_text('{}')
    def forbidden(*args):
        raise AssertionError('must not recompute unsafe episodes')
    monkeypatch.setattr(repair, 'evidence', forbidden)
    assert repair.recompute(root)['changed'] == 0
    # At zero idle interval, explicitly inspect each non-age guard.
    assert repair.closed_manifest(root, root / 'runs/active', 0) is None
    assert repair.closed_manifest(root, root / 'runs/torn', 0) is None
    assert not (root / 'runs/backup_rbfix').exists()


def test_artifact_changed_during_evidence_is_skipped(tmp_path, monkeypatch):
    root = fixture(tmp_path)
    original = episode(root, 'closed')
    def evidence(*args):
        (root / 'runs/closed/new-artifact').write_text('change')
        return dict(readback_agent=False, n_readbacks_agent=0)
    monkeypatch.setattr(repair, 'evidence', evidence)
    result = repair.recompute(root, idle_seconds=0)
    assert result['changed'] == 0
    assert result['skipped']['changed_during_recompute'] == 1
    assert json.loads((root / 'runs/closed/record.json').read_text()) == original
