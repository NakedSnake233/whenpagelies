import io
import json
import pytest

from agent.agent import ActionParseError, claim_from_message, final_message, parse_action, run_agent
from agent.env import ALLOWED_ACTIONS, EpisodeTask, action_set
from agent.llm import Reply, ZERO_USAGE
from agent.policies import policy_instruction


@pytest.mark.parametrize('text,expected', [
    ('Brief thought\n```action\nclick("2")\n```', 'click("2")'),
    ('click("1")', 'click("1")'),
    ('```action\nsend_msg_to_user(text="DONE: saved")\n```', 'send_msg_to_user(text="DONE: saved")'),
])
def test_parse(text, expected):
    assert parse_action(text) == expected


@pytest.mark.parametrize('text', [ '```python\nclick("1")\n```',
    '```action\nclick("1"); click("2")\n```', '```action\n__import__("os")\n```',
    '```action\nclick(str(2))\n```', '```action\npage.goto("http://x")\n```',
    '```action\nclick(**{})\n```', '```action\nx = click("1")\n```'])
def test_bad_parse(text):
    with pytest.raises(ActionParseError):
        parse_action(text)


@pytest.mark.parametrize('message,claim', [('DONE: saved', 'DONE'), ('NOT DONE: missing', 'NOT DONE'),
    ('not DONE: unclear', 'NONE'), ('', 'NONE'), ('I say DONE: yes', 'NONE')])
def test_claim(message, claim):
    assert claim_from_message(message) == claim


def test_final_and_policies():
    assert final_message('send_msg_to_user("DONE: saved")') == 'DONE: saved'
    assert final_message('report_infeasible("missing")') == 'NOT DONE: missing'
    assert final_message('click("2")') is None
    assert policy_instruction('B0') == ''
    assert policy_instruction('B1') == 'Before finishing, verify that your change actually took effect.'
    for policy in ('B2', 'VRV', 'Oracle', 'VRV-modeltrigger', 'VRV-samepage', 'VRV-nocbr'):
        assert isinstance(policy_instruction(policy), str)
    with pytest.raises(ValueError, match='Unknown policy'):
        policy_instruction('unknown')


def test_task_ignores_browsergym_greeting():
    task = EpisodeTask(0, {})
    chat = [{'role': 'assistant', 'message': 'Hello'}, {'role': 'user', 'message': 'goal'}]
    assert task.validate(None, chat) == (0, False, '', {})
    assert task.validate(None, chat + [{'role': 'assistant', 'message': 'DONE: saved'}])[1]


def test_parse_noop_error_reaches_next_prompt_and_limit(monkeypatch):
    monkeypatch.setattr('agent.agent.observation', lambda obs: dict(goal='goal', url='local', tabs=[],
        ax='tree', last_action='', last_error=''))
    class Env:
        def reset(self, seed):
            return {}, {}
        def step(self, action):
            pytest.fail('invalid replies must execute nothing')
    class LLM:
        prompts = []
        def complete(self, system, prompt):
            from agent.llm import Reply
            self.prompts.append(prompt)
            return Reply('bad action')
    llm, trace = LLM(), io.StringIO()
    state = dict(steps=0, claim='NONE', final_message='', tokens=ZERO_USAGE.copy(), cost=0)
    run_agent(Env(), llm, 'B1', 0, 2, trace, state)
    rows = [json.loads(line) for line in trace.getvalue().splitlines()]
    assert len(rows) == 2 and all(row['error'] for row in rows)
    assert 'one valid action call' in llm.prompts[1]
    assert state['steps'] == 2 and state['claim'] == 'NONE'


@pytest.mark.parametrize('tail', ['fill("2", "x")', 'send_msg_to_user("Filled body text")',
                                'send_msg_to_user("DONE: saved")'])
def test_multiple_fences_rejected(tail):
    with pytest.raises(ActionParseError, match=r'^Reply with exactly one action per turn\.$'):
        parse_action(f'```action\nclick("1")\n```\n```action\n{tail}\n```')


@pytest.mark.parametrize('text', ['Thought\nclick("1")', 'click("1")\nclick("2")',
    'click(\n"1"\n)', 'click("1") # explanation', 'click("1");', 'click()',
    'click("1", unknown=True)', 'fill("1")', 'send_msg_to_user()', 'send_msg_to_user(3)',
    'click(bid="1", bid="2")', 'click(*["1"])', 'noop() or click("1")', '',
    'Here is click("1")', '```action\nclick("1")', 'click("1")\nexplanation'])
def test_invalid_unfenced_format(text):
    with pytest.raises(ActionParseError, match='one valid action call'):
        parse_action(text)


@pytest.mark.parametrize('call', ['click("123")', 'fill(bid="4", value="a")', 'noop()',
    'noop(wait_ms=10)', 'send_msg_to_user("DONE: seen")'])
def test_lenient_metadata_and_noop_agreement(call):
    for text, lenient in [(call, True), (f'```action\n{call}\n```', False)]:
        metadata = {}
        assert parse_action(text, metadata=metadata) == call
        assert metadata == {'lenient_parse': lenient}
    assert set(action_set().action_set) == ALLOWED_ACTIONS


@pytest.mark.parametrize('call', ['click("notice-123")', 'fill("Price", "2")',
    'select_option(bid="notice-stock", options="1")', 'hover("#1")', 'press("[12]", "Enter")',
    'click(12)', 'click("１２")'])
def test_element_id_reminder(call):
    with pytest.raises(ActionParseError, match='bracketed numeric bid shown in the observation'):
        parse_action(call)


def scripted_run(monkeypatch, replies):
    monkeypatch.setattr('agent.agent.observation', lambda obs: dict(goal='goal', url='local',
        tabs=[], ax='tree', last_action='', last_error=''))
    class Env:
        actions = []
        def reset(self, seed):
            return {}, {}
        def step(self, action):
            self.actions.append(action)
            return {}, 0, action.startswith(('send_msg_to_user', 'report_infeasible')), False, {}
    class LLM:
        prompts = []
        pending = iter(replies)
        def complete(self, system, prompt):
            self.prompts.append(prompt)
            return Reply(next(self.pending))
    env, llm, trace = Env(), LLM(), io.StringIO()
    state = dict(steps=0, claim='NONE', final_message='', tokens=ZERO_USAGE.copy(), cost=0)
    run_agent(env, llm, 'B0', 0, len(replies), trace, state)
    return env.actions, llm.prompts, state, [json.loads(line) for line in trace.getvalue().splitlines()]


def test_multi_fence_never_dispatches_or_finishes(monkeypatch):
    text = '```action\nfill("2", "body")\n```\n```action\nsend_msg_to_user("Finished")\n```'
    actions, prompts, state, rows = scripted_run(monkeypatch, [text, 'send_msg_to_user("NOT DONE: stop")'])
    assert actions == ['send_msg_to_user("NOT DONE: stop")']
    assert rows[0]['error'] == 'Reply with exactly one action per turn.'
    assert rows[0]['error'] in prompts[1]
    assert state['steps'] == 2 and state['claim'] == 'NOT DONE'
    assert rows[1]['lenient_parse'] is True


@pytest.mark.parametrize('corrected', ['send_msg_to_user("DONE: observed")',
    'send_msg_to_user(text="NOT DONE: uncertain")', 'report_infeasible("missing")'])
def test_final_prefix_two_corrections_then_valid_message(monkeypatch, corrected):
    actions, prompts, state, rows = scripted_run(monkeypatch, [
        'send_msg_to_user("Filled body")', 'send_msg_to_user(text="Still finished")', corrected])
    assert actions == [corrected]
    assert state['steps'] == 3 and state['claim'] != 'NONE'
    assert all('must start with DONE: or NOT DONE:' in row['error'] for row in rows[:2])
    assert rows[0]['error'] in prompts[1] and rows[1]['error'] in prompts[2]


def test_final_prefix_exhaustion_stops_without_browser_dispatch(monkeypatch):
    actions, _, state, rows = scripted_run(monkeypatch,
        ['send_msg_to_user("Finished")'] * 3 + ['send_msg_to_user("DONE: must not run")'])
    assert not actions and len(rows) == 3
    assert state['steps'] == 3 and state['claim'] == 'NONE'
    assert rows[-1]['prefix_corrections_exhausted'] is True
    assert rows[-1]['prefix_corrections'] == 2


def test_valid_noop_is_executed_and_lenient_is_logged(monkeypatch):
    actions, _, _, rows = scripted_run(monkeypatch, ['noop()', '```action\nnoop(wait_ms=0)\n```'])
    assert actions == ['noop()', 'noop(wait_ms=0)']
    assert [row['lenient_parse'] for row in rows] == [True, False]
    assert not any(row['error'] for row in rows)
