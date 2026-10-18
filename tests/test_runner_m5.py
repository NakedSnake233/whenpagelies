"""Account-wide guard, persistent pause, and offline M5 plans."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from runner.budget import Budget, BudgetExceeded
from runner.llm import LLMClient
from runner.sweep import Queue
from runner import plans

pytestmark = pytest.mark.usefixtures("legacy_budget_config")


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize('hours,cost,window', [(1, 8.95, 'five_hours'),
                                             (24, 22.45, 'seven_days'), (240, 47.45, 'month')])
def test_workspace_totals_across_all_models(tmp_path, hours, cost, window):
    budget = Budget(tmp_path/'b.sqlite')
    now = stamp('2026-10-20T12:00')
    budget.record('disabled-legacy-model', cost, now=now-hours*3600)
    with pytest.raises(BudgetExceeded, match=f'workspace: {window}'):
        budget.reserve('new-model', 1000, .1, now=now)
    assert budget.workspace_spend(now)[window]['spent_usd'] == cost


def test_provider_anchor_reset_and_calendar_independence(tmp_path):
    budget = Budget(tmp_path/'b.sqlite')
    now = stamp('2026-10-26T08:44:00')
    budget.record('old', 47.5, now=now-10*86400)
    with pytest.raises(BudgetExceeded, match='workspace: month') as caught:
        budget.reserve('new', 1000, .01, now=now-1)
    assert caught.value.retry_after == 2
    budget.reserve('new', 1000, .01, now=now)
    # November 1 does not reset the provider month.
    budget.record('old', 47.5, now=stamp('2026-10-27T00:00'))
    with pytest.raises(BudgetExceeded):
        budget.reserve('new', 1000, .01, now=stamp('2026-11-05T00:00'))
    assert budget.month_bounds(stamp('2026-01-01T00:00')) == (
        stamp('2025-12-26T08:44:00'), stamp('2026-01-26T08:44:00'))
    budget.config['provider_month_anchor'] = dict(day=31, hour=1, minute=2, second=3)
    assert budget.month_bounds(stamp('2026-03-01T00:00')) == (
        stamp('2026-02-28T01:02:03'), stamp('2026-03-31T01:02:03'))


def test_workspace_pending_reservations_atomic_across_models(tmp_path):
    budget = Budget(tmp_path/'b.sqlite')
    def reserve(model):
        try:
            return budget.reserve(model, 1000, 5)
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(reserve, ['a', 'b']))
    assert sum(token is not None for token in tokens) == 1
    budget.settle(next(token for token in tokens if token), 0)
    budget.reserve('c', 1000, 5)


def test_monthly_429_pauses_other_models_and_queues(tmp_path, monkeypatch, capsys):
    queue = Queue(tmp_path/'q.sqlite')
    for name, model in [('main', 'deepseek-v4.1-flash'), ('mitigation', 'minimax-m3')]:
        queue.add(name, ['tasks/reddit_399.json'], [model], ['B0'], ['C0'], [0])
    monkeypatch.setattr('runner.sweep.ROOT', tmp_path)
    monkeypatch.setattr('agent.llm.load_opencode_key', lambda: 'dummy-secret')
    budget = Budget(tmp_path/'runs/budget.sqlite')
    row = queue.claim('main', 0)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={'error': {'type': 'GoUsageLimitError',
                              'metadata': {'limitName': 'monthly'}}})
    client = LLMClient('deepseek-v4.1-flash', 'test', budget, httpx.MockTransport(handler),
                       diagnostics=tmp_path/'429.jsonl', sleep=lambda _: pytest.fail('must not retry'))
    try:
        with pytest.raises(RuntimeError, match='GoUsageLimitError monthly') as caught:
            client.complete('s', 'p')
    finally:
        client.close()
    assert len(calls) == 1
    budget.pause()  # repeated workers emit no duplicate pause announcement
    assert capsys.readouterr().out.count('Workspace paused:') == 1
    assert Queue(queue.path).claim('mitigation', 1) is None
    with pytest.raises(BudgetExceeded, match='workspace paused'):
        budget.reserve('qwen3.7-plus', 60, .01)
    assert queue.finish(row, dict(episode='test', error=str(caught.value), error_kind='infrastructure')) == 'deferred'
    with budget.connect() as db:
        assert db.execute('SELECT cost,status FROM calls').fetchone() == (0, 'http_error')
    queue.resume()
    assert not budget.paused()
    assert queue.claim('main', 0)['attempts'] == 1


def test_disabled_models_refused_on_add_and_existing_claim(tmp_path, monkeypatch):
    queue = Queue(tmp_path/'q.sqlite')
    with pytest.raises(ValueError, match='disabled'):
        queue.add('test', ['tasks/reddit_399.json'], ['kimi-k2.6'], ['B0'], ['C0'], [0])
    queue.add('test', ['tasks/reddit_399.json'], ['deepseek-v4.1-flash'], ['B0'], ['C0'], [0])
    with queue.connect() as db:
        db.execute("UPDATE queue SET model='glm-5.2'")
    monkeypatch.setattr('runner.sweep.ROOT', tmp_path)
    with pytest.raises(ValueError, match='disabled'):
        queue.claim('test', 0)


def test_plan_order_counts_estimates_and_dedup(tmp_path):
    tasks = [(Path('tasks/reddit_399.json').resolve(), False),
             (Path('tasks/reddit_400.json').resolve(), True)]
    rows = list(plans.plan_rows('main', tasks))
    assert len(rows) == 52
    assert [r[1] for r in rows[:4]] == ['mimo-v2.6-flash', 'deepseek-v4.1-flash', 'minimax-m3', 'qwen3.7-plus']
    assert all(r[0] == str(tasks[0][0]) for r in rows[:24])
    assert all(r[3] != 'F2' for r in rows[:24])
    mitigation = list(plans.plan_rows('mitigation', tasks))
    assert len(mitigation) == 84 and all(r[3] != 'F2' for r in mitigation)
    assert {r[2] for r in mitigation} == set(plans.POLICIES)
    runs = tmp_path/'runs'
    for i, record in enumerate([
        dict(model='deepseek-v4.1-flash', cost=.02, steps=2),
        dict(model='deepseek-v4.1-flash', cost=.04, steps=2),
        dict(model='deepseek-v4.1-flash', cost=999, steps=2, dry_run=True),
        dict(model='mimo-v2.6-flash', cost=0, steps=0, error='HTTP 429')]):
        path = runs/str(i)/'record.json'; path.parent.mkdir(parents=True)
        path.write_text(json.dumps(record))
    means = plans.episode_means(runs)
    assert means['deepseek-v4.1-flash']['mean_usd'] == .03
    assert means['mimo-v2.6-flash']['fallback']
    queue = Queue(tmp_path/'plans.sqlite')
    result = plans.build('main', tasks, queue, means, 'test')
    assert result['added'] == 52 and result['estimated_cost_usd'] == pytest.approx(.78)
    assert plans.build('main', tasks, queue, means, 'test')['added'] == 0
    with queue.connect() as db:
        assert dict(db.execute('SELECT status,COUNT(*) FROM queue GROUP BY status')) == {'pending': 52}


def test_task_list_filters_unimplemented_and_f2(tmp_path, monkeypatch):
    monkeypatch.setattr(plans, 'ROOT', tmp_path)
    (tmp_path/'tasks').mkdir(); (tmp_path/'catalog').mkdir()
    (tmp_path/'catalog/reddit.yaml').write_text(yaml.safe_dump({'entries': [
        {'id': 'on', 'implemented': True, 'f2': {'strip_fields': ['x']}},
        {'id': 'off', 'implemented': False}]}))
    for name, data in [('on', {'site': 'reddit', 'family': 'on'}),
                       ('off', {'site': 'reddit', 'family': 'off'}), ('aggregate', [])]:
        (tmp_path/f'tasks/{name}.json').write_text(json.dumps(data))
    (tmp_path/'tasks/INDEX.md').write_text('[on](on.json), [off](off.json)\n')
    (tmp_path/'tasks/excluded.json').write_text(json.dumps({'site': 'reddit', 'family': 'on'}))
    assert plans.task_paths() == [(tmp_path/'tasks/on.json', True)]
    listing = tmp_path/'list.txt'; listing.write_text('# selected\ntasks/on.json\n')
    assert plans.task_paths(listing) == plans.task_paths()
    listing.write_text('["tasks/off.json"]')
    with pytest.raises(ValueError, match='not implemented'):
        plans.task_paths(listing)
    listing.write_text('["tasks/excluded.json"]')
    with pytest.raises(ValueError, match='not listed'):
        plans.task_paths(listing)


def test_paused_worker_does_not_start_proxy(tmp_path, monkeypatch):
    from runner import sweep
    monkeypatch.setattr(sweep, 'ROOT', tmp_path)
    monkeypatch.setattr(sweep, 'die_with_parent', lambda: None)
    Budget(tmp_path/'runs/budget.sqlite').pause()
    monkeypatch.setattr(sweep, 'WorkerProxy', lambda worker: SimpleNamespace(
        ensure=lambda: pytest.fail('paused worker must not start proxy'), close=lambda: None))
    sweep.worker_loop(tmp_path/'q.sqlite', 'test', 0, 30)


def test_smoke_calls_only_four_models_with_64_tokens_and_redacts(monkeypatch, capsys):
    import io
    import urllib.error
    from tools import smoke_llm
    monkeypatch.setattr(smoke_llm, 'load_opencode_key', lambda: 'dummy-sensitive-value')
    requests = []
    def send(request, timeout):
        requests.append(request)
        raise urllib.error.HTTPError(request.full_url, 429, 'limited', {}, io.BytesIO(
            b'{"error":"GoUsageLimitError dummy-sensitive-value","metadata":{"limitName":"monthly"}}'))
    monkeypatch.setattr(smoke_llm.urllib.request, 'urlopen', send)
    assert smoke_llm.main() == 1
    assert len(requests) == 4
    for request in requests:
        body = json.loads(request.data)
        assert body['max_tokens'] == 64
        path = '/messages' if body['model'] in ('minimax-m3', 'qwen3.7-plus') else '/chat/completions'
        assert request.full_url.endswith(path)
    output = capsys.readouterr().out
    assert 'dummy-sensitive-value' not in output and '[REDACTED]' in output
    assert output.count('HTTP 429') == 4 and output.count("reply='' usage={}") == 4
    assert 'GoUsageLimitError' in output and 'monthly' in output
