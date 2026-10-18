import json
from types import SimpleNamespace

import pytest

from agent.browser import browser_environment, scrub_har
from runner.run_episode import parser, run_episode


def test_browser_env_keeps_api_proxy_out_of_child(monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://example.invalid')
    monkeypatch.setenv('https_proxy', 'http://example.invalid')
    monkeypatch.setenv('OPENCODE_API_KEY', 'fake-sensitive-value')
    child = browser_environment()
    assert 'HTTP_PROXY' not in child and 'https_proxy' not in child
    assert 'OPENCODE_API_KEY' not in child
    import os
    assert os.environ['HTTP_PROXY'] == 'http://example.invalid'


def test_har_scrubbing(tmp_path):
    p = tmp_path/'network.har'
    p.write_text(json.dumps({'log': {'entries': [{'request': {'cookies': [{'name': 'session', 'value': 'fake'}],
        'headers': [{'name': 'X-Postmill-Auto-Login', 'value': 'fake'}, {'name': 'Accept', 'value': 'text/html'}]},
        'response': {'headers': [{'name': 'Set-Cookie', 'value': 'fake'}]}}]}}))
    scrub_har(p)
    body = p.read_text()
    assert 'fake' not in body and 'text/html' in body
    assert p.stat().st_mode & 0o777 == 0o600


def test_crash_records_artifacts_and_zero_api_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr('runner.run_episode.ROOT', tmp_path)
    monkeypatch.setattr('runner.run_episode.probe', lambda *a: None)
    monkeypatch.setattr('agent.llm.load_opencode_key', lambda: pytest.fail('dry run loaded API key'))
    task = tmp_path/'task.json'
    task.write_text(json.dumps(dict(id='test', site='reddit', start_url='http://local', intent='test')))
    script = tmp_path/'actions.json'
    script.write_text('[]')
    def fail(*a):
        raise RuntimeError('browser unavailable')
    monkeypatch.setattr('runner.run_episode.make_env', fail)
    args = parser().parse_args(['--task', str(task), '--dry-run', str(script)])
    record = run_episode(args)
    assert record['claim'] == 'NONE' and 'browser unavailable' in record['error']
    directory = tmp_path/'runs'/record['episode']
    assert (directory/'trace.jsonl').exists()
    assert json.loads((directory/'record.json').read_text()) == record
    assert json.loads((tmp_path/'runs/records.jsonl').read_text()) == record
    assert record['G'] is None and record['D'] is None


def test_optional_probe_hook(monkeypatch):
    from runner.run_episode import probe
    calls = []
    monkeypatch.setattr('runner.run_episode.importlib.import_module',
                        lambda name: SimpleNamespace(evaluate=lambda task, phase: calls.append((task, phase))))
    task = {'site': 'reddit'}
    probe(task, 'before')
    assert calls == [(task, 'before')]


def test_baseline_probe_is_not_mistaken_for_phase_hook(monkeypatch):
    from runner.run_episode import probe
    def evaluate(task, baseline):
        pytest.fail('M2 must not invoke a later milestone baseline evaluator')
    monkeypatch.setattr('runner.run_episode.importlib.import_module', lambda name: SimpleNamespace(evaluate=evaluate))
    with pytest.warns(UserWarning, match='not a phase hook'):
        assert probe({'site': 'reddit'}, 'after') is None
