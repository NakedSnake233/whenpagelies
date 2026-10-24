"""Explicit live LMUAI probe: python -m tools.provider_probe (never run by setup)."""
import json
import math
import re
import time
import uuid

import httpx

from agent.llm import CONFIG, LLMClient, redact
from runner.budget import Budget
from tools.providers import provider_name
from tools.secrets import load_lmuai_settings
from tools.llm_diagnostics import redact_error_body


def numeric_headers(headers):
    result = {}
    for name, value in headers.items():
        if not re.fullmatch(r'(?:x-)?(?:cost|request-cost|usage-cost|ratelimit-[a-z-]+|rate-limit-[a-z-]+)|retry-after', name.lower()):
            continue
        try:
            number = float(value)
        except ValueError:
            continue
        if math.isfinite(number):
            result[name.lower()] = number
    return result


def probe(transport=None, budget=None, identity=False):
    if provider_name() != 'lmuai':
        raise ValueError('Set OSD_PROVIDER=lmuai to probe')
    models = CONFIG['providers']['lmuai']['models']
    settings = [load_lmuai_settings(c['key_env']) for c in models.values()]
    keys = [s.get('key') for s in settings]

    def safe_text(value):
        for key in keys:
            value = redact_error_body(str(value), key)
        return re.sub(r'\b[A-Za-z0-9_-]{32,}\b', '[REDACTED]', value)

    def emit(row):
        def scrub(value):
            if isinstance(value, str):
                return safe_text(value)
            if isinstance(value, dict):
                return {safe_text(k): scrub(v) for k, v in value.items()}
            if isinstance(value, list):
                return [scrub(v) for v in value]
            return value
        raw = json.dumps(scrub(dict(provider='lmuai', **row)), allow_nan=False)
        for key in keys:
            raw = redact(raw, key)
        print(raw, flush=True)

    # Use the first model credential for discovery; each completion uses its own.
    with httpx.Client(transport=transport, timeout=30) as client:
        started = time.monotonic()
        try:
            response = client.get(settings[0].get('base_url') + '/models',
                                  headers={'Authorization': 'Bearer ' + keys[0]})
            ids = []
            all_ids = []
            metadata = []
            if response.is_success:
                items = [item for item in response.json().get('data', [])
                         if isinstance(item, dict) and isinstance(item.get('id'), str)]
                all_ids = [item['id'] for item in items]
                matching = [item for item in items if re.search('deepseek|qwen|mimo|minimax', item['id'], re.I)]
                ids = [item['id'] for item in matching]
                metadata = [{k: v for k, v in item.items() if k in (
                    'id', 'owned_by', 'created', 'context_length', 'context_window',
                    'max_context_length', 'pricing')} for item in matching]
            emit(dict(operation='models', status=response.status_code, ids=ids,
                      total_ids=len(all_ids), metadata=metadata,
                      error=None if response.is_success else safe_text(response.text),
                      latency_s=time.monotonic()-started, headers=numeric_headers(response.headers)))
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            emit(dict(operation='models', status='failed', latency_s=time.monotonic()-started))
    budget = budget if budget is not None else Budget()
    calls = [(model, False) for model in models]
    if identity:
        calls.append(('deepseek-v4.1-flash', True))
    for model, is_identity in calls:
        episode = 'provider-probe-' + uuid.uuid4().hex
        client = LLMClient(model, episode, budget=budget, transport=transport,
                           max_tokens=40 if is_identity else 16, max_retries=0, record_error_body=False)
        observed = {}

        def observe(response):
            observed.update(status=response.status_code, headers=numeric_headers(response.headers))
            response.read()
            if response.is_success:
                try:
                    data = response.json()
                    returned = data.get('model')
                    observed['returned_model'] = returned if isinstance(returned, str) else None
                    observed['system_fingerprint'] = data.get('system_fingerprint')
                    observed['raw_usage'] = data.get('usage')
                except (ValueError, AttributeError):
                    pass
            else:
                observed['error'] = safe_text(response.text)

        client.client.event_hooks['response'].append(observe)
        started = time.monotonic()
        try:
            prompt = ('Which model and version are you? Answer in one short line.'
                      if is_identity else 'Say OK.')
            reply = client.complete('Answer briefly.', prompt)
            reply_text = safe_text(reply.text)
            observed.update(usage=reply.usage, estimated_cost_usd=reply.cost,
                            reply=reply_text if is_identity else reply_text[:40])
        except Exception as exc:
            # Transport/parse failures retain the conservative budget reservation.
            observed.setdefault('status', 'failed')
            observed.setdefault('error', safe_text(str(exc)))
        finally:
            client.close()
        elapsed = time.monotonic()-started
        with budget.connect() as db:
            observed['ledger'] = [dict(cost_usd=cost, status=status) for cost, status in db.execute(
                'SELECT cost,status FROM calls WHERE episode=?', (episode,))]
        emit(dict(operation='identity' if is_identity else 'completion', model=model,
                  latency_s=elapsed, **observed))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identity', action='store_true', help='Also ask DeepSeek to identify itself')
    args = parser.parse_args()
    try:
        probe(identity=args.identity)
    except Exception:
        raise SystemExit('Provider probe failed; check configuration and credentials.') from None


if __name__ == '__main__':
    main()
