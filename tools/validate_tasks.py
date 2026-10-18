"""C0 task-by-task browser/proxy/backend/WebArena validation. No model transport.

Run long batches with: python -m tools.background m1c-validation
    .venv/bin/python -m tools.validate_tasks
Each task holds the shared site lock through HAR flush, proxy shutdown and undo.
"""
import argparse
import fcntl
import importlib
import json
import os
from pathlib import Path
import sys
import uuid
from urllib.parse import urlsplit
import yaml
from playwright.sync_api import sync_playwright
from agent.browser import scrub_har, browser_environment
from runner.worker_proxy import WorkerProxy
from runner.evaluation import evaluate as benchmark_evaluate
from probes.site_common import task_data, mutations
from proxy.plan import Plan, write_plan

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_sites_faults import BrowserFlow


def rows_equal(a, b):
    return {k: sorted(json.dumps(r, sort_keys=True) for r in v) for k, v in a.items()} == {
        k: sorted(json.dumps(r, sort_keys=True) for r in v) for k, v in b.items()}


def resolve_flow(page, task, baseline, index=0):
    """Keep evaluator task data unchanged; canonicalize only the native form URL."""
    flow = BrowserFlow(page, task, baseline, mutation_index=index)
    if flow.fam == 22:
        flow.view = '/f/' + baseline['forum_name'] + '/' + str(baseline['submission_id']) + '/-'
    if flow.fam == 4:
        # The benchmark requires this post as Referer, not just the forum listing.
        ref = mutations(task)[0]['headers']['referer'].replace('__REDDIT__', '')
        parts = ref.split('/')
        parts[2] = baseline['forum_name']
        flow.view = '/'.join(parts)
    flow.prepare()
    if flow.fam == 4:
        flow.write = '/f/' + baseline['forum_name'] + '/subscribe.json'
    return flow


def browser_write(flow):
    """Real browser fetch, captured in the context HAR, preserving native CSRF."""
    return flow.page.evaluate('''async ({url, fields}) => {
        const r = await fetch(url, {method: 'POST', body: new URLSearchParams(fields),
            credentials: 'same-origin', redirect: 'follow'});
        await r.text();
        return r.status;
    }''', {'url': flow.origin + flow.write, 'fields': flow.payload})


def validate(task, port=8241):
    site, tid = task['sites'][0], task['task_id']
    episode = f'm1c-validation-{site}-{tid}-{uuid.uuid4().hex[:8]}'
    directory = ROOT / 'runs' / episode
    directory.mkdir(parents=True)
    record = dict(task=tid, site=site, family=f"fam-{task['intent_template_id']}",
                  G=False, D=None, restore_ok=False, eval_pass=False, notes=[], episode=episode)
    probe = importlib.import_module('probes.' + site)
    har = directory / 'network.har'
    baseline = None
    stage = "baseline"
    proxy = WorkerProxy('m1c-validation', port=port)
    with (ROOT / f'runs/locks/m1b-{site}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (ROOT / f'runs/dirty_{site}').exists():
            raise RuntimeError('site has a dirty marker; restore it before validation')
        try:
            baseline = probe.baseline(task, episode_dir=directory)
            stage = "proxy startup"
            proxy.ensure()
            write_plan(proxy.plan, Plan(episode, site, 'C0', 0, [record['family']],
                       {'task_id': tid, 'baseline_path': str(directory / 'baseline.json')}))
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=['--no-sandbox', '--proxy-bypass-list=<-loopback>'],
                    proxy={'server': f'http://127.0.0.1:{port}'})
                try:
                    context = browser.new_context(record_har_path=str(har), record_har_content='embed',
                        extra_http_headers=({'X-M2-Admin-Auto-Login': 'admin:admin1234'} if site == 'shopping_admin'
                            else {'X-Postmill-Auto-Login': 'MarvelsGrantMan136:test1234'}))
                    # Browser traffic is restricted to the two local benchmark sites.
                    context.route('**/*', lambda route: route.continue_() if urlsplit(route.request.url).hostname in {'localhost', '127.0.0.1'} else route.abort())
                    page = context.new_page()
                    page.set_default_timeout(20000)
                    page.set_default_navigation_timeout(30000)
                    for index in range(len(mutations(task))):
                        stage = f"native form {index + 1}"
                        flow = resolve_flow(page, task, baseline, index)
                        stage = f"proxied write {index + 1}"
                        status = browser_write(flow)
                        record['notes'].append(f'write {index + 1}: HTTP {status}')
                    stage = "backend evaluation"
                    result = probe.evaluate(task, baseline)
                    record.update(G=result['G'], D=result['D'])
                    context.close()
                finally:
                    browser.close()
            scrub_har(har, response_keys=('subscribed', 'netScore'))
            stage = 'WebArena HAR evaluator'
            record['eval_pass'], detail = benchmark_evaluate({'webarena_task_id': tid}, har, 'DONE')
            record['notes'].append(detail)
            if not record['eval_pass']:
                record['notes'].append('excluded: unchanged upstream evaluator rejected recorded native requests; see eval.json')
                if task['intent_template_id'] == 4 and baseline.get('forum_name', '').lower() != task['instantiation_dict']['forum'].lower():
                    record['notes'].append('upstream URL/Referer use forum spelling '
                        + repr(task['instantiation_dict']['forum']) + '; native forum is '
                        + repr(baseline['forum_name']))
            stage = 'proxy write coverage'
            proxy_rows = [json.loads(line) for line in (directory / 'proxy.jsonl').read_text().splitlines()]
            assert sum(r.get('matched_entry') == record['family'] for r in proxy_rows) >= len(mutations(task)), 'missing proxied writes'
        except Exception as exc:
            # Exception text may contain request payloads/headers. Persist type only.
            record['notes'].append('excluded at ' + stage + ': ' + type(exc).__name__)
        finally:
            proxy.close()
            if har.exists():
                scrub_har(har, response_keys=('subscribed', 'netScore'))
            if baseline is not None:
                try:
                    probe.restore(task, baseline)
                    probe.restore(task, baseline)
                    record['restore_ok'] = rows_equal(baseline['rows'], probe.baseline(task)['rows'])
                except Exception as exc:
                    record['notes'].append('restore: ' + type(exc).__name__)
                if not record['restore_ok']:
                    (ROOT / f'runs/dirty_{site}').write_text(f'M1c restore verification failed: {directory}\n')
            record['validated'] = bool(record['G'] and record['D'] == 0 and record['restore_ok'] and record['eval_pass'] and not any(n.startswith('excluded') for n in record['notes']))
            (directory / 'validation.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def report(records):
    ordered = sorted(records.values(), key=lambda r: (r['site'], r['family'], r['task']))
    lines = ['# Per-task scripted validation', '', 'No LLM calls. Unmodified installed WebArena-Verified evaluator; DONE claim; browser HAR through the proxy. Only rows passing every gate enter tasks/INDEX.md.', '',
             '| Task | Family | Scripted G | D | restore_ok | eval_pass | Notes |', '|---|---|---|---:|---|---|---|']
    index = ['# Validated tasks', '', 'Task IDs are WebArena-Verified IDs. Source evidence: logs/task_validation.md and runs/m1c-validation-*/validation.json. Excluded tasks stay on disk for reproducibility but are outside the study.', '']
    for r in ordered:
        lines.append(f"| {r['site']}/{r['task']} | {r['family']} | {r['G']} | {r['D']} | {r['restore_ok']} | {r['eval_pass']} | {'; '.join(r['notes'])} |")
    for site, family in sorted({(r['site'], r['family']) for r in ordered}):
        good = [r for r in ordered if r['site'] == site and r['family'] == family and r['validated']]
        index += [f'## {site} / {family}', '', ', '.join(f"[{r['task']}]({site}_{r['task']}.json)" for r in good) or 'No validated tasks.', '']
    (ROOT / 'logs/task_validation.md').write_text('\n'.join(lines) + '\n')
    (ROOT / 'logs/task_validation.json').write_text(json.dumps(ordered, indent=2) + '\n')
    (ROOT / 'tasks/INDEX.md').write_text('\n'.join(index))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks', nargs='*', type=int)
    parser.add_argument('--family', type=int)
    parser.add_argument('--port', type=int, default=8241)
    parser.add_argument('--remaining', action='store_true', help='skip tasks already admitted by a previous validation')
    args = parser.parse_args()
    os.environ.update({k: v for k, v in browser_environment().items() if k in {'TMPDIR', 'XDG_CACHE_HOME', 'PLAYWRIGHT_BROWSERS_PATH'}})
    ids = {i for site in ('reddit', 'shopping_admin') for e in yaml.safe_load((ROOT / f'catalog/{site}.yaml').read_text())['entries'] if e.get('implemented') for i in e['task_ids']}
    tasks = [task_data(i) for i in sorted(ids) if (args.tasks is None or i in args.tasks)]
    if args.family:
        tasks = [t for t in tasks if t['intent_template_id'] == args.family]
    output = ROOT / 'logs/task_validation.json'
    records = {r['task']: r for r in json.loads(output.read_text())} if output.exists() else {}
    if args.remaining:
        tasks = [t for t in tasks if not records.get(t['task_id'], {}).get('validated')]
    for task in tasks:
        record = validate(task, args.port)
        records[task['task_id']] = record
        report(records)
        print(f"{record['site']}/{record['task']}: G={record['G']} D={record['D']} restore={record['restore_ok']} eval={record['eval_pass']} validated={record['validated']}", flush=True)
        if not record['restore_ok']:
            raise RuntimeError('validation stopped because restore did not verify')
    print(f'Completed {len(tasks)} task validations', flush=True)


if __name__ == '__main__':
    main()
