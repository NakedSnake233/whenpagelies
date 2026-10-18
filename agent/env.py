"""Custom BrowserGym task, high-level action set and compact observations."""
import re
from agent.browser import AUTO_LOGIN, LocalBrowserEnv, origin
from browsergym.core.action.highlevel import HighLevelActionSet
from browsergym.core.task import AbstractBrowserTask
from browsergym.utils.obs import flatten_axtree_to_str

ALLOWED_ACTIONS = {'noop', 'click', 'fill', 'select_option', 'hover', 'press', 'scroll', 'goto',
                   'go_back', 'go_forward', 'new_tab', 'tab_focus',
                   'send_msg_to_user', 'report_infeasible'}


def report_verification(result: str, evidence: str):
    """Report whether the next pending postcondition holds using visible evidence.

    Examples:
        report_verification('holds', 'The profile page shows the new biography')
        report_verification('does not hold', 'The product page still shows Enabled')
    """
    raise RuntimeError('report_verification must be handled by the policy loop')


def action_set(policy='B0'):
    from agent.policies import VRV_POLICIES
    verification = policy in VRV_POLICIES
    actions = HighLevelActionSet(subsets=['bid', 'nav', 'chat', 'tab', 'infeas'] + (['custom'] if verification else []),
                                 custom_actions=[report_verification] if verification else None,
                                 multiaction=False, strict=True)
    allowed = ALLOWED_ACTIONS | ({'report_verification'} if verification else set())
    actions.action_set = {k: v for k, v in actions.action_set.items() if k in allowed}
    # BrowserGym 0.14.3 hard-codes 500 ms even for click-triggered navigation.
    # Retain its actions, but allow the local benchmark servers five seconds.
    actions.python_includes = re.sub(r'\btimeout=500\b', 'timeout=5000', actions.python_includes)
    return actions


class EpisodeTask(AbstractBrowserTask):
    def __init__(self, seed, task):
        super().__init__(seed)
        self.task = task
        self.slow_mo = 0

    def setup(self, page):
        site_origin, headers = AUTO_LOGIN.get(self.task['site'], ('', {}))
        if headers:
            # Context defaults supply login; never forward it to a different origin.
            def scope_header(route):
                scoped = dict(route.request.headers)
                if origin(route.request.url) != origin(site_origin):
                    for name in headers:
                        scoped.pop(name.lower(), None)
                route.continue_(headers=scoped)
            page.context.route('**/*', scope_header)
        page.goto(self.task['start_url'], timeout=30000)
        return self.task['intent'], {}

    def validate(self, page, chat_messages):
        # BrowserGym prepends an assistant greeting before the initial user goal.
        last_user = max((i for i, m in enumerate(chat_messages) if m['role'] == 'user'), default=-1)
        final = any(m['role'] in ('assistant', 'infeasible') for m in chat_messages[last_user + 1:])
        return 0, final, '', {}


def make_env(task, episode_dir, proxy_port=0):
    launch = {'env': {}}  # LocalBrowserEnv supplies a filtered child environment.
    if proxy_port:
        launch['proxy'] = {'server': f'http://127.0.0.1:{proxy_port}'}
    return LocalBrowserEnv(task_entrypoint=EpisodeTask, task_kwargs={'task': task},
        headless=True, slow_mo=0, action_mapping=action_set().to_python_code,
        pw_chromium_kwargs=launch,
        pw_context_kwargs={'record_har_path': str(episode_dir / 'network.har'),
                           'record_har_content': 'embed',
                           'extra_http_headers': AUTO_LOGIN.get(task['site'], ('', {}))[1]})


def observation(obs):
    # IntersectionObserver visibility is viewport overlap, not CSS visibility.
    # Retain bids for rendered form controls even below the fold. Truly hidden
    # controls remain excluded; no global expansion of the accessibility tree.
    controls = obs.get('rendered_form_controls', {})
    properties = dict(obs.get('extra_element_properties') or {})
    nodes = []
    for node in obs['axtree_object']['nodes']:
        bid = node.get('browsergym_id')
        if bid in controls and not node.get('ignored', False):
            properties[bid] = {'bbox': None, 'clickable': False, 'set_of_marks': False,
                               **properties.get(bid, {}), 'visibility': 1}
            label = controls[bid]
            if label and node.get('name', {}).get('value', '').startswith('notice-'):
                node = {**node, 'name': {**node['name'], 'value': label}}
        nodes.append(node)
    tree = flatten_axtree_to_str({**obs['axtree_object'], 'nodes': nodes},
        extra_properties=properties, skip_generic=True,
        remove_redundant_static_text=True, hide_bid_if_invisible=True)[:40000]
    tabs = list(zip(obs['open_pages_titles'], obs['open_pages_urls']))
    index = int(obs['active_page_index'][0])
    return {'goal': obs['goal'], 'url': obs['open_pages_urls'][index],
            'tabs': tabs, 'ax': tree, 'last_action': obs.get('last_action', ''),
            'last_error': obs.get('last_action_error', '')}
