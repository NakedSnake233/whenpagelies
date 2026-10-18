"""M4 dry runs: fake Playwright events and scripted, metered LLM replies only."""
import io
import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from agent.agent import ActionParseError, parse_action, run_agent
from agent.browser import ActionNetwork
from agent.env import action_set
from agent.llm import Reply, ZERO_USAGE
from agent.policies import CBR_PROMPT, REFLECTION_PROMPT, SYSTEM_PROMPT, expected_effect

SITE = 'http://site.test'
EDIT = SITE + '/edit'
VIEW = SITE + '/profile'
DONE = 'send_msg_to_user("DONE: saved")'
REPORT = 'report_verification("holds", "The profile page shows the new bio")'
EFFECT = json.dumps({'postcondition': 'The bio is new', 'url': VIEW})
USAGE = dict(input=10, cached_input=2, output=4, reasoning=1)


class Context:
    def __init__(self):
        self.listeners = {}

    def on(self, name, callback):
        self.listeners.setdefault(name, []).append(callback)

    def remove_listener(self, name, callback):
        self.listeners[name].remove(callback)

    def emit(self, name, event):
        for callback in self.listeners.get(name, []):
            callback(event)


class Request:
    def __init__(self, method='POST', url=EDIT, status=200, document=True, body=None):
        self.method, self.url, self.status = method, url, status
        self.resource_type = 'document' if document else 'fetch'
        self.post_data = body
        self.frame = SimpleNamespace()
        self.frame.page = SimpleNamespace(main_frame=self.frame)


def write(status=200, **kwargs):
    return dict(events=[Request(status=status, **kwargs)])


def read(url=VIEW):
    return dict(url=url, events=[Request('GET', url)])


class Browser:
    def __init__(self, steps=()):
        self.context = Context()
        self.steps = iter(steps)
        self.actions = []
        self.url = EDIT
        self.task_kwargs = {'task': {'start_url': EDIT, 'site': 'reddit', 'policy_entry':
            {'method': 'POST', 'url_regex': '^/edit$', 'effect': 'update', 'f6': {'views': ['^/profile', '^/edit$']}}}}

    def obs(self, error=''):
        return dict(goal='Set the bio to new', url=self.url, tabs=[('Site', self.url)],
                    ax='Visible state', last_action=self.actions[-1] if self.actions else '', last_error=error,
                    last_action_error=error)

    def reset(self, seed):
        return self.obs(), {}

    def step(self, action):
        self.actions.append(action)
        scripted = next(self.steps, {})
        for request in scripted.get('events', []):
            self.context.emit('request', request)
            if request.status is not None:
                self.context.emit('response', SimpleNamespace(request=request, status=request.status))
        self.url = scripted.get('url', self.url)
        final = action.startswith(('send_msg_to_user', 'report_infeasible'))
        return self.obs(scripted.get('error', '')), 0, final, False, {}


class LLM:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def complete(self, system, prompt):
        self.calls.append((system, prompt))
        return Reply(next(self.replies), usage=USAGE.copy(), cost=.01)


def action(text):
    return f'```action\n{text}\n```'


@pytest.fixture(autouse=True)
def observation(monkeypatch):
    monkeypatch.setattr('agent.agent.observation', lambda obs: obs.copy())


def run(policy, replies, steps=(), max_steps=None, oracle_fn=None):
    env, client, trace = Browser(steps), LLM(replies), io.StringIO()
    state = dict(steps=0, claim='NONE', final_message='', tokens=ZERO_USAGE.copy(), cost=0)
    run_agent(env, client, policy, 0, max_steps or sum(text.startswith('```action') or text == 'bad parse' for text in replies), trace, state, oracle_fn=oracle_fn)
    rows = [json.loads(line) for line in trace.getvalue().splitlines()]
    assert not any(env.context.listeners.values()), 'event listeners leaked'
    return env, client, state, rows


@pytest.mark.parametrize('network_request,trigger', [
    (Request('POST'), True), (Request('PUT'), True), (Request('PATCH'), True),
    (Request('DELETE'), True), (Request('GET'), False), (Request('HEAD'), False),
    (Request('OPTIONS'), False), (Request('POST', SITE + ':81/save'), False),
    (Request('POST', 'https://site.test/save'), False),
    (Request('POST', 'http://other.test/save'), False),
    (Request('POST', 'http://site.test.evil/save'), False),
    (Request('POST', 'http://SITE.test:80/save'), True),
    (Request('GET', SITE + '/graphql?query=' + quote('mutation Save { saveBio { id } }')), True),
    (Request('GET', SITE + '/graphql?query=' + quote('query { bio }')), False),
    (Request('GET', SITE + '/graphql?query=' + quote('query { search(q: "mutation { fake }") }')), False),
    (Request('GET', SITE + '/graphql?query=' + quote('# mutation { fake }\nquery { bio }')), False),
    (Request('GET', body=json.dumps({'query': 'mutation { saveBio { id } }'})), True),
])
def test_network_trigger_only_mutating_site_requests(network_request, trigger):
    _, _, state, rows = run('B0', [action('click("1")')], [dict(events=[network_request])])
    assert rows[0]['side_effecting'] is trigger
    assert state['n_side_effecting'] == int(trigger)
    if trigger:
        assert rows[0]['mutating_requests'][0]['method'] == network_request.method
        assert rows[0]['mutating_requests'][0]['status'] == network_request.status


def test_network_action_boundaries_and_main_frame():
    context = Context()
    recorder = ActionNetwork(context, EDIT)
    old = Request(status=502)
    context.emit('request', old)  # outside an action
    recorder.begin()
    context.emit('response', SimpleNamespace(request=old, status=502))
    assert not recorder.end()['side_effecting']
    recorder.begin()
    current = Request(status=None)
    context.emit('request', current)
    assert recorder.end()['mutating_requests'][0]['status'] is None
    recorder.begin()
    context.emit('response', SimpleNamespace(request=current, status=502))
    iframe = Request('GET')
    iframe.frame.page.main_frame = object()
    context.emit('request', iframe)
    context.emit('response', SimpleNamespace(request=iframe, status=200))
    assert recorder.end()['document_responses'] == []
    recorder.close()


def test_vrv_pending_done_refused_then_fresh_readback_and_final_audit():
    env, client, state, rows = run('VRV', [
        action('click("1")'), EFFECT, action(DONE), action(REPORT),
        action(f'goto("{VIEW}")'), action(REPORT), action(DONE),
        action(REPORT), action(f'goto("{VIEW}")'), action(REPORT), action(DONE),
    ], [write(), read(), read(), {}])
    assert state['claim'] == 'DONE'
    assert env.actions == ['click("1")', f'goto("{VIEW}")', f'goto("{VIEW}")', DONE]
    assert rows[1]['error'] == 'verification pending'
    assert 'fresh successful' in rows[2]['error'] and 'fresh successful' in rows[6]['error']
    assert rows[5]['error'].startswith('FINAL CHECK:')
    assert sum('final_audit' in row['policy_event'] for row in rows) == 1
    assert state['n_done_refused'] == 2 and state['n_verifications'] == 2
    assert state['n_side_effecting'] == 1 and state['extra_llm_calls'] == 1
    assert 'VERIFICATION REQUIRED: The bio is new' in client.calls[2][1]
    assert 'Suggested verification URL: ' + VIEW in client.calls[2][1]
    assert 'VERIFICATION REQUIRED:' in client.calls[5][1]  # until reported
    assert state['tokens'] == {k: len(client.calls) * v for k, v in USAGE.items()}
    assert state['cost'] == pytest.approx(len(client.calls) * .01)
    assert state['policy_cost'] == pytest.approx(.01)
    assert sum(row['cost'] for row in rows) == pytest.approx(state['cost'])
    assert rows[0]['extra_llm_calls'][0]['usage'] == USAGE


@pytest.mark.parametrize('policy,expected', [('VRV', True), ('VRV-samepage', True), ('VRV-nocbr', False)])
@pytest.mark.parametrize('status,document', [(502, True), (200, False), (None, True)])
def test_cbr_error_or_missing_document(policy, expected, status, document):
    _, client, state, rows = run(policy, [action('click("1")'), EFFECT,
        action('send_msg_to_user("NOT DONE: uncertain")')], [write(status, document=document), {}])
    assert (CBR_PROMPT in client.calls[-1][1]) is expected
    assert ('cbr_prompted' in rows[0]['policy_event']) is expected
    assert state['cbr_prompted'] == int(expected)


def test_samepage_needs_no_navigation_or_reload():
    env, client, state, rows = run('VRV-samepage', [action('click("1")'), EFFECT,
        action(REPORT), action(DONE), action(REPORT), action(DONE)], [write(), {}])
    assert env.actions == ['click("1")', DONE]
    assert state['claim'] == 'DONE' and state['n_verifications'] == 2
    assert state['n_done_refused'] == 1
    assert 'no reload is required' in client.calls[2][1]
    assert 'the current page or another page' in client.calls[1][1]


def test_modeltrigger_has_no_extra_call_or_enforcement_even_after_502():
    _, client, state, rows = run('VRV-modeltrigger', [action('click("1")'), action(DONE)], [write(502), {}])
    assert state['claim'] == 'DONE'
    assert state['extra_llm_calls'] == state['n_done_refused'] == state['cbr_prompted'] == 0
    assert all(not row['policy_event'] for row in rows)
    assert 'After any action you believe changed something' in client.calls[0][1]
    assert 'VERIFICATION REQUIRED:' not in client.calls[1][1]


def test_modeltrigger_allows_voluntary_report():
    _, _, state, rows = run('VRV-modeltrigger', [action(REPORT), action(DONE)])
    assert state['n_verifications'] == 1 and state['claim'] == 'DONE'
    assert rows[0]['policy_event'] == ['verification_reported']


@pytest.mark.parametrize('policy', ['B0', 'B1', 'B2', 'VRV', 'VRV-samepage', 'VRV-nocbr', 'VRV-modeltrigger', 'Oracle'])
def test_readonly_no_verification_or_audit(policy):
    replies = [action(f'goto("{VIEW}")'), action(DONE)]
    if policy == 'B2':
        replies = [replies[0], 'YES opened', replies[1], 'YES finished']
    def oracle():
        pytest.fail('read-only steps must not call oracle')
    _, client, state, rows = run(policy, replies, [read(), {}], oracle_fn=oracle)
    assert state['claim'] == 'DONE'
    assert state['n_side_effecting'] == state['n_done_refused'] == state['n_verifications'] == 0
    assert all(call[0].startswith(SYSTEM_PROMPT) for call in client.calls)


def test_b2_reflects_each_action_including_failed_and_final_and_counts_costs():
    _, client, state, rows = run('B2', [action('click("1")'), 'NO failed',
        'bad parse', 'NO no action', action(DONE), 'YES finished'], [write(502), {}, {}])
    assert len(client.calls) == 6 and state['extra_llm_calls'] == 3
    assert all(REFLECTION_PROMPT in client.calls[i][1] for i in (1, 3, 5))
    assert 'Reflection: NO failed' in client.calls[2][1]
    assert 'Reflection: NO no action' in client.calls[4][1]
    assert state['policy_cost'] == pytest.approx(.03)
    assert state['cost'] == pytest.approx(.06)
    assert state['tokens'] == {k: 6 * v for k, v in USAGE.items()}
    assert all(row['extra_llm_calls'][0]['kind'] == 'reflection' for row in rows)


def test_oracle_runs_before_every_observation_after_first_write():
    calls = []
    def oracle():
        calls.append(1)
        return {'G': True, 'D': 2}
    _, client, state, rows = run('Oracle', [action('fill("2", "new")'),
        action('click("1")'), action(f'goto("{VIEW}")'), action('click("1")'),
        action(DONE)], [{}, dict(events=[Request(), Request('PUT')]), read(), write(502), {}], oracle_fn=oracle)
    assert len(calls) == 3
    assert state['n_side_effecting'] == 2
    assert [row['policy_event'] for row in rows] == [[], [], ['oracle'], ['oracle'], ['oracle']]
    assert all('[ORACLE] backend at ' in client.calls[i][1] for i in (2, 3, 4))
    assert all('goal satisfied = True; extra side effects = 2' in client.calls[i][1] for i in (2, 3, 4))
    assert all(rows[i]['oracle']['seconds_since_write'] >= 0 for i in (2, 3, 4))
    assert state['extra_llm_calls'] == state['policy_cost'] == 0


def test_oracle_missing_callable_fails_before_browser_reset():
    with pytest.raises(ValueError, match='Oracle policy requires oracle_fn'):
        run_agent(None, None, 'Oracle', 0, 1, io.StringIO(), {})


@pytest.mark.parametrize('policy', ['VRV', 'VRV-modeltrigger', 'VRV-samepage', 'VRV-nocbr'])
def test_verification_action_set_and_parser(policy):
    assert 'report_verification' in action_set(policy).action_set
    assert parse_action(action(REPORT), policy) == REPORT
    assert 'report_verification' not in action_set('B0').action_set
    with pytest.raises(ActionParseError):
        parse_action(action(REPORT), 'B0')


@pytest.mark.parametrize('report', [
    'report_verification("maybe", "evidence")', 'report_verification("holds", "")',
    'report_verification("holds")', 'report_verification("holds", "evidence", "extra")',
    'report_verification("holds", "evidence", result="holds")',
])
def test_invalid_reports_keep_pending(report):
    _, _, state, rows = run('VRV-samepage', [action('click("1")'), EFFECT, action(report),
        action(DONE)], [write()])
    assert rows[1]['error']
    assert rows[2]['error'] == 'verification pending'
    assert state['n_verifications'] == 0 and state['claim'] == 'NONE'


def test_multiple_pending_and_negative_report_are_stored_and_all_audited():
    _, _, state, rows = run('VRV-samepage', [action('click("1")'), EFFECT,
        action('click("1")'), EFFECT, action('report_verification(result="does not hold", evidence="old bio")'),
        action(DONE), action(REPORT), action(DONE), action(REPORT), action(DONE), action(REPORT), action(DONE)],
        [write(), write(), {}])
    assert rows[2]['verification_report']['result'] == 'does not hold'
    assert state['n_verifications'] == 4
    assert state['n_done_refused'] == 3
    assert state['claim'] == 'DONE'


def test_not_done_is_allowed_with_pending():
    _, _, state, _ = run('VRV', [action('click("1")'), EFFECT,
        action('report_infeasible("cannot verify")')], [write(), {}])
    assert state['claim'] == 'NOT DONE' and state['n_done_refused'] == 0


def test_failed_readback_cannot_clear_vrv():
    _, _, state, rows = run('VRV', [action('click("1")'), EFFECT,
        action(f'goto("{EDIT}")'), action(REPORT), action(f'goto("{VIEW}")'), action(REPORT)],
        [write(), dict(url=EDIT, events=[Request("GET", EDIT, status=502)]), dict(url=VIEW, events=[Request('GET', VIEW, status=502)])])
    assert rows[2]['error'] and rows[4]['error']
    assert state['n_verifications'] == 0


def test_expected_effect_fallback_never_drops_pending():
    assert expected_effect('```json\n' + EFFECT + '\n```', EDIT) == ('The bio is new', VIEW)
    assert expected_effect('', EDIT)[0]
    _, _, state, rows = run('VRV', [action('click("1")'), 'malformed output', action(DONE)], [write()])
    assert rows[-1]['error'] == 'verification pending'
    assert state['extra_llm_calls'] == 1


def test_trace_does_not_expose_key_headers_or_body():
    context = Context()
    recorder = ActionNetwork(context, EDIT)
    request = Request(url=EDIT + '?token=secret-token&normal=yes', body='password=secret-body')
    recorder.begin()
    context.emit('request', request)
    evidence = json.dumps(recorder.end())
    recorder.close()
    assert 'secret-token' not in evidence and 'secret-body' not in evidence
    assert 'normal=yes' in evidence


def test_failed_write_not_hidden_by_successful_error_document():
    _, client, state, rows = run('VRV', [action('click("1")'), EFFECT,
        action('send_msg_to_user("NOT DONE: uncertain")')],
        [dict(events=[Request(status=502, document=False), Request('GET', SITE + '/error')]), {}])
    assert CBR_PROMPT in client.calls[-1][1]
    assert rows[0]['main_response_status'] == 502
    assert state['cbr_prompted'] == 1


@pytest.mark.parametrize('policy', ['B2', 'VRV', 'Oracle'])
def test_browser_crash_keeps_write_evidence_policy_hook_and_accounting(policy):
    class BrokenBrowser(Browser):
        def step(self, action):
            super().step(action)
            raise RuntimeError('browser disconnected')
    env = BrokenBrowser([write()])
    client = LLM([action('click("1")'), 'NO browser disconnected' if policy == 'B2' else EFFECT])
    trace = io.StringIO()
    state = dict(steps=0, claim='NONE', final_message='', tokens=ZERO_USAGE.copy(), cost=0)
    calls = []
    def oracle():
        calls.append(1)
        return {'G': True, 'D': 0}
    with pytest.raises(RuntimeError, match='browser disconnected'):
        run_agent(env, client, policy, 0, 1, trace, state, oracle_fn=oracle)
    row = json.loads(trace.getvalue())
    assert row['side_effecting'] and state['n_side_effecting'] == 1
    assert not any(env.context.listeners.values())
    assert row['error'] == 'RuntimeError: browser disconnected'
    assert state['cost'] == pytest.approx(.01 if policy == 'Oracle' else .02)
    assert len(calls) == 0  # no subsequent observation on a browser crash


def test_auxiliary_call_failure_preserves_paid_main_call_and_detaches_listeners():
    class FailingLLM(LLM):
        def complete(self, system, prompt):
            if self.calls:
                raise RuntimeError('provider unavailable')
            return super().complete(system, prompt)
    env, client, trace = Browser([write()]), FailingLLM([action('click("1")')]), io.StringIO()
    state = dict(steps=0, claim='NONE', final_message='', tokens=ZERO_USAGE.copy(), cost=0)
    with pytest.raises(RuntimeError, match='provider unavailable'):
        run_agent(env, client, 'VRV', 0, 1, trace, state)
    assert state['cost'] == .01 and state['tokens'] == USAGE
    assert state['n_side_effecting'] == 1 and state['extra_llm_calls'] == 0
    assert json.loads(trace.getvalue())['side_effecting']
    assert not any(env.context.listeners.values())


def test_readback_query_encoding_matches_browser_url():
    url = VIEW + '?q=hello%20world'
    _, _, state, _ = run('VRV', [action('click("1")'), EFFECT,
        action(f'goto("{url}")'), action(REPORT), action('send_msg_to_user("NOT DONE: stop")')],
        [write(), read(url), {}])
    assert state['n_verifications'] == 1


@pytest.mark.parametrize('query,selected,expected', [
    ('query mutation { bio }', None, False),
    ('query { mutation { value } }', None, False),
    ('query Read { bio } mutation Write { saveBio }', 'Read', False),
    ('query Read { bio } mutation Write { saveBio }', 'Write', True),
    ('fragment Fields on User { id } mutation Write($x: Input = {bio: "hi"}) { saveBio(x: $x) }', 'Write', True),
])
def test_graphql_operation_selection(query, selected, expected):
    request = Request('GET', body=json.dumps({'query': query, 'operationName': selected}))
    _, _, state, _ = run('B0', [action('click("1")')], [dict(events=[request])])
    assert state['n_side_effecting'] == int(expected)


def test_successful_internal_report_clears_previous_browser_error():
    _, client, _, _ = run('VRV-samepage', [action('click("1")'), EFFECT,
        action(REPORT), action('send_msg_to_user("NOT DONE: stop")')],
        [dict(events=[Request(status=502)], error='save failed'), {}])
    before_report = json.JSONDecoder().raw_decode(client.calls[2][1])[0]
    after_report = json.JSONDecoder().raw_decode(client.calls[3][1])[0]
    assert before_report['last_error'] == 'save failed'
    assert after_report['last_error'] == ''
    assert after_report['last_action'] == REPORT


def configured_policy(site, entry, current_url):
    from agent.policies import Policy
    policy = Policy('VRV')
    policy.configure(dict(site=site, start_url=SITE), entry)
    network = dict(side_effecting=True, matched_writes=[{}], document_responses=[], main_response_status=200)
    policy.after_action(network, {'url': current_url}, {'action': 'click("1")', 'policy_event': []},
                          lambda *args: Reply(EFFECT))
    return policy


def report_on(policy, url):
    return policy.before_action(REPORT, {'url': url}, {'policy_event': []})


def navigate(policy, url, **fields):
    response = dict(method='GET', status=200, url=url, mutating=False)
    response.update(fields)
    policy.observe_readbacks(dict(side_effecting=False, document_responses=[response]),
                             {'action': f'goto("{url}")'})


def test_magento_edit_reload_acknowledgment_and_completion():
    url = SITE + '/admin/catalog/product/edit/id/872/'
    policy = configured_policy('shopping_admin', {'method': 'POST', 'effect': 'update'}, url)
    assert 'fresh successful' in report_on(policy, url)
    navigate(policy, url)
    assert report_on(policy, url) == ''
    assert 'Verification report accepted:' in policy.decorate('obs')
    assert policy.before_done({'policy_event': []}).startswith('FINAL CHECK:')
    assert 'final audit pending' in policy.decorate('obs')
    assert report_on(policy, url)
    navigate(policy, url)
    assert report_on(policy, url) == ''
    note = policy.decorate('obs')
    assert 'Verification report accepted:' in note
    assert 'Verification is complete. Now send your DONE message.' in note
    assert 'final audit complete' in note
    assert policy.before_done({'policy_event': []}) == ''


@pytest.mark.parametrize('path', ['/submit', '/f/relations/submit', '/forums'])
@pytest.mark.parametrize('samepage', [False, True])
def test_posts_reject_empty_submission_forms(path, samepage):
    policy = configured_policy('reddit', {'method': 'POST', 'effect': 'create', 'url_regex': '^/submit'}, SITE + path)
    if samepage:
        policy.name = 'VRV-samepage'
    navigate(policy, SITE + path)
    error = report_on(policy, SITE + path)
    assert 'not an eligible saved-state view' in error
    assert 'open the post you created or your submissions page' in error


def test_redirect_and_write_action_get_are_not_fresh_readbacks():
    url = SITE + '/f/relations/935/post'
    policy = configured_policy('reddit', {'method': 'POST', 'effect': 'create', 'url_regex': '^/submit'}, url)
    navigate(policy, url, site_redirect=True)
    assert 'site redirects do not count' in report_on(policy, url)
    policy.observe_readbacks(dict(side_effecting=True, document_responses=[
        dict(method='GET', status=200, url=url)]), {'action': 'click("1")'})
    assert report_on(policy, url)
    navigate(policy, url)
    assert report_on(policy, url) == ''


def test_new_write_invalidates_previous_readback():
    policy = configured_policy('reddit', {'method': 'POST', 'effect': 'update',
        'f6': {'views': ['^/profile']}}, VIEW)
    navigate(policy, VIEW)
    policy.after_action(dict(side_effecting=True, matched_writes=[{}], document_responses=[], main_response_status=200),
        {'url': VIEW}, {'policy_event': [], 'action': 'click("1")'}, lambda *args: Reply(EFFECT))
    assert 'fresh successful' in report_on(policy, VIEW)


def test_network_records_redirect_provenance_without_headers_or_body():
    context = Context()
    recorder = ActionNetwork(context, SITE, entry={'method': 'POST', 'url_regex': '^/edit$'})
    recorder.begin()
    req = Request('GET', VIEW)
    req.redirected_from = Request()
    context.emit('request', req)
    context.emit('response', SimpleNamespace(request=req, status=200))
    assert recorder.end()['document_responses'][0]['site_redirect'] is True
    recorder.close()


@pytest.mark.parametrize('policy_name', ['VRV', 'VRV-nocbr'])
@pytest.mark.parametrize('status,result,accepted', [
    (404, 'does not hold', True), (410, 'does not hold', True),
    (200, 'does not hold', True), (404, 'holds', False), (410, 'holds', False),
    (403, 'does not hold', False), (403, 'holds', False),
    (401, 'does not hold', False), (502, 'does not hold', False),
])
def test_fresh_negative_evidence_status(policy_name, status, result, accepted):
    policy = configured_policy('reddit', {'method': 'POST', 'effect': 'create',
        'url_regex': '^/submit'}, SITE + '/f/relations/935/post')
    policy.name = policy_name
    url = SITE + '/f/relations/935/post'
    navigate(policy, url, status=status)
    error = policy.before_action(f'report_verification("{result}", "saved item absent")',
                                {'url': url}, {'policy_event': []})
    assert (error == '') is accepted
    assert bool(policy.pending) is not accepted


@pytest.mark.parametrize('policy_name', ['VRV', 'VRV-nocbr'])
def test_read_recorded_notice_once_per_read(policy_name):
    policy = configured_policy('reddit', {'method': 'POST', 'effect': 'update',
        'f6': {'views': ['^/profile']}}, VIEW)
    policy.name = policy_name
    policy.decorate('obs')
    for status in (200, 404):
        navigate(policy, VIEW, status=status)
        note = policy.decorate('obs')
        assert note.count('Read recorded for /profile. Now call report_verification for:') == 1
        assert policy.pending[0]['postcondition'] in note
        assert 'Read recorded for' not in policy.decorate('obs')


def test_later_auth_error_invalidates_successful_read():
    policy = configured_policy('reddit', {'method': 'POST', 'effect': 'update',
        'f6': {'views': ['^/profile']}}, VIEW)
    navigate(policy, VIEW)
    navigate(policy, VIEW, status=403)
    assert report_on(policy, VIEW)


@pytest.mark.parametrize('policy_name', ['VRV', 'VRV-nocbr', 'VRV-samepage'])
def test_verification_notes_include_redo_hint(policy_name):
    policy = configured_policy('reddit', {'method': 'POST', 'effect': 'update',
        'f6': {'views': ['^/profile']}}, VIEW)
    policy.name = policy_name
    hint = 'If the change is missing or wrong, report does not hold, then redo it or finish with NOT DONE.'
    assert hint in policy.decorate('obs')
    navigate(policy, VIEW)
    assert report_on(policy, VIEW) == ''
    assert hint in policy.before_done({'policy_event': []})
    assert hint in policy.decorate('obs')


def test_samepage_read_notice_on_eligible_page_without_reload():
    _, client, _, _ = run('VRV-samepage', [action('click("1")'), EFFECT,
        action('noop()'), action(REPORT), action(DONE), action(REPORT), action(DONE)],
        [write(), {}, {}])
    assert 'Read recorded for /edit. Now call report_verification for:' in client.calls[2][1]
    assert 'Read recorded for' not in client.calls[3][1]
