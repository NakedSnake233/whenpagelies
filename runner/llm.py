"""Runner transport diagnostics, kept separate from the agent-owned client."""
from email.utils import parsedate_to_datetime
import fcntl
import json
import math
from pathlib import Path
import re
import time

from agent.llm import LLMClient as AgentLLMClient, redact

ROOT = Path(__file__).resolve().parents[1]


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after=None):
        super().__init__('LLM HTTP 429: retries exhausted')
        self.retry_after = retry_after


def retry_seconds(value, now=None):
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - (time.time() if now is None else now)
        except (ValueError, TypeError, OverflowError):
            return None
    return max(1, seconds) if math.isfinite(seconds) else None


def safe_diagnostic(value, key):
    text = redact(value, key)
    # Providers sometimes echo a header or credential field in an error body.
    return re.sub(r'(?i)(["\']?(?:authorization|x-api-key|api[_-]?key|token|password|secret)["\']?\s*[:=]\s*)[^,\n}\r]+',
                  r'\1[REDACTED]', text)


class LLMClient(AgentLLMClient):
    def __init__(self, *args, diagnostics=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostics = Path(diagnostics) if diagnostics is not None else ROOT / 'logs/llm_429.jsonl'
        self.rate_limit_retry = None
        self.client.event_hooks['response'].append(self._response)

    def _response(self, response):
        if response.status_code != 429:
            return
        response.read()
        self.rate_limit_retry = retry_seconds(response.headers.get('retry-after'))
        headers = {k: safe_diagnostic(v, self.key) for k, v in response.headers.items()
                   if k.lower() == 'retry-after' or re.fullmatch(r'(?:x-)?ratelimit-[a-z0-9-]+', k.lower())
                   or re.fullmatch(r'x-rate-limit-[a-z0-9-]+', k.lower())}
        record = dict(timestamp=time.time(), episode=self.episode_id, model=self.model,
                      status=429, headers=headers, body=safe_diagnostic(response.text, self.key)[:300],
                      retry_after=self.rate_limit_retry)
        self.diagnostics.parent.mkdir(parents=True, exist_ok=True)
        with self.diagnostics.open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.write(json.dumps(record) + '\n')
            stream.flush()
        self.diagnostics.chmod(0o600)

    def complete(self, *args, **kwargs):
        self.rate_limit_retry = None
        try:
            return super().complete(*args, **kwargs)
        except RuntimeError as exc:
            if str(exc) == 'LLM HTTP 429':
                raise RateLimitExceeded(self.rate_limit_retry) from None
            raise
