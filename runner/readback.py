"""Shared catalog and inventory URL eligibility for metrics and VRV."""
import re
from urllib.parse import urlsplit


def readback_views(task, entry):
    # Catalog views plus inventory readback views (including the newly created post
    # and the native edit form, which need not be eligible for F6 snapshots).
    views = list(entry.get('f6', {}).get('views', []))
    if task['site'] == 'reddit':
        views += [r'^/user/[^/]+(?:/|$)', r'^/f/', r'^/submit', r'^/forums']
    else:
        views += [r'^/admin/catalog/product/(?:edit|index)/', r'^/admin/mui/index/render/',
                  r'^/admin/(?:sales|review|cms|promo)/']
    return views


def agent_readback_views(task, entry):
    views = readback_views(task, entry)
    # For post creation, the empty composition form cannot read back a saved
    # post. Preserve the legacy broad metric, but use actual persisted views
    # for agent readback (post/listing and the user's submissions).
    post_creation = (task['site'] == 'reddit' and entry.get('effect') == 'create'
                     and '/submit' in entry.get('url_regex', ''))
    return ([
        # Forum listings and saved posts (including their populated edit form).
        # /f/<forum>/submit is another empty composition form, so ^/f/ alone
        # is too broad even for the former fam-15 special case.
        r'^/f/[^/?]+(?:/?(?:\?|$)|/(?:new|hot|top|active|comments)/?(?:\?|$)|/\d+(?:/|\?|$))',
        r'^/user/[^/]+/submissions/?(?:\?|$)',
    ] if post_creation else views)


def eligible_readback(url, task, entry):
    parsed = urlsplit(url)
    start = urlsplit(task['start_url'])
    target = parsed.path + ('?' + parsed.query if parsed.query else '')
    return ((parsed.scheme, parsed.netloc) == (start.scheme, start.netloc)
            and any(re.search(view, target) for view in agent_readback_views(task, entry)))
