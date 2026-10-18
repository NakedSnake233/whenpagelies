"""Standard-library smoke test; run with python3 -m tools.smoke_llm."""
import json
import time
import urllib.error
import urllib.request

try:
    from tools.secrets import load_opencode_key
except ModuleNotFoundError:
    from secrets import load_opencode_key

BASE_URL = 'https://opencode.ai/zen/go/v1'
MODELS = ('deepseek-v4.1-flash', 'mimo-v2.6-flash', 'minimax-m3', 'qwen3.7-plus')


def main():
    try:
        key = load_opencode_key()
    except RuntimeError as exc:
        print(str(exc), flush=True)
        return 1
    session = f'smoke-{int(time.time())}'
    headers = {'Authorization': f'Bearer {key}', 'User-Agent': 'osd-bench-agent/0.1',
               'x-opencode-session': session, 'Content-Type': 'application/json'}

    def safe(value):
        text = str(value)
        for secret in (key, json.dumps(key)[1:-1]):
            text = text.replace(secret, '[REDACTED]')
        return text

    def call(path, label, body=None, extra=None):
        start = time.monotonic()
        request = urllib.request.Request(BASE_URL + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={**headers, **(extra or {})})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.status
                raw = response.read().decode('utf-8', errors='replace')
            data = json.loads(raw)
            elapsed = time.monotonic() - start
            if path == '/models':
                models = data.get('data', [])
                ids = {model.get('id') for model in models}
                print(f'GET /models HTTP {status} models={len(models)}', flush=True)
                for model in MODELS:
                    print(f'  {model}: listed={model in ids}', flush=True)
            else:
                if path == '/messages':
                    reply = ''.join(block.get('text', '') for block in data.get('content', []) if block.get('type') == 'text')
                else:
                    choices = data.get('choices', [])
                    reply = choices[0].get('message', {}).get('content', '') if choices else ''
                print(f'{label} HTTP {status} latency={elapsed:.2f}s reply={safe(reply)[:40]!r} usage={safe(json.dumps(data.get("usage", {})))}', flush=True)
            return True
        except urllib.error.HTTPError as exc:
            error = safe(exc.read().decode('utf-8', errors='replace'))[:200]
            print(f"{label} HTTP {exc.code} latency={time.monotonic()-start:.2f}s reply='' usage={{}} error={error}", flush=True)
        except Exception as exc:
            print(f"{label} HTTP unavailable latency={time.monotonic()-start:.2f}s reply='' usage={{}} error={safe(exc)[:200]}", flush=True)
        return False

    successful = True
    for model in MODELS:
        body = {'model': model, 'messages': [{'role': 'user', 'content': 'Reply with the single word: ready'}], 'max_tokens': 64}
        anthropic = model in ('qwen3.7-plus', 'minimax-m3')
        extra = {'x-api-key': key, 'anthropic-version': '2023-06-01'} if anthropic else None
        result = call('/messages' if anthropic else '/chat/completions', model, body, extra)
        successful = result and successful
    return 0 if successful else 1


if __name__ == '__main__':
    raise SystemExit(main())
