import json
import time

import pytest

from runner.sweep import Queue
from runner.superseded import superseded_ids
from tools.supersede_episodes import supersede
from tools.sweep_summary import records_for_sweep


def fixture_queue(tmp_path):
    runs = tmp_path / 'runs'
    runs.mkdir()
    queue = Queue(runs / 'queue.sqlite')
    with queue.connect() as db:
        db.execute("""INSERT INTO queue (id,name,task,site,model,policy,condition,seed,status,attempts,episode_id)
            VALUES (1,'m5-main','tasks/example.json','reddit','mimo-v2.6-flash','B0','C0',0,'done',1,'old')""")
        db.execute('INSERT INTO history VALUES (1,?,?,?)', ('old', '', 10))
    old = runs / 'old'
    old.mkdir()
    record = dict(episode='old', model='mimo-v2.6-flash', task='unknown')
    (old / 'record.json').write_text(json.dumps(record))
    (runs / 'records.jsonl').write_text(json.dumps(record) + '\n')
    return queue


def test_supersede_preserves_artifacts_and_plans_and_is_idempotent(tmp_path):
    queue = fixture_queue(tmp_path)
    record = tmp_path / 'runs/old/record.json'
    original = record.read_bytes()
    assert supersede(queue, 'm5-main', {'mimo-v2.6-flash'}, 20, tmp_path) == dict(superseded=1, requeued=1)
    assert superseded_ids(tmp_path) == {'old'}
    assert record.read_bytes() == original
    with queue.connect() as db:
        row = dict(db.execute('SELECT * FROM queue').fetchone())
        assert (row['status'], row['attempts'], row['episode_id']) == ('pending', 0, None)
        assert (row['task'], row['condition'], row['seed']) == ('tasks/example.json', 'C0', 0)
        assert db.execute('SELECT COUNT(*) FROM history').fetchone()[0] == 1
        db.execute("UPDATE queue SET status='done',episode_id='new'")
        db.execute('INSERT INTO history VALUES (1,?,?,?)', ('new', '', time.time()))
    assert supersede(queue, 'm5-main', {'mimo-v2.6-flash'}, 20, tmp_path) == dict(superseded=0, requeued=0)
    with queue.connect() as db:
        assert db.execute('SELECT episode_id FROM queue').fetchone()[0] == 'new'


def test_supersede_requires_stopped_scheduler(tmp_path):
    queue = fixture_queue(tmp_path)
    with queue.connect() as db:
        db.execute("UPDATE queue SET status='running'")
    with pytest.raises(RuntimeError, match='stop and recover'):
        supersede(queue, 'm5-main', {'mimo-v2.6-flash'}, 20, tmp_path)


def test_summary_excludes_even_if_queue_still_references_superseded(tmp_path):
    fixture_queue(tmp_path)
    assert len(records_for_sweep(tmp_path, 'runs/queue.sqlite', 'm5-main')) == 1
    (tmp_path / 'runs/superseded.jsonl').write_text('{"episode_id":"old","reason":"test"}\n')
    assert records_for_sweep(tmp_path, 'runs/queue.sqlite', 'm5-main') == []


def test_recompute_skips_superseded_files_and_totals(tmp_path):
    from tools.recompute_metrics import recompute
    fixture_queue(tmp_path)
    record = tmp_path / 'runs/old/record.json'
    original = record.read_bytes()
    (tmp_path / 'runs/superseded.jsonl').write_text('{"episode_id":"old","reason":"test"}\n')
    assert recompute(tmp_path)['records'] == 0
    assert record.read_bytes() == original
    assert not record.with_name('record.before_m3b_metrics.json').exists()
    assert json.loads((tmp_path / 'runs/records.jsonl').read_text())['episode'] == 'old'


@pytest.mark.parametrize('name,models,cutoff', [
    ('another-sweep', {'mimo-v2.6-flash'}, 20),
    ('m5-main', {'minimax-m3'}, 20),
    ('m5-main', {'mimo-v2.6-flash'}, 5),
])
def test_supersede_respects_scope_and_cutoff(tmp_path, name, models, cutoff):
    queue = fixture_queue(tmp_path)
    assert supersede(queue, name, models, cutoff, tmp_path) == dict(superseded=0, requeued=0)
    with queue.connect() as db:
        assert db.execute('SELECT episode_id FROM queue').fetchone()[0] == 'old'


def test_protocol_supersede_preserves_live_baselines_and_hold(tmp_path):
    from tools.supersede_episodes import supersede_protocol, PROTOCOL_REASON
    queue = fixture_queue(tmp_path)
    hold = 'held: ' + PROTOCOL_REASON + ' (audit)'
    with queue.connect() as db:
        db.execute("UPDATE queue SET name='m5-mitigation',policy='VRV',status='done'")
        db.execute("INSERT INTO queue (name,task,site,model,policy,condition,seed,status,error,available_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   ('m5-mitigation', 'hold.json', 'reddit', 'm', 'VRV', 'C0', 0, 'deferred', hold, time.time()+86400))
        db.execute("INSERT INTO queue (name,task,site,model,policy,condition,seed,status) VALUES (?,?,?,?,?,?,?,?)",
                   ('m5-mitigation', 'live.json', 'reddit', 'm', 'B1', 'C0', 0, 'running'))
        db.execute("INSERT INTO queue (name,task,site,model,policy,condition,seed,status) VALUES (?,?,?,?,?,?,?,?)",
                   ('m5-mitigation', 'skip.json', 'reddit', 'm', 'VRV', 'C0', 0, 'skipped'))
        baseline = dict(db.execute("SELECT * FROM queue WHERE policy='B1'").fetchone())
    path = tmp_path / 'runs/old/record.json'
    record = json.loads(path.read_text()); record.update(policy='VRV', error='')
    path.write_text(json.dumps(record))
    result = supersede_protocol(queue, root=tmp_path)
    assert result['episode_ids'] == ['old']
    assert result['held_counts']['VRV'] == 2
    with queue.connect() as db:
        assert dict(db.execute("SELECT * FROM queue WHERE policy='B1'").fetchone()) == baseline
        assert db.execute("SELECT status FROM queue WHERE task='skip.json'").fetchone()[0] == 'skipped'
        assert all(r['status'] == 'deferred' and r['error'] == hold for r in db.execute("SELECT * FROM queue WHERE policy='VRV' AND status!='skipped'"))
    assert supersede_protocol(queue, root=tmp_path)['superseded'] == 0
    with queue.connect() as db:
        db.execute("UPDATE queue SET status='running' WHERE id=1")
    with pytest.raises(RuntimeError, match='held policies still running'):
        supersede_protocol(queue, root=tmp_path)
