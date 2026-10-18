"""Offline M5d regression tests: differential undo, FK safety, and evidence."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tools import pristine_state as reset
from tools.supersede_episodes import supersede_drift
from runner.sweep import Queue
from runner.run_episode import restore_verified


def test_diff_uses_recent_rows_and_does_not_emit_secrets():
    b = {'rows': {'users': [{'id': 13915, 'password': 'private', 'updated_at': '2026-10-11'}]},
         'keys': {'users': ['id']}}
    ds = reset.differences(b, {'users': [{'id': 13915, 'password': 'other', 'updated_at': '2026-10-12'}]})
    out = reset.summary(ds)
    assert out['drift'] == 1
    assert 'private' not in json.dumps(out) and 'other' not in json.dumps(out)
    deletes, updates = reset.row_operations('reddit', 'users', ds['users'], ['id'])
    assert not deletes and len(updates) == 1
    assert 'UPDATE' in updates[0] and 'DELETE' not in updates[0]


def test_global_reddit_restores_actor_rows_and_foreign_parent_counters():
    tables = {
        'users': [{'id': 13915, 'biography': 'seed'}],
        'submissions': [], 'comments': [], 'comment_votes': [], 'notifications': [],
        'submission_votes': [], 'forum_subscriptions': [],
        'submission_counters': [{'id': 20, 'net_score': 5, 'ranking': 4, 'comment_count': 2, 'last_active': 'old'}],
        'comment_counters': [{'id': 30, 'net_score': 7}],
    }
    b = {'site': 'reddit', 'rows': tables, 'keys': {t: ['id'] for t in tables}}
    live = {**tables, 'submission_votes': [{'id': 1, 'user_id': 13915, 'submission_id': 20}],
            'forum_subscriptions': [{'id': 'uuid', 'user_id': 13915, 'forum_id': 10}],
            'comment_votes': [{'id': 3, 'user_id': 13915, 'comment_id': 30}],
            'submission_counters': [{**tables['submission_counters'][0], 'net_score': 6}],
            'comment_counters': [{'id': 30, 'net_score': 8}]}
    ops = reset.operations(b, reset.differences(b, live))
    connection = sqlite3.connect(':memory:')
    connection.executescript('''PRAGMA foreign_keys=ON;
        CREATE TABLE users(id INTEGER PRIMARY KEY, biography TEXT);
        CREATE TABLE submissions(id INTEGER PRIMARY KEY,net_score INTEGER,ranking INTEGER,comment_count INTEGER,last_active TEXT);
        CREATE TABLE comments(id INTEGER PRIMARY KEY,net_score INTEGER);
        CREATE TABLE submission_votes(id INTEGER PRIMARY KEY,user_id INTEGER,submission_id INTEGER REFERENCES submissions(id));
        CREATE TABLE comment_votes(id INTEGER PRIMARY KEY,user_id INTEGER,comment_id INTEGER REFERENCES comments(id));
        CREATE TABLE forum_subscriptions(id TEXT PRIMARY KEY,user_id INTEGER,forum_id INTEGER);
        INSERT INTO submissions VALUES(20,6,4,2,'old');
        INSERT INTO comments VALUES(30,8);
        INSERT INTO submission_votes VALUES(1,13915,20);
        INSERT INTO comment_votes VALUES(3,13915,30);
        INSERT INTO forum_subscriptions VALUES('uuid',13915,10);
    ''')
    with connection:
        for sql in ops:
            connection.execute(sql)
    assert connection.execute('SELECT net_score FROM submissions').fetchone() == (5,)
    assert connection.execute('SELECT net_score FROM comments').fetchone() == (7,)
    for t in ('submission_votes', 'comment_votes', 'forum_subscriptions'):
        assert not connection.execute('SELECT * FROM ' + t).fetchall()
    assert reset.operations(b, reset.differences(b, tables)) == []


def test_comment_tree_cleanup_is_fk_ordered_and_missing_parents_insert_first():
    names = ['users', 'submissions', 'comments', 'comment_votes', 'notifications', 'submission_votes',
             'submission_counters', 'comment_counters']
    rows = {t: [] for t in names}
    rows['comments'] = [{'id': 2, 'parent_id': 9}, {'id': 9, 'parent_id': None}]
    b = {'site': 'reddit', 'rows': rows, 'keys': {t: ['id'] for t in rows}}
    live = {**rows, 'comments': [{'id': 12, 'parent_id': None}], 'submissions': [{'id': 99}]}
    ops = reset.operations(b, reset.differences(b, live))
    text = ';'.join(ops)
    assert 'WITH RECURSIVE' in text
    assert text.index('DELETE FROM notifications') < text.index('DELETE FROM comment_votes') < text.index('DELETE FROM comments')
    assert text.index('INSERT INTO "comments" ("id","parent_id") VALUES (9,NULL)') < text.index('VALUES (2,9)')
    assert 'FOREIGN_KEY_CHECKS' not in text


def test_magento_bulk_no_timestamp_change_missing_attribute_and_review():
    rows = {'catalog_product_entity': [{'entity_id': '63', 'updated_at': 'old'}],
            'catalog_product_entity_int': [{'value_id': '1', 'entity_id': '63', 'store_id': '0', 'value': '1'},
                                           {'value_id': '2', 'entity_id': '64', 'store_id': '0', 'value': '1'}],
            'cataloginventory_stock_item': [],
            'review': [{'review_id': '8', 'status_id': '1'}],
            'review_detail': [{'detail_id': '9', 'review_id': '8', 'detail': 'original'}]}
    keys = {t: ['entity_id' if t == 'catalog_product_entity' else 'value_id' if t.endswith('_int')
                 else 'review_id' if t == 'review' else 'detail_id' if t == 'review_detail' else 'item_id'] for t in rows}
    live = {**rows, 'catalog_product_entity_int': [{**rows['catalog_product_entity_int'][0], 'value': '2'}],
            'review': [{'review_id': '8', 'status_id': '2'}],
            'review_detail': [{'detail_id': '9', 'review_id': '8', 'detail': 'modified'}]}
    b = {'site': 'shopping_admin', 'rows': rows, 'keys': keys}
    ops = reset.operations(b, reset.differences(b, live))
    assert len(ops) == 4
    assert any('INSERT INTO `catalog_product_entity_int`' in s for s in ops)
    assert not any('DELETE FROM' in s for s in ops)
    assert any('UPDATE `review`' in s for s in ops)
    assert not any('UPDATE `catalog_product_entity` ' in s for s in ops)


def test_write_targets_repeated_selected_and_mass_attribute_multistep(tmp_path):
    requests = [
        {'method': 'POST', 'url': 'http://localhost/admin/catalog/product/massStatus/',
         'postData': {'text': 'form_key=private&selected%5B%5D=63&selected%5B%5D=64'}},
        {'method': 'POST', 'url': 'http://localhost/admin/catalog/product_action_attribute/edit/',
         'postData': {'params': [{'name': 'selected[0]', 'value': '65'}, {'name': 'selected[1]', 'value': '66'}]}},
        {'method': 'POST', 'url': 'http://localhost/admin/catalog/product/save/id/70/', 'postData': {}},
        {'method': 'GET', 'url': 'http://localhost/admin/catalog/product/edit/id/99/'},
    ]
    (tmp_path / 'network.har').write_text(json.dumps({'log': {'entries': [{'request': r} for r in requests]}}))
    assert reset.write_targets(tmp_path) == {63, 64, 65, 66, 70}


@pytest.mark.parametrize('task', [{'site': 'reddit'}, {'sites': ['reddit']}])
def test_restore_integration_order_and_global_mismatch_propagation(tmp_path, monkeypatch, task):
    calls = []
    b = {'site': 'reddit', 'rows': {}, 'episode_started_at': 42}
    module = SimpleNamespace(restore=lambda *a: calls.append('scoped'), baseline=lambda *a: b)
    def global_reset(site, directory, start):
        calls.append('global')
        assert site == 'reddit' and start == 42 and directory == tmp_path
        raise RuntimeError('global reset drift')
    monkeypatch.setattr(reset, 'reset', global_reset)
    with pytest.raises(RuntimeError, match='global reset drift'):
        restore_verified(module, task, b, tmp_path)
    assert calls == ['scoped', 'global']
    assert 'global reset drift' in (tmp_path / 'restore_error.log').read_text()


def test_checksum_failure_precedes_any_container_access(tmp_path, monkeypatch):
    path = tmp_path / 'snapshot.json'; path.write_text('{}')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'sites': {'reddit': {'runtime_path': str(path), 'runtime_sha256': 'bad'}}}))
    monkeypatch.setattr(reset, 'MANIFEST', manifest)
    monkeypatch.setattr(reset.audit, 'command', lambda *a: pytest.fail('must not contact container'))
    with pytest.raises(RuntimeError, match='checksum'):
        reset.load('reddit')


def test_requeue_exact_ids_deduplicates_plans_and_preserves_records(tmp_path):
    q = Queue(tmp_path / 'queue.sqlite')
    q.add('main', ['tasks/reddit_399.json'], ['deepseek-v4.1-flash'], ['B0'], ['C0'], [0])
    ids = ['old-a', 'old-b']
    with q.connect() as db:
        row = dict(db.execute('SELECT * FROM queue').fetchone())
        for episode in ids:
            path = tmp_path / 'runs' / episode / 'record.json'; path.parent.mkdir(parents=True)
            path.write_text(json.dumps({**row, 'episode': episode, 'dry_run': False}))
            db.execute('INSERT INTO history VALUES (?,?,?,?)', (row['id'], episode, '', 1))
        db.execute("UPDATE queue SET status='done',episode_id='old-b',attempts=2")
    result = supersede_drift(q, 'main', ids, tmp_path)
    assert (result['superseded'], result['requeued']) == (2, 1)
    assert result['by_model_condition'][0]['episodes'] == 2
    assert result['by_model_condition'][0]['plans'] == 1
    with q.connect() as db:
        row = db.execute('SELECT * FROM queue').fetchone()
        assert row['status'] == 'pending' and row['episode_id'] is None and row['attempts'] == 0
    assert supersede_drift(q, 'main', ids, tmp_path)['requeued'] == 0
    assert len((tmp_path / 'runs/superseded.jsonl').read_text().splitlines()) == 2
    assert all((tmp_path / 'runs' / episode / 'record.json').exists() for episode in ids)


def test_transaction_and_error_body_redaction(monkeypatch):
    calls = []
    monkeypatch.setattr(reset.audit, 'command', lambda args, payload: calls.append((args, payload)))
    reset.execute('reddit', ['UPDATE users SET biography=NULL WHERE id=13915'])
    assert calls[0][1].startswith('BEGIN;') and calls[0][1].endswith('COMMIT;')
    reset.execute('shopping_admin', ['UPDATE review SET status_id=1 WHERE review_id=8'])
    assert '$db->beginTransaction()' in calls[1][0][-1]
    assert '$db->commit()' in calls[1][0][-1]


def test_standalone_seed_replica_gets_an_exact_new_plan(tmp_path):
    q = Queue(tmp_path / 'queue.sqlite')
    directory = tmp_path / 'runs' / 'standalone'; directory.mkdir(parents=True)
    record = dict(episode='standalone', task='reddit-406', site='reddit', model='qwen3.7-plus',
                  policy='B0', condition='C0', seed=2, dry_run=False)
    (directory / 'record.json').write_text(json.dumps(record))
    assert supersede_drift(q, 'main', ['standalone'], tmp_path)['requeued'] == 1
    with q.connect() as db:
        row = dict(db.execute('SELECT * FROM queue').fetchone())
        assert row['task'] == 'tasks/reddit_406.json' and row['seed'] == 2
        assert row['status'] == 'pending' and row['episode_id'] is None
    assert supersede_drift(q, 'main', ['standalone'], tmp_path)['requeued'] == 0
