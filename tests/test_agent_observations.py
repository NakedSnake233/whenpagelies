"""Observation regressions use only fake pages and local HTML, never sites."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.browser import (LocalBrowserEnv, RENDERED_FORM_CONTROLS,
                           browser_environment, wait_for_observation)
from agent.env import observation
from agent.obs_check import field_bids, site_for_url
from browsergym.core import _get_global_playwright
from browsergym.core.env import BrowserEnv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def snapshot():
    def node(bid, role, name, children=()):
        return dict(nodeId=bid, browsergym_id=bid, role={'value': role}, name={'value': name},
                    childIds=list(children), ignored=False, properties=[])
    return dict(goal='inspect', open_pages_titles=['Page'], open_pages_urls=['http://local'],
        active_page_index=[0], axtree_object={'nodes': [
            node('1', 'RootWebArea', 'Page', ['2', '3', '4', '5']),
            node('2', 'textbox', 'Price'), node('3', 'combobox', 'notice-ABCD'),
            node('4', 'textbox', 'Hidden'), node('5', 'link', 'Other offscreen link')]},
        extra_element_properties={str(i): {'visibility': 0, 'bbox': None, 'clickable': False, 'set_of_marks': False} for i in range(1, 6)},
        rendered_form_controls={'2': 'Price', '3': 'Stock Status'})


def test_rendered_controls_keep_bids_below_fold_and_repair_notice_label():
    obs = snapshot()
    original = deepcopy(obs)
    tree = observation(obs)['ax']
    assert "[2] textbox 'Price'" in tree
    assert "[3] combobox 'Stock Status'" in tree
    assert '[4]' not in tree and '[5]' not in tree
    assert 'notice-ABCD' not in tree
    assert obs == original, 'formatting must not mutate the original AX evidence'
    assert field_bids(tree, ['Price', 'Stock Status']) == {'Price': ['2'], 'Stock Status': ['3']}


def test_observation_cap_and_legacy_snapshot_compatibility():
    obs = snapshot()
    obs.pop('rendered_form_controls')
    assert "combobox 'notice-ABCD'" in observation(obs)['ax']
    obs['axtree_object']['nodes'][1]['name']['value'] = 'x' * 50000
    assert len(observation(obs)['ax']) == 40000


def test_readiness_wait_shares_ten_second_budget(monkeypatch):
    times = iter([20, 20.001, 26, 26.001])
    monkeypatch.setattr('agent.browser.time.monotonic', lambda: next(times))
    calls = []
    page = SimpleNamespace(url='http://localhost:7780/admin/catalog/product/edit/id/872/',
        wait_for_load_state=lambda state, timeout: calls.append(('network', timeout)),
        wait_for_function=lambda script, timeout: calls.append(('mask', timeout)))
    wait_for_observation(page)
    assert calls[0][0] == 'network' and 9900 <= calls[0][1] <= 10000
    assert calls[1][0] == 'mask' and 3900 <= calls[1][1] <= 4000


def test_readiness_timeout_is_bounded_and_non_magento_does_not_wait():
    calls = []
    def timeout(*args, **kwargs):
        calls.append(kwargs['timeout'])
        raise PlaywrightTimeoutError('still loading')
    page = SimpleNamespace(url='http://localhost:7780/admin', wait_for_load_state=timeout,
        wait_for_function=lambda *a, **kw: pytest.fail('no budget remains'))
    wait_for_observation(page)
    assert len(calls) == 1 and 0 < calls[0] <= 10000
    wait_for_observation(SimpleNamespace(url='http://127.0.0.1:9999/f/headphones'))


def test_env_waits_before_extracting_and_attaches_rendered_controls(monkeypatch):
    order = []
    monkeypatch.setattr('agent.browser.wait_for_observation', lambda page: order.append('wait'))
    def extract(self):
        order.append('extract')
        return {}
    monkeypatch.setattr(BrowserEnv, '_get_obs', extract)
    page = SimpleNamespace(evaluate=lambda script: {'1': 'Price'})
    env = object.__new__(LocalBrowserEnv)
    env.page = page
    obs = env._get_obs()
    assert order == ['wait', 'extract']
    assert obs['rendered_form_controls'] == {'1': 'Price'}


def test_rendered_dom_controls_exclude_hidden_but_keep_below_viewport():
    browser = _get_global_playwright().chromium.launch(headless=True, env=browser_environment())
    try:
        page = browser.new_page(viewport={'width': 800, 'height': 600})
        page.set_content('''<label for="p">Price</label><input id="p" bid="1">
            <div style="margin-top:1200px"><label for="s">Stock Status</label>
            <select id="s" bid="2"><option>In Stock</option></select></div>
            <fieldset style="display:none"><input bid="3"></fieldset>
            <input bid="4" type="hidden"><input bid="5" style="visibility:hidden">
            <div style="opacity:0"><input bid="6"></div>''')
        assert page.evaluate(RENDERED_FORM_CONTROLS) == {'1': 'Price', '2': 'Stock Status'}
        # Exercise the real JavaScript readiness predicate with delayed rendering.
        page.route('http://localhost:7780/**', lambda route: route.fulfill(body='''
            <div class="loading-mask">Loading</div><script>
            setTimeout(() => {document.querySelector('.loading-mask').style.display='none';
                const input=document.createElement('input'); input.name='product[sku]';
                document.body.append(input);}, 700);
            </script>''', content_type='text/html'))
        page.goto('http://localhost:7780/admin/catalog/product/edit/id/1/')
        wait_for_observation(page)
        assert page.locator('input').count() == 1
        assert not page.locator('.loading-mask').is_visible()
    finally:
        browser.close()


@pytest.mark.parametrize('url', ['http://localhost:7780/admin/catalog/product/delete/id/1/',
    'http://localhost:7780/admin/catalog/product/edit/id/1/?key=x',
    'http://127.0.0.1:9999/logout', 'https://external.test/',
    'http://127.0.0.1:9999/f/headphones/subscribe'])
def test_dry_check_rejects_non_browsing_urls(url):
    with pytest.raises(ValueError):
        site_for_url(url)
