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
