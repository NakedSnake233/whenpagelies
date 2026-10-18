import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import pytest

from agent.llm import LLMClient, cost_for, normalise
from runner.budget import Budget, BudgetExceeded

pytestmark = pytest.mark.usefixtures("legacy_budget_config")


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize('model,path,payload,expected,write', [
    ('deepseek-v4.1-flash', '/chat/completions',
     {'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}],
      'usage': {'prompt_tokens': 100, 'completion_tokens': 40,
                'prompt_tokens_details': {'cached_tokens': 20},
                'completion_tokens_details': {'reasoning_tokens': 30}}},
     dict(input=100, cached_input=20, output=40, reasoning=30), 0),
    ('qwen3.7-plus', '/messages',
     {'content': [{'type': 'thinking', 'thinking': 'private'}, {'type': 'text', 'text': 'ok'}],
      'stop_reason': 'end_turn', 'usage': {'input_tokens': 60, 'cache_read_input_tokens': 20,
                                        'cache_creation_input_tokens': 20, 'output_tokens': 40}},
     dict(input=100, cached_input=20, output=40, reasoning=0), 20),
    ('gpt-5.6-luna', '/responses',
     {'status': 'completed', 'output': [{'type': 'reasoning'}, {'type': 'message',
       'content': [{'type': 'output_text', 'text': 'ok'}]}],
      'usage': {'input_tokens': 100, 'input_tokens_details': {'cached_tokens': 20},
                'output_tokens': 40, 'output_tokens_details': {'reasoning_tokens': 30}}},
     dict(input=100, cached_input=20, output=40, reasoning=30), 0),
])
def test_http_formats(monkeypatch, tmp_path, model, path, payload, expected, write):
    monkeypatch.setattr('agent.llm.load_opencode_key', lambda: 'dummy-test-key')
    def handler(request):
        assert request.url.path.endswith(path)
        assert request.headers['Authorization'] == 'Bearer ' + 'dummy-test-key'
        assert request.headers['x-opencode-session'] == 'unit'
        assert request.headers['User-Agent'] == 'osd-bench-agent/0.1'
        body = json.loads(request.content)
        if path == '/messages':
            assert body['system'] == 'system'
            assert request.headers['anthropic-version'] == '2023-06-01'
            assert request.headers['x-api-key'] == 'dummy-test-key'
        elif path == '/responses':
            assert body['instructions'] == 'system'
            assert body['max_output_tokens'] == 4096
            assert 'temperature' not in body
        else:
            assert body['messages'][0]['role'] == 'system'
            assert body['temperature'] == 0
        return httpx.Response(200, json=payload)
    budget = Budget(tmp_path/'budget.db')
    client = LLMClient(model, 'unit', budget, httpx.MockTransport(handler))
    try:
        reply = client.complete('system', 'prompt')
    finally:
        client.close()
    assert reply.text == 'ok'
    assert reply.usage == expected
    assert reply.cache_write == write
    assert reply.cost == pytest.approx(cost_for(model, expected, cache_write=write))
    with budget.connect() as db:
        assert db.execute('SELECT cost,status FROM calls').fetchone() == (reply.cost, 'complete')


@pytest.mark.parametrize('time,peak', [('2026-10-09T01:00', True), ('2026-10-09T04:00', False),
    ('2026-10-09T06:00', True), ('2026-10-09T10:00', False), ('2026-10-10T02:00', False)])
def test_deepseek_cost(time, peak):
    usage = dict(input=1000000, cached_input=200000, output=1000000, reasoning=700000)
    assert cost_for('deepseek-v4.1-flash', usage, utc(time)) == pytest.approx(.7206 * (2 if peak else 1))


def test_cache_write_cost():
    usage = dict(input=1000000, cached_input=200000, output=1000000, reasoning=0)
    assert cost_for('qwen3.7-plus', usage, cache_write=300000) == pytest.approx(1.958)


@pytest.mark.parametrize('old_hours,spent,fraction', [(1, 10.7, .18), (24, 26.9, .45), (240, 56.9, .95)])
def test_budget_windows(tmp_path, old_hours, spent, fraction):
    budget = Budget(tmp_path/'b.sqlite')
    budget.cap = 1000  # isolate legacy per-model windows
    now = utc('2026-10-20T12:00').timestamp()
    budget.record('model', spent, now=now-old_hours*3600)
    with pytest.raises(BudgetExceeded):
        budget.reserve('model', 60, .2, now=now)
    # Independent model accounting.
    budget.reserve('other', 60, .2, now=now)


def test_budget_boundary_month_and_pending(tmp_path):
    b = Budget(tmp_path/'b.sqlite')
    b.cap = 1000  # isolate legacy calendar-month model cap
    now = utc('2026-11-01T00:00').timestamp()
    b.record('m', 57, now=now-8*86400)  # previous calendar month, outside all rolling windows
    token = b.reserve('m', 60, 10.8, now=now)
    with pytest.raises(BudgetExceeded):
        b.reserve('m', 60, .01, now=now)
    b.settle(token, .01)
    b.reserve('m', 60, 10, now=now)


def test_concurrent_reservations(tmp_path):
    b = Budget(tmp_path/'b.sqlite')
    def reserve(_):
        try:
            b.reserve('m', 60, 6)
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, range(2))) == [False, True]


@pytest.mark.parametrize('failure', [429, 503, 'timeout'])
def test_retry_and_redaction(monkeypatch, tmp_path, failure):
    monkeypatch.setattr('agent.llm.load_opencode_key', lambda: 'dummy-sensitive-value')
    calls, sleeps = [], []
    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            if failure == 'timeout':
                raise httpx.ReadTimeout('dummy-sensitive-value')
            return httpx.Response(failure, text='dummy-sensitive-value')
        return httpx.Response(200, json={'choices': [{'message': {'content': 'dummy-sensitive-value'}}],
                                        'usage': {'prompt_tokens': 3, 'completion_tokens': 2}})
    b = Budget(tmp_path/'b.sqlite')
    client = LLMClient('kimi-k2.6', 'unit', b, httpx.MockTransport(handler), sleep=sleeps.append)
    reply = client.complete('system', 'prompt')
    client.close()
    assert reply.text == '[REDACTED]'
    assert len(calls) == 2 and sleeps == [1]
    with b.connect() as db:
        statuses = [x[0] for x in db.execute('SELECT status FROM calls')]
    assert statuses == [('reserved' if failure == 'timeout' else 'http_error'), 'complete']


def test_errors_no_secret_and_retry_limit(monkeypatch, tmp_path):
    monkeypatch.setattr('agent.llm.load_opencode_key', lambda: 'dummy-sensitive-value')
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(500, text='dummy-sensitive-value')
    client = LLMClient('glm-5.2', 'unit', Budget(tmp_path/'b.sqlite'), httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(RuntimeError, match='^LLM HTTP 500$'):
        client.complete('s', 'p')
    client.close()
    assert len(calls) == 6


def test_guard_prevents_http(monkeypatch, tmp_path):
    monkeypatch.setattr('agent.llm.load_opencode_key', lambda: 'dummy')
    budget = Budget(tmp_path/'b.sqlite')
    budget.record('gpt-5.6-luna', 2.7)
    def handler(request):
        pytest.fail('HTTP must not happen after exhausted budget')
    client = LLMClient('gpt-5.6-luna', 'unit', budget, httpx.MockTransport(handler))
    with pytest.raises(BudgetExceeded):
        client.complete('s', 'p')
    client.close()


@pytest.mark.parametrize('model,path,payload,usage,cost', [
    ('minimax-m3', '/messages',
     {'content': [{'type': 'thinking', 'thinking': 'hidden'},
                  {'type': 'text', 'text': 're'}, {'type': 'text', 'text': 'ady'}],
      'stop_reason': 'end_turn',
      'usage': {'input_tokens': 80, 'cache_read_input_tokens': 20, 'output_tokens': 40}},
     dict(input=100, cached_input=20, output=40, reasoning=0), .0000732),
    ('mimo-v2.6-flash', '/chat/completions',
     {'choices': [{'message': {'content': 'ready', 'reasoning_content': 'hidden'}, 'finish_reason': 'stop'}],
      'usage': {'prompt_tokens': 100, 'completion_tokens': 40,
                'prompt_tokens_details': {'cached_tokens': 20},
                'completion_tokens_details': {'reasoning_tokens': 30}}},
     dict(input=100, cached_input=20, output=40, reasoning=30), .000022456),
])
def test_m5_transports(monkeypatch, tmp_path, model, path, payload, usage, cost):
    monkeypatch.setattr('agent.llm.load_opencode_key', lambda: 'dummy')
    def handler(request):
        assert request.url.path.endswith(path)
        body = json.loads(request.content)
        assert body['model'] == model and body['max_tokens'] == 64
        return httpx.Response(200, json=payload)
    client = LLMClient(model, 'unit', Budget(tmp_path/'budget.sqlite'),
                       httpx.MockTransport(handler), max_tokens=64)
    try:
        reply = client.complete('s', 'p')
    finally:
        client.close()
    assert reply.text == 'ready'
    assert reply.usage == usage
    assert reply.cost == pytest.approx(cost)


def test_mimo_flat_cached_usage():
    reply = normalise({'choices': [{'message': {'content': None}}], 'usage': {
        'prompt_tokens': 100, 'prompt_cache_hit_tokens': 80, 'completion_tokens': 64,
        'reasoning_tokens': 64}}, 'chat')
    assert reply.text == ''
    assert reply.usage == dict(input=100, cached_input=80, output=64, reasoning=64)


@pytest.mark.parametrize('status', [400, 401, 403, 404, 422])
def test_client_error_diagnostic(monkeypatch, tmp_path, status):
    key = 'dummy-"sensitive-key'
    monkeypatch.setattr('agent.llm.load_opencode_key', lambda: key)
    monkeypatch.setattr('agent.llm.ROOT', tmp_path)
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={
            'error': {'type': 'invalid_request_error', 'code': 'context_length',
                      'message': f'Cannot process {key}; sk-fakecredential; token=private-token'},
            'headers': {'Authorization': 'Bearer private-auth', 'Custom': 'private-header'},
            'password': 'private-password', 'padding': 'x' * 1000,
        }, headers={'x-private': 'private-response-header'})

    budget = Budget(tmp_path / 'budget.db')
    client = LLMClient('qwen3.7-plus', 'episode', budget,
                       httpx.MockTransport(handler), sleep=sleeps.append)
    try:
        with pytest.raises(RuntimeError, match=(
                f'^LLM HTTP {status}: type=invalid_request_error, code=context_length$')):
            client.complete('system', '你好')
    finally:
        client.close()
    path = tmp_path / 'runs/episode/llm_error.json'
    raw = path.read_text()
    diagnostic = json.loads(raw)
    assert diagnostic['status'] == status
    assert len(diagnostic['body']) == 500
    assert diagnostic['prompt_chars'] == 8
    assert diagnostic['prompt_utf8_bytes'] == 12
    assert '[REDACTED]' in diagnostic['body']
    for secret in (key, json.dumps(key)[1:-1], 'sk-fakecredential', 'private-token',
                   'private-auth', 'private-header', 'private-password', 'private-response-header'):
        assert secret not in raw
    assert len(calls) == 1 and sleeps == []
    assert path.stat().st_mode & 0o777 == 0o600
    with budget.connect() as db:
        assert db.execute('SELECT cost,status FROM calls').fetchall() == [(0, 'http_error')]


def test_client_error_plain_text(monkeypatch, tmp_path):
    monkeypatch.setattr('agent.llm.load_opencode_key', lambda: 'dummy-sensitive-key')
    monkeypatch.setattr('agent.llm.ROOT', tmp_path)
    client = LLMClient('qwen3.7-plus', 'episode', Budget(tmp_path / 'budget.db'),
        httpx.MockTransport(lambda request: httpx.Response(400, text=(
            'Bad request dummy-sensitive-key\nAuthorization: Bearer private-value\n'
            'x-api-key: private-value\n' + 'x' * 600))))
    try:
        with pytest.raises(RuntimeError, match=r'^LLM HTTP 400: provider error \(no type/code\)$'):
            client.complete('s', 'p')
    finally:
        client.close()
    raw = (tmp_path / 'runs/episode/llm_error.json').read_text()
    assert 'dummy-sensitive-key' not in raw and 'private-value' not in raw
    assert len(json.loads(raw)['body']) == 500


def test_client_error_without_provider_reason(monkeypatch, tmp_path):
    monkeypatch.setattr('agent.llm.load_opencode_key', lambda: 'dummy-key')
    monkeypatch.setattr('agent.llm.ROOT', tmp_path)
    client = LLMClient('qwen3.7-plus', 'episode', Budget(tmp_path / 'budget.db'),
        httpx.MockTransport(lambda request: httpx.Response(400, json={'model': 'qwen3.7-plus'})))
    try:
        with pytest.raises(RuntimeError, match=r'provider error \(no type/code\)'):
            client.complete('s', 'p')
    finally:
        client.close()
    diagnostic = json.loads((tmp_path / 'runs/episode/llm_error.json').read_text())
    assert json.loads(diagnostic['body']) == {'model': 'qwen3.7-plus'}
