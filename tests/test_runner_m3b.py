import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from runner.metrics import evidence
from runner.run_episode import POLICIES, oracle_hook, parser
from runner.sweep import Queue


def lines(path, rows):
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


def test_oracle_is_live_and_exposes_only_goal_and_effect():
    calls = []
    def evaluate(task, baseline):
        calls.append((task, baseline))
        return dict(G=len(calls) > 1, D=len(calls), details='private')
    module = SimpleNamespace(evaluate=evaluate)
    assert oracle_hook('B0', module, 'task', 'baseline') is None
    hook = oracle_hook('Oracle', module, 'task', 'baseline')
    assert hook() == dict(G=False, D=1)
    assert hook() == dict(G=True, D=2)
    assert calls == [('task', 'baseline')] * 2


def test_all_policies_and_add_rejects_unknown_atomically(tmp_path):
    queue = Queue(tmp_path / 'queue.sqlite')
    args = ('test', ['tasks/reddit_400.json'], ['deepseek-v4.1-flash'])
    with pytest.raises(ValueError, match='unknown policies'):
        queue.add(*args, ['B0', 'typo'], ['C0'], [0])
    with queue.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM queue').fetchone()[0] == 0
    assert queue.add(*args, POLICIES, ['C0'], [0]) == 8
    for policy in POLICIES:
        assert parser().parse_args(['--task', 'tasks/reddit_400.json', '--policy', policy]).policy == policy


def test_attached_worker_filters_site(tmp_path):
    q = Queue(tmp_path / 'q.sqlite')
    q.add('batch', ['tasks/reddit_400.json', 'tasks/shopping_admin_453.json'],
          ['deepseek-v4.1-flash'], ['B0'], ['C0'], [0])
    assert q.claim('batch', 6, 'shopping_admin')['site'] == 'shopping_admin'
    assert q.claim('batch', 6, 'shopping_admin') is None
    assert q.claim('batch', 0)['site'] == 'reddit'


@pytest.mark.parametrize('kind,expected', [
    ('automatic', False), ('ajax', False), ('thinking', False), ('fill', False),
    ('retry_redirect', False), ('delayed_redirect', False), ('after_final', False),
    ('goto', True), ('click', True), ('reload', True), ('tab_focus', True)])
def test_readback_requires_later_agent_document_navigation(tmp_path, kind, expected):
    base = dict(matched_entry=None, method='GET', url='http://local/user/a', ts=12, request_ts=12)
    write = dict(matched_entry='fam-6', method='POST', url='http://local/write', ts=10, request_ts=9.9)
    flows = [write, base | dict(ts=10.2, request_ts=10.2)]
    action = kind if kind in ('goto', 'click', 'reload', 'tab_focus') else 'click'
    steps = [dict(step=1, action="click('save')", started_at=9, action_started_at=9.5, ended_at=11),
             dict(step=2, action=f"{action}('x')", started_at=11, action_started_at=11.5, ended_at=13)]
    if kind == 'automatic':
        base.update(ts=10.5, request_ts=10.5)
    if kind == 'thinking':
        base.update(ts=11.2, request_ts=11.2)
    if kind == 'fill':
        steps[1]['action'] = "fill('field', 'value')"
    if kind == 'retry_redirect':
        flows.append(write | dict(ts=11.8, request_ts=11.8))
    flows.append(base)
    def har(r, redirect=''):
        headers = [{'name': 'Upgrade-Insecure-Requests', 'value': '1'},
                   {'name': 'Accept', 'value': 'text/html'}]
        if kind == 'ajax' and r['method'] == 'GET':
            headers = [{'name': 'Accept', 'value': 'text/html'}]
        return dict(startedDateTime=datetime.fromtimestamp(r['request_ts'], timezone.utc).isoformat(),
                    request=dict(method=r['method'], url=r['url'], headers=headers),
                    response=dict(redirectURL=redirect))
    entries = [har(r) for r in flows]
    if kind == 'delayed_redirect':
        # A delayed POST redirect arriving in a later navigation action interval.
        entries = [har(write, '/user/a'), har(base)]
    (tmp_path / 'network.har').write_text(json.dumps({'log': {'entries': entries}}))
    lines(tmp_path / 'proxy.jsonl', flows)
    lines(tmp_path / 'trace.jsonl', steps)
    r = evidence(tmp_path, dict(family='fam-6', site='reddit', start_url='http://local/'), {},
                 11.9 if kind == 'after_final' else 14, 'C0')
    assert r['readback_any'] and r['readback_after_write']
    assert r['readback_agent'] is expected


def test_restore_updates_parent_without_cascading(monkeypatch):
    from probes import shopping_admin as p
    b = dict(task_id=453, scopes={'catalog_product_super_attribute': 'product_id IN (78)'},
             rows={'catalog_product_super_attribute': [{'product_super_attribute_id': 1, 'product_id': 78, 'position': 0}]})
    calls = []
    def query(site, sql):
        calls.append(sql)
        if len(calls) == 1:
            return [[dict(b['rows']['catalog_product_super_attribute'][0], position=2)]]
        if len(calls) == 2:
            return [[dict(table_name='catalog_product_super_attribute', column_name='product_super_attribute_id')]]
        return []
    monkeypatch.setattr(p, 'query', query)
    p.restore(453, b)
    assert len(calls[-1]) == 1 and calls[-1][0].startswith('UPDATE ')
    assert 'DELETE' not in calls[-1][0]


@pytest.mark.parametrize('family', ['fam-12', 'fam-13', 'fam-15', 'fam-16', 'fam-6100', 'future-post'])
@pytest.mark.parametrize('claim_time', [None, 5])
@pytest.mark.parametrize('path,expected', [
    ('/submit', False), ('/submit/headphones?next=1', False),
    ('/f/headphones/submit', False), ('/forums', False),
    ('/user/a', False), ('/user/a/account', False),
    ('/f/headphones/37/post', True), ('/f/headphones/37/post/edit', True),
    ('/f/headphones', True), ('/f/headphones/new?sort=1', True),
    ('/user/a/submissions', True), ('/user/a/submissions?page=2', True)])
def test_post_creation_readback_requires_persisted_view_even_at_step_limit(
        tmp_path, family, claim_time, path, expected):
    lines(tmp_path / 'trace.jsonl', [
        dict(step=1, action="click('save')", action_started_at=1, ended_at=2),
        dict(step=2, action="goto('view')", action_started_at=3, ended_at=4)])
    lines(tmp_path / 'proxy.jsonl', [
        dict(matched_entry=family, method='POST', url='http://local/submit', ts=1.5),
        dict(matched_entry=None, method='GET', url='http://local'+path, ts=3.5, document_navigation=True)])
    entry = dict(effect='create', url_regex=r'^/submit(?:/|$)',
                 f6=dict(views=[r'^/submit', r'^/forums', r'^/f/', r'^/user/']))
    result = evidence(tmp_path, dict(family=family, site='reddit', start_url='http://local/'),
                      entry, claim_time, 'C0')
    assert result['readback_agent'] is expected
    assert result['n_readbacks_agent'] == int(expected)
    # The legacy broad metric still accepts the forms when there is a claim.
    assert result['readback_any'] is (claim_time is not None)
    assert result['readback_after_write'] is (claim_time is not None)
    assert result['n_readbacks'] == int(claim_time is not None)


def test_metric_migration_preserves_backup_and_concurrent_append(tmp_path, monkeypatch):
    import tools.recompute_metrics as migration
    from agent.browser import ROOT
    tasks = tmp_path / 'tasks'
    tasks.mkdir()
    (tasks / 'reddit_400.json').write_text((ROOT / 'tasks/reddit_400.json').read_text())
    directory = tmp_path / 'runs/example'
    directory.mkdir(parents=True)
    original = dict(episode='example', task='reddit-400', family='fam-6', condition='C0',
                    final_message_at=5, readback_after_write=False)
    (directory / 'record.json').write_text(json.dumps(original))
    aggregate = tmp_path / 'runs/records.jsonl'
    lines(aggregate, [original])
    inode = aggregate.stat().st_ino
    monkeypatch.setattr(migration, 'load_catalog', lambda path: SimpleNamespace(entries=[{'id': 'fam-6'}]))
    write_json = migration.write_json
    appended = dict(original, episode='finished-during-migration')
    def publish(path, value):
        if path == directory / 'record.json' and not getattr(publish, 'appended', False):
            with aggregate.open('a') as out:
                out.write(json.dumps(appended) + '\n')
            publish.appended = True
        write_json(path, value)
    monkeypatch.setattr(migration, 'write_json', publish)
    assert migration.recompute(tmp_path)['records'] == 1
    assert aggregate.stat().st_ino == inode
    records = [json.loads(line) for line in aggregate.read_text().splitlines()]
    assert len(records) == 2 and records[1] == appended
    assert records[0]['readback_agent'] is False
    assert json.loads((directory / 'record.before_m3b_metrics.json').read_text()) == original
    migration.recompute(tmp_path)
    assert json.loads((directory / 'record.before_m3b_metrics.json').read_text()) == original


def test_delayed_write_redirect_excluded_with_proxy_only(tmp_path):
    lines(tmp_path / 'trace.jsonl', [
        dict(step=1, action="click('save')", action_started_at=1, ended_at=2),
        dict(step=2, action="click('other')", action_started_at=3, ended_at=4)])
    lines(tmp_path / 'proxy.jsonl', [
        dict(matched_entry='fam-6', method='POST', url='http://local/save', ts=1.5,
             redirect_url='/user/a'),
        dict(matched_entry=None, method='GET', url='http://local/user/a', ts=3.5,
             document_navigation=True)])
    r = evidence(tmp_path, dict(family='fam-6', site='reddit', start_url='http://local/'), {}, 5, 'C0')
    assert r['readback_any'] and not r['readback_agent']
