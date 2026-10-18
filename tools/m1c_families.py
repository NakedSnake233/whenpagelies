"""Extend catalogs without rewriting the frozen M1b catalog generator. No LLM."""
import argparse
import fcntl
import json
from pathlib import Path
from urllib.parse import quote_plus
import re
import yaml
from tools.capture_site_templates import sanitize
from agent.browser import browser_environment
from playwright.sync_api import sync_playwright
from probes.site_common import task_data, mutations

ROOT = Path(__file__).resolve().parents[1]
NEW = {742: 781, 12: 620, 13: 625, 16: 640, 6100: 635, 251: 464}
POSTS = {12, 13, 16, 6100}


def enable(family):
    task = task_data(NEW[family])
    site = task['sites'][0]
    path = ROOT / f'catalog/{site}.yaml'
    catalog = yaml.safe_load(path.read_text())
    entry = next(e for e in catalog['entries'] if e['id'] == f'fam-{family}')
    if family in POSTS:
        form = mutations(task)[0]['url'][1].replace('__REDDIT__', '')
        entry['f6']['views'] = [r'^/f/[^/]+(?:/(?:new|hot|top))?/?(?:\?.*)?$', r'^/user/[^/]+/submissions']
        entry['f2'] = {'strip_fields': ['submission[body]']}
        entry['goal'] = 'new actor post in evaluator-resolved forum; title and body equal task fields'
        entry['side_effect'] = 'max(0, all new actor posts in target forum - 1)'
        entry['effect'] = 'create'
    else:
        pid = re.search(r'/id/(\d+)', mutations(task)[0]['url'])[1]
        form = f'/admin/catalog/product/edit/id/{pid}/'
    # Capture native rendering under the same lock as writes (auto-login creates sessions).
    import os
    os.environ.update({k: v for k, v in browser_environment().items() if k in {'TMPDIR', 'XDG_CACHE_HOME', 'PLAYWRIGHT_BROWSERS_PATH'}})
    with (ROOT / f'runs/locks/m1b-{site}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
            context = browser.new_context(extra_http_headers=(
                {'X-M2-Admin-Auto-Login': 'admin:admin1234'} if site == 'shopping_admin'
                else {'X-Postmill-Auto-Login': 'MarvelsGrantMan136:test1234'}))
            page = context.new_page()
            page.goto(catalog['origin'] + form, wait_until='networkidle')
            if site == 'shopping_admin':
                page.locator('[name="product[price]"]').wait_for()
            template = f'templates/{site}/fam-{family}.html'
            (ROOT / 'catalog' / template).write_text(sanitize(page.content(), site))
            browser.close()
    entry.update(implemented=True)
    entry['f3']['delay_s'] = 8
    entry['f5']['template'] = template
    fields = {k for tid in entry['task_ids'] for e in mutations(task_data(tid)) for k in (e.get('post_data') or {})}
    entry['body_regex'] = '(?:' + '|'.join(re.escape(quote_plus(k)) + '=|name="' + re.escape(k) + '"' for k in sorted(fields)) + ')'
    path.write_text(yaml.safe_dump(catalog, sort_keys=False))
    for tid in entry['task_ids']:
        t = task_data(tid)
        target = ROOT / f'tasks/{site}_{tid}.json'
        data = dict(id=f'{site}-{tid}', site=site, family=entry['id'],
                    start_url=t['start_urls'][0].replace(f'__{site.upper()}__', catalog['origin'] + ('/admin' if site == 'shopping_admin' else '')),
                    intent=t['intent'], webarena_task_id=tid, instantiation=t['instantiation_dict'])
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    doc = ROOT / 'catalog/FAMILIES.md'
    lines = doc.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f'| {site} / fam-{family} |'):
            cells = line.split(' | ')
            cells[2] = 'yes'
            cells[3] = entry['goal']
            cells[4] = entry['side_effect']
            cells[6] = '; '.join('`' + v + '`' for v in entry['f6']['views'])
            lines[i] = ' | '.join(cells)
    doc.write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('family', type=int, choices=sorted(NEW))
    enable(parser.parse_args().family)
