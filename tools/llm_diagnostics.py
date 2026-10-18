"""Redact provider diagnostics before truncating or publishing them."""
import json
import re


SENSITIVE = re.compile(
    r'(?i)headers?|authorization|proxy-authorization|x-api-key|api[_-]?key|'
    r'token|access_token|password|secret|cookie|set-cookie|'
    r'user-agent|x-opencode-session|anthropic-version')


def redact_error_body(value, key):
    """Remove echoed credentials and header fields, including nested JSON."""
    def safe_text(text):
        if key:
            for secret in (key, json.dumps(key)[1:-1]):
                text = text.replace(secret, '[REDACTED]')
        text = re.sub(r'\b(?:oc_|sk-)[A-Za-z0-9_-]+', '[REDACTED]', text)
        # Withhold the rest of an inline header/credential assignment's line.
        return re.sub(r'(?i)(["\']?(?:' + SENSITIVE.pattern.removeprefix('(?i)') +
                      r')["\']?\s*[:=]\s*)[^\n\r]+', r'\1[REDACTED]', text)

    def scrub(item):
        if isinstance(item, dict):
            return {safe_text(name): '[REDACTED]' if SENSITIVE.fullmatch(name) else scrub(content)
                    for name, content in item.items()}
        if isinstance(item, list):
            return [scrub(content) for content in item]
        if isinstance(item, str):
            return safe_text(item)
        return item

    try:
        return json.dumps(scrub(json.loads(value)), ensure_ascii=False)
    except ValueError:
        return safe_text(value)


def provider_reason(body):
    """Only short structured identifiers may enter exception messages."""
    try:
        data = json.loads(body)
    except ValueError:
        return ''
    if not isinstance(data, dict):
        return ''
    error = data.get('error', data)
    if not isinstance(error, dict):
        return ''
    return ', '.join(f'{name}={value}' for name in ('type', 'code')
                     if isinstance(value := error.get(name), (str, int))
                     and re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', str(value)))
