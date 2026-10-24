"""Single-action parsing and bounded browser interaction loop."""
import ast
import json
import inspect
import re
import time

from agent.browser import ActionNetwork
from agent.env import ALLOWED_ACTIONS, action_set, observation
from agent.llm import ZERO_USAGE, redact
from agent.policies import Policy, REFLECTION_PROMPT, REPORT_DESCRIPTION, SYSTEM_PROMPT, VRV_POLICIES
from runner.deadline import BrowserDeadline, browser_deadline
from runner.storage import write_json


class ActionParseError(ValueError):
    pass


FORMAT_REMINDER = 'Use exactly one ```action fenced block containing one valid action call.'
BID_REMINDER = 'Elements must be referenced by the bracketed numeric bid shown in the observation.'
PREFIX_REMINDER = 'send_msg_to_user must start with DONE: or NOT DONE:; reply with the corrected action.'


def parse_action(text, policy='B0', metadata=None, model=None):
    from agent.action_adapter import first_of_many, to_action_call
    from agent.llm import CONFIG
    from browsergym.core.action import functions
    from agent.env import report_verification
    allowed = ALLOWED_ACTIONS | ({'report_verification'} if policy in VRV_POLICIES else set())
    selected = None
    if (model == 'gpt-5.6-luna' and
            CONFIG['models'][model].get('action_adapter') == 'first_of_many'):
        selected = first_of_many(text)
        if selected is not None:
            text = f'```action\n{selected[0]}\n```'
            if metadata is not None:
                metadata.update(lenient_parse='first_of_many', n_actions_in_reply=selected[1])
    native = to_action_call(text)
    if native is not None:
        text = native
        if metadata is not None:
            metadata['native_format'] = True
    matches = list(re.finditer(r'```action\s*\n(.*?)```', text, re.DOTALL))
    if len(re.findall(r'```action(?=\s|$)', text)) > 1:
        raise ActionParseError('Reply with exactly one action per turn.')
    lenient = not matches
    if lenient and ('```' in text or len(text.strip().splitlines()) != 1):
        raise ActionParseError(FORMAT_REMINDER)
    block = text.strip() if lenient else matches[0].group(1).strip()
    try:
        nodes = ast.parse(block).body
        if len(nodes) != 1 or not isinstance(nodes[0], ast.Expr):
            raise ValueError
        call = nodes[0].value
        if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name)
                or call.func.id not in allowed or ast.get_source_segment(block, call) != block):
            raise ValueError
        args = [ast.literal_eval(arg) for arg in call.args]
        if any(kw.arg is None for kw in call.keywords):
            raise ValueError
        kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
        if len(kwargs) != len(call.keywords):
            raise ValueError
        function = report_verification if call.func.id == 'report_verification' else getattr(functions, call.func.id)
        bound = inspect.signature(function).bind(*args, **kwargs)
    except (SyntaxError, ValueError, TypeError):
        raise ActionParseError(FORMAT_REMINDER) from None
    if 'bid' in bound.arguments:
        bid = bound.arguments['bid']
        if not isinstance(bid, str) or not re.fullmatch(r'[0-9]+', bid):
            raise ActionParseError(BID_REMINDER)
    if call.func.id in ('send_msg_to_user', 'report_infeasible'):
        if not isinstance(next(iter(bound.arguments.values())), str):
            raise ActionParseError(FORMAT_REMINDER)
    if metadata is not None:
        metadata['lenient_parse'] = 'first_of_many' if selected else lenient and native is None
    return block


def final_message(action):
    call = ast.parse(action).body[0].value
    if call.func.id not in ('send_msg_to_user', 'report_infeasible'):
        return None
    values = [ast.literal_eval(x) for x in call.args]
    keywords = {k.arg: ast.literal_eval(k.value) for k in call.keywords}
    value = values[0] if values else keywords.get('text', keywords.get('reason'))
    if not isinstance(value, str):
        return None
    return 'NOT DONE: ' + value if call.func.id == 'report_infeasible' else value


def claim_from_message(message):
    if re.match(r'^NOT DONE\s*:', message.strip()):
        return 'NOT DONE'
    if re.match(r'^DONE\s*:', message.strip()):
        return 'DONE'
    return 'NONE'


def run_agent(env, client, policy, seed, max_steps, trace, state, oracle_fn=None):
    controller = Policy(policy, oracle_fn)
    state.update(controller.summary)
    system = SYSTEM_PROMPT + '\n\nAvailable actions:\n' + action_set(policy).describe()
    if policy in VRV_POLICIES:
        system += '\n' + REPORT_DESCRIPTION
    with browser_deadline():
        obs, _ = env.reset(seed=seed)
    if hasattr(client, 'env'):
        client.env = env
    task = getattr(env, 'task_kwargs', {}).get('task', {})
    site_url = task.get('start_url', observation(obs)['url'])
    context = getattr(env, 'context', None)
    if context is None and (controller.enforced or policy == 'Oracle'):
        raise ValueError(f'{policy} requires a browser context with request/response events')
    entry = None
    if controller.enforced:
        if 'family' in task:
            from proxy.catalog import load_catalog
            from agent.browser import ROOT
            catalog = load_catalog(ROOT / f"catalog/{task['site']}.yaml")
            entry = next(e for e in catalog.entries if e['id'] == task['family'])
        else:
            entry = task.get('policy_entry')
        controller.configure(task, entry)
    network = ActionNetwork(context, site_url, entry=entry, track_write_time=policy == 'Oracle')
    try:
        _run_steps(env, client, controller, system, obs, max_steps, trace, state, network)
    finally:
        state.update(controller.summary)
        network.close()


def _run_steps(env, client, controller, system, obs, max_steps, trace, state, network):
    history = []
    last_error = ''
    last_action = ''
    prefix_corrections = 0
    for step in range(1, max_steps + 1):
        current = observation(obs)
        if last_action:
            current['last_error'] = last_error
        current['last_action'] = last_action or current['last_action']
        prompt = controller.decorate(json.dumps({**current, 'history': history[-8:]}, ensure_ascii=False))
        started = time.monotonic()
        row = dict(provider=state.get('provider', 'opencode'), started_at=time.time(), step=step, url=current['url'], ax_chars=len(current['ax']),
                   prompt_chars=len(system) + len(prompt), prompt_tokens=0,
                   usage=ZERO_USAGE.copy(), cost=0, raw_model_text='', action='', error='',
                   side_effecting=False, mutating_requests=[], policy_event=[], extra_llm_calls=[],
                   lenient_parse=False, native_format=False)
        state['steps'] = step

        def account(reply):
            for key in state['tokens']:
                state['tokens'][key] += reply.usage[key]
            state['cost'] += reply.cost

        def extra_call(kind, instruction):
            extra_prompt = json.dumps({**observation(obs), 'last_action': last_action,
                'last_error': row['error'], 'history': history[-8:]}, ensure_ascii=False) + '\n' + instruction
            reply = client.complete(system, extra_prompt)
            account(reply)
            controller.summary['extra_llm_calls'] += 1
            controller.summary['policy_cost'] += reply.cost
            row['extra_llm_calls'].append(dict(kind=kind, prompt=extra_prompt, usage=reply.usage,
                cost=reply.cost, raw_model_text=reply.text, finish_reason=reply.finish_reason))
            # Per-step totals include auxiliary calls; individual call costs remain
            # available separately for auditing against the episode/budget totals.
            for key in row['usage']:
                row['usage'][key] += reply.usage[key]
            row['cost'] += reply.cost
            row['prompt_tokens'] += reply.usage['input']
            row['prompt_chars'] += len(system) + len(extra_prompt)
            return reply

        try:
            reply = client.complete(system, prompt)
            row.update(raw_model_text=reply.text, usage=reply.usage.copy(), cost=reply.cost,
                       prompt_tokens=reply.usage['input'], finish_reason=reply.finish_reason)
            row['action_llm_call'] = dict(usage=reply.usage.copy(), cost=reply.cost)
            account(reply)
            try:
                action = parse_action(reply.text, controller.name, row, model=getattr(client, 'model', None))
            except ActionParseError as exc:
                action = 'noop()'
                row['error'] = str(exc)
            row['action'] = last_action = action
            message = final_message(action)
            # Invalid replies execute nothing, including no fallback browser noop.
            interception = row['error'] or controller.before_action(action, current, row)
            prefix_exhausted = False
            if message is not None and claim_from_message(message) == 'NONE':
                prefix_corrections += 1
                interception = PREFIX_REMINDER
                prefix_exhausted = prefix_corrections > 2
                row['prefix_corrections'] = min(prefix_corrections, 2)
                row['prefix_corrections_exhausted'] = prefix_exhausted
                if prefix_exhausted:
                    interception = 'Final message prefix correction limit reached; ending with NONE.'
            if message is not None and claim_from_message(message) == 'DONE':
                error = controller.before_done(row)
                if error:
                    interception = error
            row['action_started_at'] = time.time()
            if hasattr(trace, 'name'):
                from pathlib import Path
                write_json(Path(trace.name).with_name('current_step.json'),
                           dict(step=step, action=action, started_at=row['action_started_at']))
            terminated = truncated = False
            action_failure = None
            network.begin()
            try:
                if interception is None:
                    with browser_deadline():
                        obs, _, terminated, truncated, _ = env.step(action)
                    row['error'] = row['error'] or obs.get('last_action_error', '')
                else:
                    row['error'] = interception
            except (Exception, BrowserDeadline) as exc:
                # The server may have committed before a browser/CDP failure.
                # Retain its event evidence and run the post-action policy hook.
                action_failure = exc
                row['error'] = redact(f'{type(exc).__name__}: {exc}', getattr(client, 'key', ''))
            finally:
                evidence = network.end()
                row.update(evidence)
            last_error = row['error']
            history.append((reply.text.split('```action')[0].strip(), action))
            controller.after_action(evidence, observation(obs), row, extra_call)
            if controller.name == 'B2':
                reflection = extra_call('reflection', REFLECTION_PROMPT +
                                        ' For this auxiliary call, answer in plain text, not an action.')
                thought, previous_action = history[-1]
                history[-1] = (thought + '\nReflection: ' + reflection.text, previous_action)
            if action_failure is not None:
                raise action_failure
            if not row['error'] and interception is None and message is not None:
                state['final_message_at'] = row['action_started_at']
                state['final_message_observed_at'] = time.time()
                state['final_message'] = message
                state['claim'] = claim_from_message(message)
                break
            if prefix_exhausted:
                state['claim'] = 'NONE'
                state['final_message'] = message
                break
            if terminated or truncated:
                break
        except (Exception, BrowserDeadline) as exc:
            row['error'] = redact(f'{type(exc).__name__}: {exc}', getattr(client, 'key', ''))
            raise
        finally:
            state.update(controller.summary)
            row['ended_at'] = time.time()
            row['seconds'] = time.monotonic() - started
            trace.write(redact(json.dumps(row, ensure_ascii=False), getattr(client, 'key', '')) + '\n')
            trace.flush()
