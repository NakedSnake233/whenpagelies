"""M3c recovery and deferral regressions, isolated from live queue/site state."""
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from runner.budget import Budget, BudgetExceeded
from runner.llm import LLMClient, RateLimitExceeded, retry_seconds
from runner.sweep import Queue

pytestmark = pytest.mark.usefixtures("legacy_budget_config")


@pytest.fixture(autouse=True)
def legacy_curation_roster(monkeypatch):
    # These historical M3c fixtures deliberately reconstruct the old roster.
    from agent.llm import CONFIG
    monkeypatch.setitem(CONFIG['models']['kimi-k2.6'], 'enabled', True)


def test_concurrent_reservations(tmp_path, monkeypatch):
    """A waits at BEGIN; B inserts later, then A must see B after acquiring it.

    Events force the ordering that the previous race-dependent test missed.
    The original agent-owned concurrency test stays untouched for M4b.
    """
    budget = Budget(tmp_path / 'budget.sqlite')
    connect = budget.connect
    reached, release = threading.Event(), threading.Event()
    clock = [100000.0]
    monkeypatch.setattr('runner.budget.time.time', lambda: clock[0])
    class Delayed:
        def __enter__(self):
            self.db = connect().__enter__()
            return self
        def execute(self, sql, *args):
            if sql == 'BEGIN IMMEDIATE':
                reached.set()
                assert release.wait(5)
            return self.db.execute(sql, *args)
        def __exit__(self, *args):
            return self.db.__exit__(*args)
    monkeypatch.setattr(budget, 'connect', Delayed)
    def reserve():
        with pytest.raises(BudgetExceeded):
            budget.reserve('m', 60, 6)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reserve)
        try:
            assert reached.wait(5)
            clock[0] += 1
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?)', ('b', 'm', '', clock[0], 6, 'reserved'))
        finally:
            release.set()
        future.result(timeout=5)


@pytest.mark.parametrize('header', ['90', None])
def test_429_diagnostics_and_exhaustion(tmp_path, monkeypatch, header):
    monkeypatch.setattr('agent.llm.load_opencode_key', lambda: 'dummy-credential')
    count = []
    def handler(request):
        count.append(1)
        credential = 'never-log'
        headers = {'x-ratelimit-remaining': '0', 'authorization': credential, 'x-api-key': credential}
        if header:
            headers['retry-after'] = header
        return httpx.Response(429, headers=headers, text=json.dumps({'error': 'quota exhausted dummy-credential',
            'authorization': 'Bearer ' + credential}) + 'z' * 400)
    path = tmp_path / '429.jsonl'
    client = LLMClient('kimi-k2.6', 'unit', Budget(tmp_path / 'b.sqlite'),
                       httpx.MockTransport(handler), sleep=lambda _: None, max_retries=2, diagnostics=path)
    try:
        with pytest.raises(RateLimitExceeded) as caught:
            client.complete('system', 'prompt')
    finally:
        client.close()
    assert caught.value.retry_after == (90 if header else None)
    logs = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(logs) == len(count) == 3
    assert all(r['status'] == 429 and len(r['body']) <= 300 for r in logs)
    assert 'dummy-credential' not in path.read_text() and 'never-log' not in path.read_text()
    assert all(set(r['headers']) <= {'retry-after', 'x-ratelimit-remaining'} for r in logs)
    assert retry_seconds(formatdate(1000, usegmt=True), now=900) == 100
    assert retry_seconds('invalid') is None and retry_seconds('nan') is None


def test_rate_deferrals_keep_budget_and_move_to_other_row(tmp_path, monkeypatch):
    q = Queue(tmp_path / 'q.sqlite')
    q.add('test', ['tasks/reddit_400.json'], ['kimi-k2.6'], ['B0'], ['C0'], [0, 1])
    monkeypatch.setattr('runner.sweep.ROOT', tmp_path)
    row = q.claim('test', 0)
    for delay in [600, 1200, 2400, 4800, 7200, 7200]:
        assert q.finish(row, dict(episode='429', error='LLM HTTP 429', error_kind='rate_limit')) == 'deferred'
        with q.connect() as db:
            saved = dict(db.execute('SELECT * FROM queue WHERE id=?', (row['id'],)).fetchone())
            assert saved['attempts'] == 0 and saved['retry_after'] == delay
            db.execute("UPDATE queue SET available_at=0 WHERE id=?", (row['id'],))
        row = q.claim('test', 0)
    assert q.finish(row, dict(episode='429', error='LLM HTTP 429', error_kind='rate_limit', retry_after=42)) == 'deferred'
    other = q.claim('test', 0)
    assert other['id'] != row['id']
    with q.connect() as db:
        assert db.execute('SELECT retry_after FROM queue WHERE id=?', (row['id'],)).fetchone()[0] == 42


def test_recover_published_429_uses_deferral(tmp_path, monkeypatch):
    q = Queue(tmp_path / 'q.sqlite')
    q.add('test', ['tasks/reddit_400.json'], ['kimi-k2.6'], ['B0'], ['C0'], [0])
    row = q.claim('test', 0)
    source = Path(row['task']).read_text()
    task = tmp_path / row['task']
    task.parent.mkdir()
    task.write_text(source)
    monkeypatch.setattr('runner.sweep.ROOT', tmp_path)
    directory = tmp_path / 'runs/limited'
    directory.mkdir(parents=True)
    (directory / 'record.json').write_text(json.dumps(dict(episode='limited', task='reddit-400',
        model='kimi-k2.6', policy='B0', condition='C0', seed=0, worker=0,
        error='LLM HTTP 429', error_kind='rate_limit', retry_after=12)))
    # Some filesystems timestamp writes a tick behind time.time().
    with q.connect() as db:
        started = db.execute('SELECT started_at FROM queue').fetchone()[0]
    os.utime(directory / 'record.json', (started + 1, started + 1))
    q.recover('test')
    with q.connect() as db:
        result = dict(db.execute('SELECT * FROM queue').fetchone())
    assert result['status'] == 'deferred' and result['attempts'] == 0 and result['retry_after'] == 12


def test_kill_hung_browser_tree_preserves_preexisting_child(tmp_path):
    from runner.browser_recovery import BrowserProcesses, process_table
    sibling = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    tracker = BrowserProcesses()
    ready = tmp_path / 'ready.json'
    script = '''import os, signal, subprocess, sys, time, json
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child=subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'])
Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]))
time.sleep(30)
'''
    browser = subprocess.Popen([sys.executable, '-c', script, str(ready)])
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        targets = json.loads(ready.read_text())
        killed = tracker.kill(grace=.1, settle=.1)
        assert set(targets) <= set(killed)
        assert browser.wait(timeout=2) == -signal.SIGKILL
        assert sibling.poll() is None
        table = process_table()
        assert all(pid not in table or table[pid][2] == 'Z' for pid in targets)
    finally:
        for process in (browser, sibling):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)


def test_in_flight_requires_response_not_error(tmp_path):
    from runner.browser_recovery import in_flight
    (tmp_path / 'proxy_requests.jsonl').write_text(''.join(json.dumps({'flow_id': str(i)})+'\n' for i in range(3)))
    (tmp_path / 'proxy.jsonl').write_text('{"flow_id":"0","status":200}\n{"flow_id":"1","status":null}\n')
    assert [r['flow_id'] for r in in_flight(tmp_path)] == ['1', '2']


@pytest.mark.parametrize('phase', ['action', 'close'])
@pytest.mark.parametrize('restore_fails', [False, True])
def test_hung_episode_restores_and_requeues(tmp_path, monkeypatch, phase, restore_fails):
    import runner.run_episode as runner
    monkeypatch.setattr('tools.pristine_state.reset', lambda *a: None)
    from runner.deadline import BrowserDeadline
    from runner.run_episode import load_task, ROOT
    task = load_task(ROOT / 'tasks/reddit_400.json')
    task_path = tmp_path / 'task.json'
    task_path.write_text(json.dumps(task))
    q = Queue(tmp_path / 'queue.sqlite')
    q.add('test', ['tasks/reddit_400.json'], ['kimi-k2.6'], ['B0'], ['C0'], [0])
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr('runner.sweep.ROOT', tmp_path)
    calls = []
    b = {'rows': {'users': [{'biography': 'old'}]}}
    def restore(*args):
        calls.append('restore')
        if restore_fails:
            raise RuntimeError('pre-image mismatch')
    module = SimpleNamespace(baseline=lambda _: b, evaluate=lambda *a: dict(G=False, D=0), restore=restore)
    real_import = runner.importlib.import_module
    monkeypatch.setattr(runner.importlib, 'import_module', lambda name, *a, **kw:
                        module if name == 'probes.reddit' else real_import(name, *a, **kw))
    monkeypatch.setattr('proxy.catalog.load_catalog', lambda _: SimpleNamespace(entries=[dict(id='fam-6', implemented=True)]))
    monkeypatch.setattr(runner, 'BrowserProcesses', lambda: SimpleNamespace(kill=lambda: calls.append('kill') or [999]))
    monkeypatch.setattr(runner, 'in_flight', lambda _: calls.append('in_flight') or [{'flow_id': 'write'}])
    def close():
        calls.append('close')
        if phase == 'close':
            raise BrowserDeadline('fake hung close')
    monkeypatch.setattr(runner, 'make_env', lambda *a: SimpleNamespace(close=close))
    monkeypatch.setattr(runner, 'LLMClient', lambda *a: SimpleNamespace(close=lambda: None))
    def agent(*a, **kw):
        if phase == 'action':
            raise BrowserDeadline('fake hung browser')
    monkeypatch.setattr(runner, 'run_agent', agent)
    row = q.claim('test', 0)
    record = runner.run_episode(runner.parser().parse_args(['--task', str(task_path), '--proxy-port', '8300']))
    assert record['browser_killed'] and record['in_flight_count'] == 1
    assert calls.index('kill') < calls.index('in_flight') < calls.index('restore')
    assert record['restore_ok'] is not restore_fails
    assert (tmp_path / 'runs/dirty_reddit').exists() is restore_fails
    assert q.finish(row, record) == ('failed' if restore_fails else 'pending')
    if not restore_fails:
        assert record['error_kind'] == 'infrastructure'
        assert q.finish(q.claim('test', 0), record) == 'failed'


def test_reddit_599_canonical_forum(tmp_path, monkeypatch):
    from probes import reddit
    from probes.site_common import task_data
    replies = iter([[[{'id': 7, 'biography': ''}]], [[]], [[{'id': 10018}]], [[]]])
    queries = []
    monkeypatch.setattr(reddit, 'query', lambda site, sql: queries.extend(sql) or next(replies))
    result = reddit.baseline(task_data(599))
    assert result['forum_id'] == 10018 and 'machinelearning' in queries[2]
    assert result['scopes']['forum_subscriptions'] == 'user_id=7 AND forum_id=10018'


def test_proxy_logs_request_before_response_and_pins_episode(tmp_path, monkeypatch):
    from mitmproxy import http
    from mitmproxy.test import tflow
    from proxy.osd_faults import FaultProxy, Episode
    from proxy.plan import Plan
    from runner.browser_recovery import in_flight
    proxy = FaultProxy()
    proxy.runs_dir = tmp_path
    proxy.enabled = True
    proxy.episode = Episode(Plan('first', 'toy', 'C0', 0), SimpleNamespace(entries=[], origin='http://local'))
    monkeypatch.setattr(proxy, 'refresh', lambda: None)
    flow = tflow.tflow(req=http.Request.make('POST', 'http://local/write', b'secret-body'))
    proxy.request(flow)
    assert [r['flow_id'] for r in in_flight(tmp_path / 'first')] == [flow.id]
    proxy.episode = Episode(Plan('next', 'toy', 'C0', 0), proxy.episode.catalog)
    flow.response = http.Response.make(200, b'ok')
    proxy.response(flow)
    assert in_flight(tmp_path / 'first') == []
    assert not (tmp_path / 'next').exists()
    assert 'secret-body' not in (tmp_path / 'first/proxy_requests.jsonl').read_text()


def test_curation_report_clean_seeds_and_history(tmp_path):
    from tools.curation_report import report
    q = Queue(tmp_path / 'q.sqlite')
    q.add('m3-curation', ['tasks/reddit_400.json'], ['kimi-k2.6', 'qwen3.7-plus'], ['B0'], ['C0'], [0, 1, 2])
    task = tmp_path / 'tasks/reddit_400.json'
    task.parent.mkdir()
    task.write_text(Path('tasks/reddit_400.json').read_text())
    with q.connect() as db:
        rows = db.execute('SELECT * FROM queue').fetchall()
        for row in rows:
            ep = str(row['id'])
            directory = tmp_path / 'runs' / ep
            directory.mkdir(parents=True)
            record = dict(G=True, D=0, restore_ok=True, error='')
            if row['seed'] == 2:
                record['G'] = False
            if row['model'] == 'qwen3.7-plus':
                record['error'] = 'LLM HTTP 429'
            (directory / 'record.json').write_text(json.dumps(record))
            db.execute("UPDATE queue SET status='done',episode_id=? WHERE id=?", (ep, row['id']))
        db.execute('INSERT INTO history VALUES (?,?,?,?)', (rows[0]['id'], 'old-error', 'BrowserDeadline', 1))
    result = report(q.path, tmp_path)[0]
    assert result['selection'] == 'KEEP' and result['solves']['kimi-k2.6'] == 2
    assert result['clean']['qwen3.7-plus'] == 0 and result['harness_errors'] == 1
    with q.connect() as db:
        db.execute("UPDATE queue SET status='pending' WHERE model='kimi-k2.6' AND seed=2")
    assert report(q.path, tmp_path)[0]['selection'] == 'PENDING'
