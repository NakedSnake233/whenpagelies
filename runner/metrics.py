"""Derive episode evidence only from timestamped proxy and agent artifacts."""
from proxy.plan import F3_DELAYS
import ast
from datetime import datetime
import json
import re
import time
from urllib.parse import urlsplit, urljoin


from runner.readback import readback_views, agent_readback_views


NAV_ACTIONS = {'goto', 'click', 'go_back', 'go_forward', 'reload', 'tab_focus', 'new_tab'}


def action_name(step):
    try:
        node = ast.parse(step.get('action', '')).body[0].value
        return node.func.id if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) else None
    except (SyntaxError, IndexError, AttributeError):
        return None


def action_step(steps, timestamp):
    return next((s for s in steps if
                 s.get('action_started_at', s.get('started_at', float('inf'))) <= timestamp
                 <= s.get('ended_at', 0)), None)


def har_requests(directory):
    path = directory / 'network.har'
    if not path.exists():
        return []
    try:
        entries = json.loads(path.read_text()).get('log', {}).get('entries', [])
    except (ValueError, OSError):
        return []
    result = []
    for entry in entries:
        request, response = entry.get('request', {}), entry.get('response', {})
        try:
            ts = datetime.fromisoformat(entry['startedDateTime'].replace('Z', '+00:00')).timestamp()
        except (KeyError, ValueError):
            continue
        headers = {h['name'].lower(): h['value'] for h in request.get('headers', [])}
        # Older HARs omit resourceType/Sec-Fetch. Chromium's navigation-only
        # Upgrade-Insecure-Requests plus HTML Accept distinguishes them from AJAX.
        if entry.get('_resourceType'):
            document = entry['_resourceType'] == 'document'
        elif headers.get('sec-fetch-dest'):
            document = headers['sec-fetch-dest'] == 'document'
        else:
            document = headers.get('upgrade-insecure-requests') == '1' and 'text/html' in headers.get('accept', '')
        result.append(dict(url=request.get('url'), method=request.get('method'), ts=ts,
                           document_navigation=document, redirect_url=response.get('redirectURL')))
    return sorted(result, key=lambda r: r['ts'])


def agent_readbacks(directory, steps, writes, reads, cutoff):
    if not writes:
        return []
    write_time = min(r.get('request_ts', r['ts']) for r in writes)
    first = action_step(steps, write_time)
    if first is None:
        return []
    har = har_requests(directory)
    if not har:
        har = sorted((dict(r, ts=r.get('request_ts', r['ts']))
                      for r in rows(directory / 'proxy.jsonl')), key=lambda r: r['ts'])
    # Mark every descendant of a POST/PUT/etc redirect as part of that write,
    # even when a slow redirect arrives during a later agent step.
    pending, redirected_writes = [], set()
    for i, req in enumerate(har):
        parents = [(j, write) for j, url, write in pending if url == req['url']]
        parent = max(parents, default=None)
        from_write = req['method'] not in ('GET', 'HEAD') or bool(parent and parent[1])
        if parent:
            pending = [p for p in pending if p[0] != parent[0]]
        if from_write:
            redirected_writes.add(i)
        if req.get('redirect_url'):
            pending.append((i, urljoin(req['url'], req['redirect_url']), from_write))
    accepted = []
    for read in reads:
        ts = read.get('request_ts', read['ts'])
        step = action_step(steps, ts)
        if not step or step['step'] <= first['step'] or ts >= cutoff or action_name(step) not in NAV_ACTIONS:
            continue
        matching = [(abs(req['ts'] - ts), i, req) for i, req in enumerate(har)
                    if req['url'] == read['url'] and req['method'] == 'GET' and abs(req['ts'] - ts) < .25]
        match = min(matching, key=lambda x: x[0]) if matching else None
        document = match[2].get('document_navigation', False) if match else read.get('document_navigation', False)
        if not document or (match and match[1] in redirected_writes):
            continue
        # Exclude retry-write redirects even without HAR redirect metadata.
        if any(action_step([step], w.get('request_ts', w['ts'])) for w in writes):
            continue
        accepted.append(read)
    return accepted


def rows(path):
    if not path.exists():
        return []
    # A producer can be in the middle of appending the last line.
    return [json.loads(line) for line in path.read_text().splitlines(keepends=True)
            if line.endswith('\n') and line.strip()]


def wait_rollbacks(directory, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        events = rows(directory / 'proxy_events.jsonl')
        scheduled = sum(e['event'] == 'rollback_scheduled' for e in events)
        done = sum(e['event'] in ('rollback_done', 'rollback_error') for e in events)
        if done >= scheduled:
            if any(e['event'] == 'rollback_error' for e in events):
                raise RuntimeError('rollback_error')
            return
        if time.monotonic() >= deadline:
            raise TimeoutError('rollback_timeout')
        time.sleep(.1)


def evidence(directory, task, entry, claim_time, condition):
    flows = sorted(rows(directory / 'proxy.jsonl'), key=lambda r: r['ts'])
    events = rows(directory / 'proxy_events.jsonl')
    writes = [r for r in flows if r['matched_entry'] == task['family'] and
              r['method'] in ('POST', 'PUT', 'PATCH', 'DELETE')]
    fired = next((e for e in events if e['event'] == 'fired'), None)
    steps = rows(directory / 'trace.jsonl')
    step = next((r['step'] for r in steps if fired and
                 r.get('started_at', float('inf')) <= fired['ts'] <= r.get('ended_at', 0)), None)
    views = readback_views(task, entry)
    agent_views = agent_readback_views(task, entry)
    reads, agent_candidates = [], []
    cutoff = claim_time or max((s.get('ended_at', 0) for s in steps), default=0)
    for r in flows:
        parsed = urlsplit(r['url'])
        target = parsed.path + ('?' + parsed.query if parsed.query else '')
        same_origin = (parsed.scheme, parsed.netloc) == (urlsplit(task['start_url']).scheme, urlsplit(task['start_url']).netloc)
        if (same_origin and writes and claim_time and r['method'] == 'GET' and
            writes[0]['ts'] < r.get('request_ts', r['ts']) <= claim_time and
            any(re.search(v, target) for v in views)):
            reads.append(r)
        if (same_origin and writes and r['method'] == 'GET' and
            min(w.get('request_ts', w['ts']) for w in writes) < r.get('request_ts', r['ts']) < cutoff and
            any(re.search(v, target) for v in agent_views)):
            agent_candidates.append(r)
    undo = [e for e in events if e['event'] == 'rollback_done']
    initiated = agent_readbacks(directory, steps, writes, agent_candidates, cutoff)
    return dict(fault_fired=bool(fired), fired_step=step, fired_time=fired['ts'] if fired else None,
                n_matched_writes=len(writes), rollback_before_claim=(
                    bool(undo) and bool(claim_time) and all(e['ts'] <= claim_time for e in undo)
                    if condition in ('F1', *F3_DELAYS) else None),
                readback_after_write=bool(reads), readback_any=bool(reads),
                readback_agent=bool(initiated), n_readbacks=len(reads), n_readbacks_agent=len(initiated))
