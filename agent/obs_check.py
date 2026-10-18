"""Read-only observation check: python -m agent.obs_check [URL ...].

Only product edit pages and public Reddit browsing pages are accepted. All
non-GET/HEAD traffic and cross-origin traffic are blocked. No actions or LLM
calls are made; only field labels, bids and observation sizes are printed.
"""
import argparse
import json
import re
from unittest.mock import patch
from urllib.parse import urlsplit

from agent.browser import AUTO_LOGIN, LocalBrowserEnv, origin, visible_url
from agent.env import EpisodeTask, action_set, observation
from browsergym.utils.obs import flatten_axtree_to_str

DEFAULT_URLS = [
    'http://localhost:7780/admin/catalog/product/edit/id/1481/',
    'http://localhost:7780/admin/catalog/product/edit/id/872/',
    'http://127.0.0.1:9999/f/headphones',
]


def site_for_url(url):
    parsed = urlsplit(url)
    if parsed.query or parsed.username or parsed.password:
        raise ValueError('Use a plain read-only page URL without credentials or query parameters')
    if origin(url) == origin(AUTO_LOGIN['shopping_admin'][0]) and re.fullmatch(
            r'/admin/catalog/product/edit/id/[0-9]+/?', parsed.path):
        return 'shopping_admin'
    if origin(url) == origin(AUTO_LOGIN['reddit'][0]) and re.fullmatch(
            r'/(?:f/[A-Za-z0-9_]+(?:/[0-9]+(?:/[-\w]+)?)?/?|)', parsed.path):
        return 'reddit'
    raise ValueError('URL must be a local Magento product edit or public Reddit page')


class ReadOnlyTask(EpisodeTask):
    def setup(self, page):
        expected_origin = origin(self.task['start_url'])
        self.blocked_requests = 0

        def guard(route):
            if route.request.method not in ('GET', 'HEAD') or origin(route.request.url) != expected_origin:
                self.blocked_requests += 1
                route.abort()
            else:
                route.continue_()

        page.context.route('**/*', guard)
        page.goto(self.task['start_url'], timeout=30000)
        return self.task['intent'], {}


def legacy_tree(obs):
    return flatten_axtree_to_str(obs['axtree_object'],
        extra_properties=obs.get('extra_element_properties'), skip_generic=True,
        remove_redundant_static_text=True, hide_bid_if_invisible=True)[:40000]


def field_bids(tree, fields):
    return {field: [match[1] for line in tree.splitlines()
        if re.search(r'\b' + re.escape(field) + r'\b', line, re.I)
        and (match := re.search(r'\[([0-9]+)\] (?:textbox|combobox|spinbutton)\b', line))]
        for field in fields}


def check_url(url, fields):
    site = site_for_url(url)
    env = LocalBrowserEnv(task_entrypoint=ReadOnlyTask,
        task_kwargs={'task': dict(site=site, start_url=url, intent='Read-only observation check')},
        headless=True, slow_mo=0, action_mapping=action_set().to_python_code,
        pw_chromium_kwargs={'env': {}},
        pw_context_kwargs={'extra_http_headers': AUTO_LOGIN[site][1]})
    try:
        # Reproduce the old extraction timing and formatter in the same session.
        with patch('agent.browser.wait_for_observation', lambda page: None):
            initial, _ = env.reset(seed=0)
        before = legacy_tree(initial)
        settled = env._get_obs()
        old_settled = legacy_tree(settled)
        after = observation(settled)['ax']
        return dict(url=visible_url(url), before_chars=len(before),
            settled_legacy_chars=len(old_settled), after_chars=len(after),
            before_fields=field_bids(before, fields),
            settled_legacy_fields=field_bids(old_settled, fields),
            after_fields=field_bids(after, fields),
            field_lines=[line.strip() for line in after.splitlines()
                         if any(bids and f'[{bid}]' in line for bids in field_bids(after, fields).values()
                                for bid in bids)],
            blocked_requests=env.task.blocked_requests)
    finally:
        env.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('urls', nargs='*', default=DEFAULT_URLS)
    parser.add_argument('--fields', nargs='+', default=['Price', 'Quantity', 'Stock Status'])
    args = parser.parse_args()
    failed = False
    for url in args.urls:
        result = check_url(url, args.fields)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if site_for_url(url) == 'shopping_admin' and not all(result['after_fields'].values()):
            failed = True
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
