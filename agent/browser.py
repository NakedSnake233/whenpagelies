"""Repository-local Chromium settings and BrowserGym launch compatibility."""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl, urlencode
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
# Set before Playwright starts its driver. The API client retains internet proxies.
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / '.cache/ms-playwright')
os.environ['XDG_CACHE_HOME'] = str(ROOT / '.cache')
os.environ['TMPDIR'] = str(ROOT / '.tmp')
(ROOT / '.tmp').mkdir(exist_ok=True)

from browsergym.core import _get_global_playwright
from browsergym.core.env import BrowserEnv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

AUTO_LOGIN = {
    'shopping_admin': ('http://localhost:7780', {'X-M2-Admin-Auto-Login': 'admin:admin1234'}),
    'reddit': ('http://127.0.0.1:9999', {'X-Postmill-Auto-Login': 'MarvelsGrantMan136:test1234'}),
    # Storefront header discovery and provenance are documented in config/README.md.
    'shopping': ('http://localhost:7770', {}),
}


def browser_environment():
    return {k: v for k, v in os.environ.items()
            if k.lower() not in {'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'}
            and not any(word in k.upper() for word in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD'))}


def wait_for_observation(page):
    """Let Magento finish rendering, with one shared ten-second wait budget."""
    if origin(page.url) != origin(AUTO_LOGIN['shopping_admin'][0]):
        return
    deadline = time.monotonic() + 10

    def remaining():
        return max(1, int((deadline - time.monotonic()) * 1000))

    try:
        page.wait_for_load_state('networkidle', timeout=remaining())
        if time.monotonic() >= deadline:
            return
        page.wait_for_function("""() => {
            const visible = e => e.checkVisibility({checkVisibilityCSS: true, checkOpacity: true});
            if ([...document.querySelectorAll('.loading-mask, [data-role="spinner"]')].some(visible))
                return false;
            // Knockout may insert the form after the document's load event.
            return !location.pathname.includes('/catalog/product/edit/') ||
                [...document.querySelectorAll('input[name="product[sku]"]')].some(visible);
        }""", timeout=remaining())
    except PlaywrightTimeoutError:
        # Error pages and long-lived traffic must still produce an observation.
        pass


RENDERED_FORM_CONTROLS = """() => Object.fromEntries(
    [...document.querySelectorAll('input[bid], select[bid], textarea[bid]')]
    .filter(e => e.type !== 'hidden' && e.getClientRects().length &&
        e.checkVisibility({checkVisibilityCSS: true, checkOpacity: true}))
    .map(e => [e.getAttribute('bid'), [...(e.labels || [])]
        .map(l => l.innerText.trim()).filter(Boolean).join(' ').replace(/\\s+/g, ' ')]))"""


class LocalBrowserEnv(BrowserEnv):
    """0.14.3 supplies args explicitly; merge our flag at the launch boundary.

    The temporary patch is local to reset; use one synchronous environment per worker.
    """
    def _get_obs(self):
        wait_for_observation(self.page)
        obs = super()._get_obs()
        obs['rendered_form_controls'] = self.page.evaluate(RENDERED_FORM_CONTROLS)
        return obs

    def reset(self, *args, **kwargs):
        chromium = _get_global_playwright().chromium
        launch = chromium.launch

        def local_launch(*a, **kw):
            kw['args'] = [*kw.get('args', []), '--proxy-bypass-list=<-loopback>']
            kw['env'] = browser_environment()
            return launch(*a, **kw)

        with patch.object(chromium, 'launch', local_launch):
            return super().reset(*args, **kwargs)


def origin(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


def visible_url(url):
    """Keep navigable URL evidence without credentials or common auth parameters."""
    parsed = urlsplit(url)
    netloc = parsed.netloc.rsplit('@', 1)[-1]
    query = urlencode([(k, '[REDACTED]' if any(word in k.lower() for word in
                       ('token', 'password', 'secret', 'api_key', 'form_key')) else v)
                       for k, v in parse_qsl(parsed.query, keep_blank_values=True)])
    return parsed._replace(netloc=netloc, query=query).geturl()


def graphql_mutation(request):
    """Inspect only browser-visible query text; never retain request bodies."""
    params = dict(parse_qsl(urlsplit(request.url).query))
    operations = [(params.get('query', ''), params.get('operationName'))]
    body = getattr(request, 'post_data', None)
    if body:
        try:
            payload = json.loads(body)
            for operation in payload if isinstance(payload, list) else [payload]:
                if isinstance(operation, dict):
                    operations.append((operation.get('query', ''), operation.get('operationName')))
        except (ValueError, TypeError):
            operations.append((body, None))
    for query, selected in operations:
        if not isinstance(query, str):
            continue
        # Ignore quoted values and comments containing the word "mutation".
        query = re.sub(r'""".*?"""|"(?:\\.|[^"\\])*"|#[^\n]*', '', query, flags=re.DOTALL)
        start = depth = parentheses = 0
        for index, char in enumerate(query):
            if char == '(':
                parentheses += 1
            elif char == ')':
                parentheses -= 1
            elif char == '{' and parentheses == 0:
                if depth == 0:
                    header = re.match(r'\s*(query|mutation|subscription|fragment)\b(?:\s+([A-Za-z_]\w*))?',
                                      query[start:index])
                    if header and header[1] == 'mutation' and (not selected or header[2] == selected):
                        return True
                depth += 1
            elif char == '}' and parentheses == 0:
                depth -= 1
                if depth == 0:
                    start = index + 1
    return False


class ActionNetwork:
    """Per-action evidence from Playwright context events, across all tabs.

    Requests must start during the action. Late responses from older actions do
    not get attributed to a new action. No headers, bodies, HAR, or proxy inputs.
    """
    def __init__(self, context, site_url, entry=None, track_write_time=False):
        self.entry = entry
        self.track_write_time = track_write_time
        self.context = context
        self.site_origin = origin(site_url)
        self.active = False
        self.requests = {}
        if context is not None:
            context.on('request', self.request)
            context.on('response', self.response)

    def begin(self):
        self.requests = {}
        self.active = True

    def request(self, request):
        if not self.active or origin(request.url) != self.site_origin:
            return
        method = request.method.upper()
        document = request.resource_type == 'document'
        if document:
            try:
                document = request.frame == request.frame.page.main_frame
            except Exception:
                document = False
        self.requests[request] = dict(method=method, url=visible_url(request.url), status=None,
            mutating=method in {'POST', 'PUT', 'PATCH', 'DELETE'} or graphql_mutation(request),
            document=document)

        if self.track_write_time:
            self.requests[request]['request_ts'] = time.monotonic()
        if self.entry is not None:
            parsed = urlsplit(request.url)
            target = parsed.path + ('?' + parsed.query if parsed.query else '')
            self.requests[request].update(
                matched_write=(method == self.entry['method']
                    and bool(re.search(self.entry['url_regex'], target))
                    and ('body_regex' not in self.entry or bool(re.search(
                        self.entry['body_regex'], getattr(request, 'post_data', None) or '')))),
                request_ts=time.monotonic(),
                site_redirect=getattr(request, 'redirected_from', None) is not None)

    def response(self, response):
        if self.active and response.request in self.requests:
            self.requests[response.request]['status'] = response.status

    def end(self):
        self.active = False
        requests = list(self.requests.values())
        mutating = [{k: r[k] for k in ('method', 'url', 'status')} for r in requests if r['mutating']]
        documents = [r for r in requests if r['document'] and r['status'] is not None]
        # A form POST can redirect to the final document; an AJAX write's own
        # response is its main response, but it still has no document response.
        main_status = documents[-1]['status'] if documents else (mutating[-1]['status'] if mutating else None)
        # Loading an error page must not hide a failed write response.
        failed_writes = [r['status'] for r in mutating if r['status'] is not None and r['status'] >= 400]
        if failed_writes:
            main_status = failed_writes[-1]
        result = dict(side_effecting=bool(mutating), mutating_requests=mutating,
                    document_responses=documents, main_response_status=main_status)

        if self.track_write_time:
            result['first_write_ts'] = min((r['request_ts'] for r in requests if r['mutating']), default=None)
        if self.entry is not None:
            result['matched_writes'] = [r for r in requests if r['matched_write']]
        return result

    def close(self):
        self.active = False
        if self.context is not None:
            self.context.remove_listener('request', self.request)
            self.context.remove_listener('response', self.response)


def scrub_har(path, response_keys=()):
    """Remove authentication headers/cookies from the HAR after context flush."""
    path = Path(path)
    if not path.exists():
        return
    data = json.loads(path.read_text())
    for entry in data.get('log', {}).get('entries', []):
        for side in ('request', 'response'):
            obj = entry.get(side, {})
            obj['cookies'] = []
            if side == 'request' and obj.get('postData'):
                post = obj['postData']
                def sensitive(name):
                    return name.lower() in {'token', 'form_key', 'password', 'api_key', 'csrf_token'} or name.lower().endswith('[_token]')
                for param in post.get('params', []):
                    if sensitive(param.get('name', '')):
                        param['value'] = '[REDACTED]'
                if 'x-www-form-urlencoded' in post.get('mimeType', '') and 'text' in post:
                    post['text'] = urlencode([(k, '[REDACTED]' if sensitive(k) else v)
                                              for k, v in parse_qsl(post['text'], keep_blank_values=True)])
                elif 'multipart/form-data' in post.get('mimeType', '') and 'text' in post:
                    pattern = r'(Content-Disposition:[^\r\n]*?;\s*name="([^"]+)"[^\r\n]*\r?\n(?:[^\r\n]+\r?\n)*\r?\n)(.*?)(?=\r?\n--)'
                    post['text'] = re.sub(pattern,
                        lambda m: m[1] + ('[REDACTED]' if sensitive(m[2]) else m[3]),
                        post['text'], flags=re.DOTALL | re.IGNORECASE)
            if side == 'response':
                content = obj.get('content', {})
                raw = content.pop('text', None)
                if raw and response_keys and 'json' in content.get('mimeType', ''):
                    try:
                        body = json.loads(raw)
                        kept = {k: body[k] for k in response_keys if k in body}
                        if kept:
                            content['text'] = json.dumps(kept)
                    except (ValueError, TypeError):
                        pass
            for header in obj.get('headers', []):
                if header['name'].lower() in ('cookie', 'set-cookie', 'authorization', 'x-api-key') or 'auto-login' in header['name'].lower():
                    header['value'] = '[REDACTED]'
    path.write_text(json.dumps(data))
    path.chmod(0o600)
