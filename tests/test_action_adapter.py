import ast

import pytest

from agent.action_adapter import to_action_call
from agent.agent import ActionParseError, parse_action


def native(name, **params):
    return f'<tool_call><function={name}>' + ''.join(
        f'<parameter={k}>{v}</parameter>' for k, v in params.items()) + '</function></tool_call>'


@pytest.mark.parametrize('text,expected', [
    ('I will open the menu.' + native('click', bid='68'), "click(bid='68')"),
    ("<tool_call><function=action>click('12')</function></tool_call>", "click('12')"),
    (native('fill', bid='"123"', value='\'Hello "wide world"\''), 'fill(bid="123", value=\'Hello "wide world"\')'),
    (native('fill', bid='1', value="It's a nice day"), 'fill(bid="1", value="It\'s a nice day")'),
    (native('scroll', delta_x='0', delta_y='-500.5'), 'scroll(delta_x=0, delta_y=-500.5)'),
    (native('send_msg_to_user', text='DONE: saved the profile'), 'send_msg_to_user(text="DONE: saved the profile")'),
    (native('select_option', bid='7', options='001'), 'select_option(bid="7", options="001")'),
    (native('fill', bid='7', value='True', enable_autocomplete_menu='False'), 'fill(bid="7", value="True", enable_autocomplete_menu=False)'),
    (native('press', bid='7', key_comb='Control+a'), 'press(bid="7", key_comb="Control+a")'),
    (native('goto', url='http://localhost/a?x=1&y=2'), 'goto(url="http://localhost/a?x=1&y=2")'),
    (native('noop'), 'noop()'),
    ('click(\'85\')]<]minimax[>[\n<function_calls>\n<invoke name="click">\n<parameter name="bid">"85"</parameter>\n</invoke>\n</function_calls>', 'click(bid="85")'),
])
def test_native_actions(text, expected):
    adapted = to_action_call(text)
    assert ast.dump(ast.parse(adapted)) == ast.dump(ast.parse(expected))
    assert parse_action(adapted) == adapted


@pytest.mark.parametrize('text', ['', 'click("1")', 'thought', '<tool_call>unfinished',
    '```action\nclick("1")\n```' + native('click', bid='2')])
def test_no_adaptation(text):
    assert to_action_call(text) is None


@pytest.mark.parametrize('text', [native('click', bid='1') * 2,
    '<function_calls><invoke name="noop"></invoke><invoke name="noop"></invoke></function_calls>',
    native('noop') + '<function_calls><invoke name="noop"></invoke></function_calls>'])
def test_multiple_actions(text):
    with pytest.raises(ActionParseError, match='^Reply with exactly one action per turn\\.$'):
        to_action_call(text)


@pytest.mark.parametrize('text', [native('unknown'), native('click'), native('click', bid='1', extra='2'),
    native('scroll', delta_x='__import__("os")', delta_y='1'),
    '<tool_call><function=click><parameter=bid>1</parameter><parameter=bid>2</parameter></function></tool_call>',
    '<tool_call><function=noop>garbage</function></tool_call>',
    '<tool_call><function=noop></function><function=noop></function></tool_call>'])
def test_malformed_native(text):
    with pytest.raises(ActionParseError):
        to_action_call(text)


def test_parser_native_metadata_and_policy():
    meta = {}
    assert parse_action(native('click', bid='68'), metadata=meta) == "click(bid='68')"
    assert meta == {'native_format': True, 'lenient_parse': False}
    text = native('report_verification', result='holds', evidence='Visible saved value')
    with pytest.raises(ActionParseError):
        parse_action(text, 'B0')
    assert parse_action(text, 'VRV').startswith('report_verification(')


def test_native_wrapper_still_validates_one_literal_action():
    for body in ['click("1"); click("2")', 'click(str(1))', '__import__("os")', 'click("notice-2")']:
        with pytest.raises(ActionParseError):
            parse_action(f'<tool_call><function=action>{body}</function></tool_call>')


def test_trace_marks_native_format(monkeypatch):
    from tests.test_agent_parse import scripted_run
    actions, _, state, rows = scripted_run(monkeypatch, [
        native('click', bid='68'), native('send_msg_to_user', text='DONE: saved')])
    assert len(actions) == 2 and state['claim'] == 'DONE'
    assert all(row['native_format'] and not row['error'] for row in rows)


@pytest.mark.parametrize('text,count', [
    ('```action\nclick("68")\n```\n```action\nsend_msg_to_user("DONE: saved")\n```', 2),
    ('```action\nclick("68")\nsend_msg_to_user("DONE: saved")\nnoop()\n```', 3),
    ('click("68"); send_msg_to_user("DONE: saved")', 2),
])
def test_luna_first_of_many(text, count):
    metadata = {}
    assert parse_action(text, metadata=metadata, model='gpt-5.6-luna') == 'click("68")'
    assert metadata == {'lenient_parse': 'first_of_many', 'n_actions_in_reply': count}


@pytest.mark.parametrize('first', ['unknown()', 'click()', 'click("bad")', 'click(str(68))',
                                  'click(**{})', 'report_verification("holds", "seen")'])
def test_luna_invalid_first_uses_normal_validation(first):
    with pytest.raises(ActionParseError) as baseline:
        parse_action(first)
    metadata = {}
    with pytest.raises(ActionParseError) as adapted:
        parse_action(f'```action\n{first}\nnoop()\n```', metadata=metadata, model='gpt-5.6-luna')
    assert str(adapted.value) == str(baseline.value)
    assert metadata == {'lenient_parse': 'first_of_many', 'n_actions_in_reply': 2}


@pytest.mark.parametrize('model', [None, 'deepseek-v4.1-flash', 'qwen3.7-plus', 'minimax-m3', 'mimo-v2.6-flash'])
@pytest.mark.parametrize('text', ['```action\nnoop()\nnoop()\n```',
                                 '```action\nnoop()\n```\n```action\nnoop()\n```'])
def test_other_models_remain_strict(model, text):
    with pytest.raises(ActionParseError) as baseline:
        parse_action(text)
    with pytest.raises(ActionParseError) as actual:
        parse_action(text, model=model)
    assert str(actual.value) == str(baseline.value)


def test_luna_adapter_can_be_disabled(monkeypatch):
    from agent.llm import CONFIG
    monkeypatch.delitem(CONFIG['models']['gpt-5.6-luna'], 'action_adapter')
    with pytest.raises(ActionParseError):
        parse_action('```action\nnoop()\nnoop()\n```', model='gpt-5.6-luna')


def test_luna_trace_and_dispatch(monkeypatch):
    from tests.test_agent_parse import scripted_run
    # The existing helper uses Reply directly; attach the real model identity
    # at the run_agent boundary to exercise production dispatch and tracing.
    import tests.test_agent_parse as helper
    original = helper.run_agent
    def run(env, client, *args, **kwargs):
        client.model = 'gpt-5.6-luna'
        return original(env, client, *args, **kwargs)
    monkeypatch.setattr(helper, 'run_agent', run)
    actions, _, state, rows = scripted_run(monkeypatch, [
        '```action\nclick("68")\nsend_msg_to_user("DONE: premature")\n```',
        'send_msg_to_user("NOT DONE: stop")'])
    assert actions == ['click("68")', 'send_msg_to_user("NOT DONE: stop")']
    assert state['claim'] == 'NOT DONE'
    assert rows[0]['lenient_parse'] == 'first_of_many'
    assert rows[0]['n_actions_in_reply'] == 2


def test_luna_malformed_fence_is_not_repaired():
    text = '```action\nnoop()\n`````action\nnoop()\n```'
    with pytest.raises(ActionParseError):
        parse_action(text, model='gpt-5.6-luna')
