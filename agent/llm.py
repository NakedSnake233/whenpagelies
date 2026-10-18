"""OpenCode's chat, Anthropic messages, and Responses transports."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

import httpx
import yaml

from runner.budget import Budget
from runner.storage import write_json
from tools.llm_diagnostics import provider_reason, redact_error_body
from tools.secrets import load_opencode_key

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / 'config/models.yaml').read_text())
ZERO_USAGE = dict(input=0, cached_input=0, output=0, reasoning=0)


def rates(model, when=None):
    config = CONFIG['models'][model]
    when = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
    peak = when.weekday() < 5 and (1 <= when.hour < 4 or 6 <= when.hour < 10)
    return {**config, **(config.get('peak', {}) if peak else {})}


def cost_for(model, usage, when=None, cache_write=0):
    price = rates(model, when)
    cached = usage['cached_input']
    # Output already INCLUDES reasoning; never charge reasoning twice.
    uncached = max(0, usage['input'] - cached - cache_write)
    return (uncached * price['input'] + cached * price['cached'] +
            cache_write * price.get('cache_write', price['input']) +
            usage['output'] * price['output']) / 1_000_000


def redact(value, key=''):
    text = str(value)
    if key:
        for secret in (key, json.dumps(key)[1:-1]):
            text = text.replace(secret, '[REDACTED]')
    return re.sub(r'\b(?:oc_|sk-)[A-Za-z0-9_-]+', '[REDACTED]', text)


def monthly_limit(response):
    """Recognize the provider's structured account-wide quota rejection."""
    if response.status_code != 429:
        return False
    try:
        data = response.json()
    except ValueError:
        return False
    def fields(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key, item
                yield from fields(item)
        elif isinstance(value, list):
            for item in value:
                yield from fields(item)
    pairs = list(fields(data))
    return (any(value == 'GoUsageLimitError' for key, value in pairs) and
            any(key == 'limitName' and value == 'monthly' for key, value in pairs))


@dataclass
class Reply:
    text: str
    finish_reason: str = ''
    usage: dict = field(default_factory=lambda: ZERO_USAGE.copy())
    cost: float = 0.0
    cache_write: int = 0


def normalise(data, api_format):
    u = data.get('usage') or {}
    cache_write = 0
    if api_format == 'chat':
        choice = data['choices'][0]
        text = choice['message'].get('content') or ''
        if isinstance(text, list):
            text = ''.join(b.get('text', '') for b in text if isinstance(b, dict))
        finish = choice.get('finish_reason', '')
        inp = u.get('prompt_tokens', 0)
        cached = (u.get('prompt_tokens_details') or {}).get('cached_tokens', u.get('prompt_cache_hit_tokens', 0))
        output = u.get('completion_tokens', 0)
        reasoning = (u.get('completion_tokens_details') or {}).get('reasoning_tokens', u.get('reasoning_tokens', 0))
    elif api_format == 'messages':
        text = ''.join(b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text')
        finish = data.get('stop_reason', '')
        cached = u.get('cache_read_input_tokens', 0)
        cache_write = u.get('cache_creation_input_tokens', 0)
        inp = u.get('input_tokens', 0) + cached + cache_write
        output = u.get('output_tokens', 0)
        reasoning = u.get('reasoning_tokens', 0)
    else:
        text = ''.join(c.get('text', '') for item in data.get('output', [])
                       if item.get('type') == 'message' for c in item.get('content', [])
                       if c.get('type') == 'output_text')
        finish = (data.get('incomplete_details') or {}).get('reason', data.get('status', ''))
        inp, output = u.get('input_tokens', 0), u.get('output_tokens', 0)
        cached = (u.get('input_tokens_details') or {}).get('cached_tokens', 0)
        reasoning = (u.get('output_tokens_details') or {}).get('reasoning_tokens', 0)
    return Reply(text, finish, dict(input=inp, cached_input=cached, output=output,
                                   reasoning=reasoning), cache_write=cache_write)


class LLMClient:
    def __init__(self, model, episode_id, budget=None, transport=None, sleep=time.sleep,
                 max_retries=5, max_tokens=4096):
        self.model, self.episode_id = model, episode_id
        self.config = CONFIG['models'][model]
        self.key = load_opencode_key()
        self.budget = budget if budget is not None else Budget(ROOT / 'runs/budget.sqlite')
        self.client = httpx.Client(trust_env=True, timeout=180, transport=transport)
        self.sleep, self.max_retries, self.max_tokens = sleep, max_retries, max_tokens

    def close(self):
        self.client.close()

    def complete(self, system, prompt):
        fmt = self.config['format']
        headers = {'Authorization': f'Bearer {self.key}', 'User-Agent': 'osd-bench-agent/0.1',
                   'x-opencode-session': self.episode_id}
        body = {'model': self.model}
        if fmt == 'messages':
            path = '/messages'
            headers.update({'x-api-key': self.key, 'anthropic-version': '2023-06-01'})
            body.update(system=system, messages=[{'role': 'user', 'content': prompt}],
                        max_tokens=self.max_tokens, temperature=0)
        elif fmt == 'responses':
            path = '/responses'
            body.update(instructions=system, input=prompt, max_output_tokens=self.max_tokens)
            # Reasoning GPT models do not accept temperature=0.
        else:
            path = '/chat/completions'
            body.update(messages=[{'role': 'system', 'content': system},
                                  {'role': 'user', 'content': prompt}],
                        max_tokens=self.max_tokens, temperature=0)
        # UTF-8 bytes conservatively bound prompt tokens; include framing overhead.
        estimate_input = len(json.dumps(body, ensure_ascii=False).encode()) + 1024
        price = {**self.config, **self.config.get('peak', {})}
        estimate = (estimate_input * price['input'] + self.max_tokens * price['output']) / 1e6
        for attempt in range(self.max_retries + 1):
            reservation = self.budget.reserve(self.model, self.config['monthly_cap'], estimate,
                                              self.episode_id)
            when = datetime.now(timezone.utc)
            try:
                response = self.client.post(CONFIG['base_url'] + path, headers=headers, json=body)
            except (httpx.TimeoutException, httpx.TransportError):
                # Unknown provider outcome: keep the reservation to fail conservatively.
                if attempt == self.max_retries:
                    raise RuntimeError('LLM transport failed; uncertain charge remains reserved') from None
                self.sleep(min(2**attempt, 30))
                continue
            if response.status_code >= 400:
                self.budget.settle(reservation, 0, 'http_error')
                if monthly_limit(response):
                    self.budget.pause()
                    raise RuntimeError('LLM HTTP 429: GoUsageLimitError monthly; workspace paused')
                if (response.status_code == 429 or response.status_code >= 500) and attempt < self.max_retries:
                    self.sleep(min(2**attempt, 30))
                    continue
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    safe_body = redact_error_body(response.text, self.key)
                    reason = provider_reason(safe_body) or 'provider error (no type/code)'
                    directory = ROOT / 'runs' / self.episode_id
                    directory.mkdir(parents=True, exist_ok=True)
                    write_json(directory / 'llm_error.json', {
                        'status': response.status_code,
                        'body': safe_body[:500],
                        'prompt_chars': len(system) + len(prompt),
                        'prompt_utf8_bytes': len(system.encode()) + len(prompt.encode()),
                    })
                    raise RuntimeError(f'LLM HTTP {response.status_code}: {reason}')
                raise RuntimeError(f'LLM HTTP {response.status_code}')
            try:
                reply = normalise(response.json(), fmt)
                reply.text = redact(reply.text, self.key)
                reply.cost = cost_for(self.model, reply.usage, when, reply.cache_write)
            except (ValueError, KeyError, TypeError, IndexError):
                raise RuntimeError('Invalid LLM response; uncertain charge remains reserved') from None
            self.budget.settle(reservation, reply.cost)
            return reply


class ScriptedLLM:
    """A JSON list of action strings, optionally using a visible role/name target.

    {"action": "fill", "role": "textbox", "name": "Biography", "value": "..."}
    resolves the current BrowserGym bid; it still executes only a high-level action.
    """
    def __init__(self, actions):
        self.actions = iter(actions)
        self.env = None

    def complete(self, system, prompt):
        item = next(self.actions)
        if isinstance(item, dict):
            name = item['action']
            if name not in ('fill', 'click'):
                raise ValueError('script target action must be fill or click')
            bid = self.env.page.get_by_role(item['role'], name=item['name'], exact=True).get_attribute('bid')
            if not bid:
                raise ValueError('script target has no BrowserGym bid')
            args = [bid] + ([item['value']] if name == 'fill' else [])
            item = f'{name}(' + ', '.join(repr(x) for x in args) + ')'
        return Reply(item if '```action' in item else f'```action\n{item}\n```')

    def close(self):
        pass
