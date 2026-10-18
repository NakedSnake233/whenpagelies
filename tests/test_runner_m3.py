import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from agent.browser import ROOT, scrub_har
from runner.metrics import evidence, wait_rollbacks
from runner.run_episode import load_task, same_rows, restore_verified, previous_baseline
from runner.sweep import Queue

pytestmark = pytest.mark.usefixtures("legacy_budget_config")


def lines(path, values):
    path.write_text(''.join(json.dumps(v) + '\n' for v in values))


def test_task_inventory_and_legacy_locations():
    tasks = [load_task(p) for p in (ROOT / 'tasks').glob('*_*.json') if p.name != 'webarena_candidates.json']
    # M1c extends the original 34-task / seven-family inventory.
    assert len(tasks) >= 34
    assert len({t['family'] for t in tasks}) >= 7
    for name in ['reddit_399', 'shopping_admin_453']:
        a, b = [load_task(ROOT / f'{folder}/{name}.json') for folder in ['tasks', 'config/tasks']]
        assert (a['task_id'], a['family'], a['intent']) == (b['task_id'], b['family'], b['intent'])
    assert all('__' not in t['start_url'] for t in tasks)


def test_evidence_timing_retries_and_origin(tmp_path):
    base = dict(matched_entry='fam-6', method='POST', url='http://local/write')
    lines(tmp_path / 'proxy.jsonl', [base | {'ts': 10}, base | {'ts': 15},
        base | dict(method='GET', url='http://local/user/a/edit', ts=9),
        base | dict(method='GET', url='http://local/user/a/edit', ts=11),
        base | dict(method='GET', url='http://other/user/a/edit', ts=12),
        base | dict(method='GET', url='http://local/user/a/edit', ts=21)])
    lines(tmp_path / 'proxy_events.jsonl', [dict(event='fired', ts=9.5), dict(event='rollback_done', ts=19)])
    lines(tmp_path / 'trace.jsonl', [dict(step=2, started_at=9, ended_at=10.5)])
    task = dict(family='fam-6', site='reddit', start_url='http://local/')
    r = evidence(tmp_path, task, {}, 20, 'F3')
    assert r == dict(fault_fired=True, fired_step=2, fired_time=9.5, n_matched_writes=2,
                    rollback_before_claim=True, readback_after_write=True, readback_any=True, readback_agent=False, n_readbacks=1, n_readbacks_agent=0)
    assert evidence(tmp_path, task, {}, 18, 'F3')['rollback_before_claim'] is False
    assert evidence(tmp_path, task, {}, None, 'F3')['readback_after_write'] is False


def test_rollback_wait_terminal_errors_and_timeout(tmp_path):
    p = tmp_path / 'proxy_events.jsonl'
    lines(p, [{'event': 'rollback_scheduled'}])
    with pytest.raises(TimeoutError, match='rollback_timeout'):
        wait_rollbacks(tmp_path, timeout=0)
    lines(p, [{'event': 'rollback_scheduled'}, {'event': 'rollback_error'}])
    with pytest.raises(RuntimeError, match='rollback_error'):
        wait_rollbacks(tmp_path)
    lines(p, [{'event': 'rollback_scheduled'}, {'event': 'rollback_done'}])
    wait_rollbacks(tmp_path)


def test_restore_compares_scoped_rows_not_sequences():
    a = {'rows': {'t': [{'a': 1}, {'a': 2}]}, 'watermarks': {'t': 5}}
    b = {'rows': {'t': [{'a': 2}, {'a': 1}]}, 'watermarks': {'t': 7}}
    assert same_rows(a, b)
    module = SimpleNamespace(restore=lambda *a: None, baseline=lambda t: b)
    restore_verified(module, {}, a)
    b['rows']['t'][0]['a'] = 3
    with pytest.raises(RuntimeError, match='pre-image'):
        restore_verified(module, {}, a)


def test_har_keeps_mutation_bodies_omits_response_bodies(tmp_path):
    p = tmp_path / 'network.har'
    lines(p, [{'log': {'entries': [{'request': {'postData': {'mimeType': 'x', 'text': 'a=b'},
        'headers': [{'name': 'Authorization', 'value': 'secret'}]},
        'response': {'content': {'text': 'large body', 'size': 10}}}]}}])
    scrub_har(p)
    content = p.read_text()
    assert 'a=b' in content and 'large body' not in content and 'secret' not in content


def test_queue_dedup_site_exclusion_and_retry(tmp_path, monkeypatch):
    q = Queue(tmp_path / 'queue.sqlite')
    args = ('test', ['tasks/reddit_400.json', 'tasks/shopping_admin_453.json'],
            ['deepseek-v4.1-flash'], ['B0'], ['C0', 'F1'], [0])
    assert q.add(*args) == 4
    assert q.add(*args) == 0
    # A live site marker must not affect this isolated queue regression.
    monkeypatch.setattr('runner.sweep.ROOT', tmp_path)
    first, second = q.claim('test', 0), q.claim('test', 1)
    assert first['site'] != second['site']
    assert q.claim('test', 2) is None
    failure = dict(episode='fake', error='transport', error_kind='infrastructure')
    assert q.finish(first, failure) == 'pending'
    retry = q.claim('test', 0)
    assert retry['id'] == first['id'] and retry['attempts'] == 2
    assert q.finish(retry, failure) == 'failed'
    assert q.finish(second, dict(episode='agent-failure', error='', claim='NOT DONE', G=False)) == 'done'


def test_budget_deferral_preserves_attempt(tmp_path, monkeypatch):
    q = Queue(tmp_path / 'queue.sqlite')
    q.add('test', ['tasks/reddit_400.json'], ['deepseek-v4.1-flash'], ['B0'], ['C0'], [0])
    monkeypatch.setattr('runner.sweep.ROOT', tmp_path)
    row = q.claim('test', 0)
    assert q.finish(row, dict(episode='budget', error='BudgetExceeded', error_kind='budget', retry_after=100)) == 'budget_wait'
    assert q.claim('test', 0) is None
    with q.connect() as db:
        assert db.execute('SELECT attempts FROM queue').fetchone()[0] == 0


def test_recover_crash_once(tmp_path, monkeypatch):
    q = Queue(tmp_path / 'queue.sqlite')
    q.add('test', ['tasks/reddit_400.json'], ['deepseek-v4.1-flash'], ['B0'], ['C0'], [0])
    q.claim('test', 0)
    monkeypatch.setattr('runner.sweep.ROOT', tmp_path)
    q.recover('test')
    assert q.claim('test', 0)['attempts'] == 2
    q.recover('test')
    assert q.claim('test', 0) is None
    with q.connect() as db:
        assert db.execute('SELECT status FROM queue').fetchone()[0] == 'failed'


def test_budget_reports_window_release(tmp_path):
    from runner.budget import Budget, BudgetExceeded
    b = Budget(tmp_path / 'budget.sqlite')
    b.record('test', 10, now=100000)
    with pytest.raises(BudgetExceeded) as caught:
        b.reserve('test', 60, 1, now=100001)
    assert caught.value.retry_after == 18000


def test_har_retains_only_required_json_evidence(tmp_path):
    p = tmp_path / 'network.har'
    lines(p, [{'log': {'entries': [{'request': {}, 'response': {'content': {
        'mimeType': 'application/json', 'text': '{"subscribed":true,"token":"private"}'}}}]}}])
    scrub_har(p, response_keys={'subscribed'})
    content = p.read_text()
    assert 'subscribed' in content and 'private' not in content and 'token' not in content


@pytest.mark.parametrize('mode', ['success', 'restore_failure', 'global_failure', 'budget', 'dirty_without_baseline', 'recover_baseline'])
def test_full_episode_cleanup_and_preconditions(tmp_path, monkeypatch, mode):
    import runner.run_episode as runner
    task = load_task(ROOT / 'tasks/reddit_400.json')
    path = tmp_path / 'task.json'
    path.write_text(json.dumps(task))
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    baseline = dict(site='reddit', task_id=400, rows={'users': [{'biography': 'old'}]})
    calls = []
    def global_reset(*args):
        calls.append('global_reset')
        if mode == 'global_failure':
            raise RuntimeError('global reset drift')
    monkeypatch.setattr('tools.pristine_state.reset', global_reset)
    current = {'value': 'old', 'evaluations': 0}
    def capture(task):
        calls.append('baseline')
        return json.loads(json.dumps(baseline))
    def evaluate(task, b):
        calls.append('evaluate')
        current['evaluations'] += 1
        return dict(G=(current['value'] == 'new' or (mode in ('dirty_without_baseline','recover_baseline') and current['evaluations'] == 1)), D=0, details=[])
    def restore(task, b):
        calls.append('restore')
        if mode == 'restore_failure':
            raise RuntimeError('test restore failed')
        current['value'] = 'old'
    module = SimpleNamespace(baseline=capture, evaluate=evaluate, restore=restore)
    real_import = runner.importlib.import_module
    monkeypatch.setattr(runner.importlib, 'import_module', lambda name, *a, **kw: module if name == 'probes.reddit' else real_import(name, *a, **kw))
    monkeypatch.setattr('proxy.catalog.load_catalog', lambda p: SimpleNamespace(entries=[{'id': 'fam-6', 'implemented': True}]))
    monkeypatch.setattr('runner.evaluation.evaluate', lambda *a: (current['value'] == 'new', 'test'))
    def env(task, directory, port):
        calls.append('env')
        return SimpleNamespace(close=lambda: (directory / 'network.har').write_text('{"log":{"entries":[]}}'))
    monkeypatch.setattr(runner, 'make_env', env)
    monkeypatch.setattr(runner, 'LLMClient', lambda *a: SimpleNamespace(close=lambda: None))
    def agent(env, client, policy, seed, steps, trace, state, oracle_fn=None):
        calls.append('agent')
        directory = tmp_path / 'runs' / state['episode']
        (directory / 'proxy.jsonl').touch()
        if mode == 'budget':
            from runner.budget import BudgetExceeded
            raise BudgetExceeded('test budget', 20)
        current['value'] = 'new'
        state.update(claim='DONE', final_message_at=20, steps=1)
    monkeypatch.setattr(runner, 'run_agent', agent)
    if mode == 'recover_baseline':
        old = tmp_path / 'runs/previous'
        old.mkdir(parents=True)
        (old / 'baseline.json').write_text(json.dumps(baseline))
    args = runner.parser().parse_args(['--task', str(path), '--proxy-port', '8300'])
    result = runner.run_episode(args)
    if mode == 'dirty_without_baseline':
        assert 'dirty_state' in result['error'] and 'agent' not in calls
        assert result['G'] is None
    elif mode in ('restore_failure', 'global_failure'):
        assert result['restore_ok'] is False
        assert (tmp_path / 'runs/dirty_reddit').exists()
    else:
        assert result['restore_ok'] and current['value'] == 'old'
        if mode == 'budget':
            assert result['error_kind'] == 'budget' and result['retry_after'] == 20
        else:
            assert result['G'] and result['eval_pass'] and not result['error']
    assert not (tmp_path / 'runs/active_reddit.json').exists() or mode in ('restore_failure', 'global_failure')
    if 'global_reset' in calls:
        assert calls.index('evaluate') < calls.index('restore') < calls.index('global_reset')
    assert json.loads((tmp_path / 'runs' / result['episode'] / 'record.json').read_text()) == result


def test_atomic_record_publication(tmp_path):
    from runner.storage import write_json
    p = tmp_path / 'baseline.json'
    write_json(p, {'rows': {'users': [{'bio': 'before'}]}})
    write_json(p, {'rows': {'users': [{'bio': 'after'}]}})
    assert json.loads(p.read_text())['rows']['users'][0]['bio'] == 'after'
    assert p.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [p]


def test_request_start_prevents_slow_prewrite_get_false_positive(tmp_path):
    lines(tmp_path / 'proxy.jsonl', [dict(matched_entry='fam-6', method='POST', url='http://local/edit', ts=10),
        dict(matched_entry=None, method='GET', url='http://local/user/a', request_ts=8, ts=12)])
    r = evidence(tmp_path, dict(family='fam-6', site='reddit', start_url='http://local/'), {}, 20, 'C0')
    assert r['n_readbacks'] == 0


def test_sensitive_form_fields_redacted_without_losing_eval_payload(tmp_path):
    p = tmp_path / 'network.har'
    lines(p, [{'log': {'entries': [{'request': {'postData': {'mimeType': 'application/x-www-form-urlencoded',
        'text': 'user_biography%5Bbiography%5D=hello+world&user_biography%5B_token%5D=sensitive&form_key=another',
        'params': [{'name': 'form_key', 'value': 'another'}]}}, 'response': {}}]}}])
    scrub_har(p)
    data = p.read_text()
    assert 'hello+world' in data and 'sensitive' not in data and 'another' not in data


def test_worker_restarts_only_its_own_dead_proxy():
    import socket
    from runner.worker_proxy import WorkerProxy
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    proxy = WorkerProxy(91, port)
    try:
        proxy.ensure()
        first = proxy.process.pid
        proxy.process.kill()
        proxy.process.wait(timeout=5)
        proxy.ensure()
        assert proxy.process.pid != first and proxy.process.poll() is None
    finally:
        proxy.close()


def test_worker_refuses_occupied_port_without_touching_owner():
    import socket
    from runner.worker_proxy import WorkerProxy
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        proxy = WorkerProxy(92, sock.getsockname()[1])
        with pytest.raises(OSError):
            proxy.ensure()
        assert proxy.process is None


def test_browser_deadline_bypasses_swallowed_action_exceptions():
    import time
    from runner.deadline import BrowserDeadline, browser_deadline
    with pytest.raises(BrowserDeadline):
        with browser_deadline(.01):
            try:
                time.sleep(.1)
            except Exception:
                pytest.fail('deadline must bypass generic action-error handlers')
    with browser_deadline(.1):
        pass


def test_worker_drains_multiple_rows_in_same_batch(tmp_path, monkeypatch):
    import runner.sweep as sweep
    q = Queue(tmp_path / 'queue.sqlite')
    q.add('batch', ['tasks/reddit_400.json'], ['deepseek-v4.1-flash'], ['B0'], ['C0'], [0,1,2])
    monkeypatch.setattr(sweep, 'ROOT', tmp_path)
    monkeypatch.setattr(sweep, 'die_with_parent', lambda: None)
    (tmp_path / 'logs').mkdir()
    monkeypatch.setattr(sweep, 'WorkerProxy', lambda worker: SimpleNamespace(
        ensure=lambda:None, close=lambda:None, port=8300, process=SimpleNamespace(poll=lambda:None)))
    launched = []
    def launch(command, **kwargs):
        launched.append(command)
        kwargs['stdout'].write(json.dumps(dict(episode=f'fake-{len(launched)}', error='', error_kind=None)) + '\n')
        kwargs['stdout'].flush()
        return SimpleNamespace(pid=99999, wait=lambda **kw:0)
    monkeypatch.setattr(sweep.subprocess, 'Popen', launch)
    sweep.worker_loop(q.path, 'batch', 0, 30)
    assert len(launched) == 3
    with q.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM queue WHERE status='done'").fetchone()[0] == 3


def test_proxy_preflight_accepts_time_wait():
    import socket
    from runner.worker_proxy import WorkerProxy
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', 0))
        port = server.getsockname()[1]
        server.listen()
        with socket.create_connection(('127.0.0.1',port)) as client:
            connection, _ = server.accept()
            connection.close()
            assert client.recv(1) == b''
    proxy = WorkerProxy(93,port)
    try:
        proxy.ensure()
        assert proxy.process.poll() is None
    finally:
        proxy.close()


def test_native_multipart_f2_with_form_catalog():
    from mitmproxy import http
    from proxy.osd_faults import FaultProxy
    body = b'--demo\r\nContent-Disposition: form-data; name="submission[body]"\r\n\r\ndrop me\r\n--demo\r\nContent-Disposition: form-data; name="submission[title]"\r\n\r\nkeep me\r\n--demo--\r\n'
    request = http.Request.make('POST','http://local/submit',body,{'Content-Type':'multipart/form-data; boundary=demo'})
    state = SimpleNamespace(entry={'content_type':'form', 'f2': {'strip_fields':['submission[body]']}})
    FaultProxy().partial_commit(state, SimpleNamespace(request=request))
    assert b'drop me' not in request.content and b'keep me' in request.content


def test_multipart_har_keeps_task_data_redacts_token(tmp_path):
    body = '--demo\r\nContent-Disposition: form-data; name="submission[body]"\r\n\r\nrequired content\r\n--demo\r\nContent-Disposition: form-data; name="submission[_token]"\r\n\r\nsensitive\r\n--demo--\r\n'
    p = tmp_path / 'network.har'
    lines(p, [{'log': {'entries': [{'request': {'postData': {'mimeType': 'multipart/form-data; boundary=demo','text': body}}, 'response': {}}]}}])
    scrub_har(p)
    text=json.loads(p.read_text())['log']['entries'][0]['request']['postData']['text']
    assert 'required content' in text and 'sensitive' not in text


def test_final_dispatch_correction_excludes_observation_delay(tmp_path):
    from tools.m3_recompute_timing import correct_record
    lines(tmp_path/'proxy.jsonl', [dict(matched_entry='fam-6',method='POST',url='http://local/edit',ts=1),
        dict(matched_entry=None,method='GET',url='http://local/user/a',ts=2.5)])
    lines(tmp_path/'proxy_events.jsonl', [dict(event='rollback_done',ts=2.5)])
    action="send_msg_to_user('DONE: saved')"
    lines(tmp_path/'trace.jsonl',[dict(step=2,action=action,error='')])
    (tmp_path/'current_step.json').write_text(json.dumps(dict(step=2,action=action,started_at=2)))
    original=dict(final_message_at=3,final_message='DONE: saved',condition='F1')
    result=correct_record(original,tmp_path,dict(family='fam-6',site='reddit',start_url='http://local/'),{})
    assert result['final_message_at']==2 and result['final_message_observed_at']==3
    assert not result['rollback_before_claim'] and not result['readback_after_write']
    assert original['final_message_at']==3
