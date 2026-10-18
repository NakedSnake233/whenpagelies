"""Read-only first agent read-back audit; output contains templates, never headers."""
from collections import Counter
import json
import re
from urllib.parse import urlsplit
from unittest.mock import patch

from proxy.catalog import load_catalog
from runner import metrics
from tools.analyze import ROOT, capture

POST = {'fam-12', 'fam-13', 'fam-15', 'fam-16', 'fam-6100'}


def template(url):
    path = urlsplit(url).path.rstrip('/') or '/'
    if path == '/submit':
        return '/submit'
    if path.startswith('/submit/'):
        return '/submit/<forum>'
    if path.startswith('/f/'):
        parts = path.split('/')
        parts[2] = '<forum>'
        if len(parts) > 3 and parts[3].isdigit():
            return '/f/<forum>/<post>' + ('/edit' if path.endswith('/edit') else '')
        return '/'.join(parts)
    if path.startswith('/user/'):
        parts = path.split('/')
        parts[2] = '<user>'
        return '/'.join(parts)
    return re.sub(r'/\d+(?=/|$)', '/<id>', path)


def cannot_display(row, path):
    if row['site'] != 'reddit':
        return False
    if path.startswith('/submit') or path == '/f/<forum>/submit':
        return True
    if path.startswith('/forums') and row['family'] != 'fam-4':
        return True
    if row['family'] == 'fam-6' and path == '/user/<user>/account':
        return True
    if row['family'] in POST and path.startswith('/user/') and '/submissions' not in path:
        return True
    return False


def main():
    selected, _, _, _, tasks, _, metadata = capture(ROOT)
    catalogs = {s: {e['id']: e for e in load_catalog(ROOT / f'catalog/{s}.yaml').entries}
                for s in {t['site'] for t in tasks.values()}}
    main = [r for r in selected if r['_stream'] == 'm5-main' and r['policy'] == 'B0' and r['exposed']]
    counters, only_invalid, details = {}, {}, []
    original = metrics.agent_readbacks
    for row in main:
        if row['family'] not in POST and row['condition'] not in {'F3', 'F4', 'F5'}:
            continue
        accepted = []
        def collect(*args):
            result = original(*args)
            accepted.extend(result)
            return result
        with patch.object(metrics, 'agent_readbacks', collect):
            result = metrics.evidence(ROOT / 'runs' / row['episode'], tasks[row['task']],
                                      catalogs[row['site']][row['family']], row.get('final_message_at'), row['condition'])
        paths = [template(r['url']) for r in accepted]
        invalid_only = bool(paths) and all(cannot_display(row, p) for p in paths)
        groups = []
        if row['family'] in POST:
            groups.append('post families/' + row['condition'])
        if row['condition'] in {'F3', 'F4', 'F5'}:
            groups.append('all families/' + row['condition'])
        for group in groups:
            counters.setdefault(group, Counter())[paths[0] if paths else '(none)'] += 1
            only_invalid[group] = only_invalid.get(group, 0) + invalid_only
        details.append(dict(episode=row['episode'], condition=row['condition'], family=row['family'],
                            first=paths[0] if paths else None, paths=sorted(set(paths)),
                            invalid_only=invalid_only, stored=row.get('readback_agent'),
                            computed=result['readback_agent']))
    lines = ['# Agent read-back view audit', '', 'Snapshot: ' + metadata['snapshot_utc'] + '.',
             'Selection: tools.analyze.capture, latest valid m5-main B0 exposed episodes; post families fam-12/13/15/16/6100 across conditions, plus all families F3/F4/F5.',
             'Read-only diagnosis: reuse evidence()/agent_readbacks() with existing filters and request timestamps; first accepted GET before claim/step cutoff. Paths omit queries and variable forum/user/post IDs.', '',
             '|Scope/condition|First agent read-back template: episode counts|Only non-state views|',
             '|---|---|---:|']
    for group, counts in sorted(counters.items()):
        lines.append('|'+group+'|'+'; '.join(f'`{p}`: {n}' for p,n in sorted(counts.items()))+'|'+str(only_invalid[group])+'|')
    mismatch = sum(d['stored'] != d['computed'] for d in details)
    lines += ['', f'Checked {len(details)} unique episodes; stored/computed RB mismatches: {mismatch}.',
              'Non-state views: `/submit` and `/submit/<forum>` and `/f/<forum>/submit` are empty composition forms; `/forums` cannot show saved post content (subscription fam-4 exempt); user profile/edit pages cannot show saved post content. `/user/<user>/account` is a suspected additional non-biography view (inferred from route; HAR has no retained page body), included in the all-family F4 non-state count and left outside the post-only fix. Other observed paths were post pages, forum listings, or Magento persisted-state views.',
              'Only non-state means every accepted agent read-back in the episode is non-state; a non-state FIRST view alone does not imply the flag must flip.']
    (ROOT / 'logs/rb_view_audit.md').write_text('\n'.join(lines)+'\n')
    (ROOT / 'logs/rb_view_audit_snapshot.json').write_text(json.dumps(dict(metadata=metadata, details=details, only_invalid=only_invalid), indent=2)+'\n')
    print(json.dumps(dict(episodes=len(details), only_invalid=only_invalid, mismatches=mismatch)))


if __name__ == '__main__':
    main()
