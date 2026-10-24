"""Provider coverage uses fake files, mock HTTP, and isolated ledgers only."""
import json
import sqlite3

import httpx
import pytest

from agent.llm import CONFIG, LLMClient, cost_for
from runner.budget import Budget, BudgetExceeded
from tools import secrets
from tools.providers import budget_path


@pytest.fixture(autouse=True)
def provider_budget_config(tmp_path, monkeypatch):
    """Exercise provider limits without depending on local budget settings."""
    from runner import budget
    import yaml
    config = yaml.safe_load(budget.CONFIG_PATH.read_text())
    config['providers'] = {'lmuai': {
        'workspace_monthly_usd': 15, 'per_model_monthly_usd': 15,
        'provider_month_anchor': {'day': 1, 'hour': 0, 'minute': 0, 'second': 0},
        'fractions': {'five_hours': 1.0, 'seven_days': 1.0, 'month': 1.0}}}
    path = tmp_path / 'budget.yaml'
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(budget, 'CONFIG_PATH', path)


@pytest.fixture
def lmuai(monkeypatch, tmp_path):
    monkeypatch.setenv('OSD_PROVIDER', 'lmuai')
    path = tmp_path / 'fake.env'
    path.write_text('# fake credentials only\nLMUAI_BASE_URL="https://fake.invalid/v1"\n'
                    'DEEPSEEK_API_KEY=fake-deep\nQWEN_API_KEY=\'fake-qwen\'\nMIMO_API_KEY=fake-mimo\n')
    monkeypatch.setattr(secrets, '_lmuai_path', lambda: path)
    monkeypatch.setattr(secrets, '_credential_path', lambda: tmp_path / 'opencode.env')
    for name in ('DEEPSEEK_API_KEY', 'QWEN_API_KEY', 'MIMO_API_KEY', 'LMUAI_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    return path


def test_parser(tmp_path):
    path = tmp_path / 'fake.env'
    path.write_text('\n# comment\nA="fake-value=literal#hash" # comment\nB=\'fake-second\'\n')
    values = secrets.parse_env_file(path)
    assert values.get('A') == 'fake-value=literal#hash'
    assert values.get('B') == 'fake-second'
    assert 'fake' not in str(values) + repr(values)
    for invalid in ('fake-secret-without-assignment', 'A="fake-secret', 'fake-secret!=value'):
        path.write_text(invalid)
        with pytest.raises(ValueError) as error:
            secrets.parse_env_file(path)
        assert 'fake-secret' not in str(error.value) + repr(error.value)
    with pytest.raises(RuntimeError) as error:
        secrets.parse_env_file(tmp_path / 'fake-secret-missing')
    assert 'fake-secret' not in str(error.value) + repr(error.value)


def test_parser_empty_values(tmp_path):
    path = tmp_path / 'empty.env'
    path.write_text('A=""\nB=\'\' # optional\nC=\n')
    values = secrets.parse_env_file(path)
    assert all(values.get(name) == '' for name in ('A', 'B', 'C'))


def test_default_provider_routing(monkeypatch):
    monkeypatch.delenv('OSD_PROVIDER', raising=False)
    monkeypatch.setattr('agent.llm.load_opencode_key', lambda: 'fake-opencode')
    client = LLMClient('qwen3.7-plus', 'default', transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={})))
    try:
        assert client.provider == 'opencode'
        assert client.base_url == CONFIG['base_url']
        assert client.key == 'fake-opencode'
        assert client.model_id == 'qwen3.7-plus'
        assert client.config == CONFIG['models']['qwen3.7-plus']
        assert client.config['format'] == 'messages'
        assert budget_path().name == 'budget.sqlite'
    finally:
        client.close()


@pytest.mark.parametrize('model,key', [('deepseek-v4.1-flash','fake-deep'),
    ('qwen3.7-plus','fake-qwen'), ('mimo-v2.6-flash','fake-mimo')])
def test_routing(lmuai, monkeypatch, model, key):
    config = CONFIG['providers']['lmuai']['models'][model]
    monkeypatch.setitem(config, 'model_id', 'mapped-model')
    def handler(request):
        assert str(request.url) == 'https://fake.invalid/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer ' + key
        assert 'x-api-key' not in request.headers
        body = json.loads(request.content)
        assert body['model'] == 'mapped-model' and body['temperature'] == 0
        return httpx.Response(200, json={'choices':[{'message':{'content':'ok'}}],
            'usage':{'prompt_tokens':10,'completion_tokens':2}})
    client = LLMClient(model, 'fake', transport=httpx.MockTransport(handler))
    try:
        assert client.config['format'] == 'chat'
        reply = client.complete('s', 'p')
        assert reply.cost == cost_for(model, reply.usage)
        with client.budget.connect() as db:
            assert db.execute('SELECT provider,status FROM calls').fetchone() == ('lmuai','complete')
    finally:
        client.close()


def test_settings_override_and_default(lmuai, monkeypatch):
    monkeypatch.setenv('QWEN_API_KEY', 'fake-override')
    settings = secrets.load_lmuai_settings('QWEN_API_KEY')
    assert settings.get('key') == 'fake-override'
    assert 'fake' not in str(settings) + repr(settings)
    monkeypatch.setenv('LMUAI_BASE_URL', 'https://override.invalid/v1/')
    assert secrets.load_lmuai_settings('QWEN_API_KEY').get('base_url') == 'https://override.invalid/v1'
    monkeypatch.delenv('LMUAI_BASE_URL')
    lmuai.unlink()
    assert secrets.load_lmuai_settings('QWEN_API_KEY').get('base_url') == 'https://api.lmuai.ai/v1'


def test_ledger_separation(monkeypatch):
    monkeypatch.delenv('OSD_PROVIDER', raising=False)
    original = Budget()
    original.record('qwen3.7-plus', 100)
    original.pause()
    monkeypatch.setenv('OSD_PROVIDER', 'lmuai')
    other = Budget()
    assert budget_path().name == 'budget_lmuai.sqlite'
    assert original.path != other.path and not other.paused()
    assert other.cap == 15
    token = other.reserve('qwen3.7-plus', 60, 15)
    with pytest.raises(BudgetExceeded):
        other.reserve('qwen3.7-plus', 60, .01)
    other.settle(token, .01)
    assert other.workspace_spend()['five_hours']['limit_usd'] == 15
    with original.connect() as db:
        assert db.execute('SELECT cost,provider FROM calls').fetchone() == (100,'opencode')


def test_probe_mocked(lmuai, capsys):
    from tools.provider_probe import probe
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json={'data':[{'id':'qwen-test'}, {'id':'other'}]})
        body = json.loads(request.content)
        assert body['max_tokens'] == 16 and body['temperature'] == 0
        return httpx.Response(200, json={'model':body['model'],
            'choices':[{'message':{'content':'fake-deep'}}],
            'usage':{'prompt_tokens':4,'completion_tokens':1}},
            headers={'x-request-cost':'0.01','x-ratelimit-remaining':'12',
                     'authorization':secrets.load_lmuai_settings('QWEN_API_KEY').get('key'),'x-cost':'fake-mimo'})
    budget = Budget()
    probe(httpx.MockTransport(handler), budget)
    output = capsys.readouterr().out
    assert all(key not in output for key in ('fake-deep','fake-qwen','fake-mimo','authorization'))
    rows = [json.loads(line) for line in output.splitlines()]
    assert rows[0]['ids'] == ['qwen-test'] and len(requests) == 4
    assert rows[0]['total_ids'] == 2
    assert rows[1]['headers']['x-request-cost'] == .01
    assert rows[1]['returned_model'] == 'deepseek-v4.1-flash'
    assert rows[1]['reply'] == '[REDACTED]'
    assert rows[1]['ledger'][0]['cost_usd'] == rows[1]['estimated_cost_usd']
    with budget.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM calls WHERE provider='lmuai' AND status='complete'").fetchone()[0] == 3


def test_probe_identity(lmuai, capsys):
    from tools.provider_probe import probe
    calls = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [
                {'id': 'minimax-test', 'owned_by': 'test', 'context_length': 1000}]})
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={'model': body['model'], 'system_fingerprint': 'fp-test',
            'choices': [{'message': {'content': 'I am a test model.'}}],
            'usage': {'prompt_tokens': 4, 'completion_tokens': 6}})
    probe(httpx.MockTransport(handler), Budget(), identity=True)
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(calls) == 4
    assert calls[-1]['model'] == 'deepseek-v4.1-flash'
    assert calls[-1]['max_tokens'] == 40 and calls[-1]['temperature'] == 0
    assert calls[-1]['messages'][-1]['content'] == 'Which model and version are you? Answer in one short line.'
    assert rows[0]['metadata'][0]['context_length'] == 1000
    assert rows[-1]['operation'] == 'identity'
    assert rows[-1]['reply'] == 'I am a test model.'
    assert rows[-1]['system_fingerprint'] == 'fp-test'


def test_legacy_schema_migration(tmp_path, monkeypatch):
    monkeypatch.delenv('OSD_PROVIDER', raising=False)
    path = tmp_path / 'legacy.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE calls (id TEXT PRIMARY KEY, model TEXT, episode TEXT, timestamp REAL, cost REAL, status TEXT)')
        db.execute("INSERT INTO calls VALUES ('old','m','e',0,1,'complete')")
    budget = Budget(path)
    with budget.connect() as db:
        assert db.execute('SELECT provider,cost FROM calls').fetchone() == ('opencode',1)


def test_lmuai_prices_are_flat_estimates(lmuai):
    from datetime import datetime, timezone
    from agent.llm import rates
    for model, profile in CONFIG['providers']['lmuai']['models'].items():
        assert profile['prices_estimated'] and 'peak' not in profile
        for field in ('input', 'output', 'cached'):
            assert profile[field] == 2 * CONFIG['models'][model][field]
        assert rates(model, datetime(2026,10,9,2,tzinfo=timezone.utc)) == rates(
            model, datetime(2026,10,9,12,tzinfo=timezone.utc))


def test_probe_errors_do_not_log_bodies(lmuai, monkeypatch, tmp_path, capsys):
    from tools.provider_probe import probe
    monkeypatch.setattr('agent.llm.ROOT', tmp_path)
    def handler(request):
        return httpx.Response(401, text='fake-deep fake-qwen fake-mimo')
    budget = Budget()
    probe(httpx.MockTransport(handler), budget)
    output = capsys.readouterr().out
    assert all(key not in output for key in ('fake-deep', 'fake-qwen', 'fake-mimo'))
    assert not (tmp_path / 'runs/provider-probe/llm_error.json').exists()
    rows = [json.loads(line) for line in output.splitlines()]
    assert len(rows) == 4 and all(row['status'] == 401 for row in rows)
    with budget.connect() as db:
        assert db.execute('SELECT SUM(cost) FROM calls').fetchone()[0] == 0


def test_concurrent_ledger_initialization(lmuai, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / 'concurrent.sqlite'
    def record(_):
        Budget(path).record('qwen3.7-plus', .01)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(record, range(8)))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM calls WHERE provider='lmuai'").fetchone()[0] == 8
