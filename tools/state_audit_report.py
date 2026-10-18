"""Offline primary-key diff and conservative attribution for the M5c snapshots."""
from collections import Counter
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, parse_qs

from tools.state_audit import OUT, ROOT, SITES, save
from runner.superseded import superseded_ids


def stamp(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
    except ValueError:
        return None


def row_times(row):
    return [t for k, v in (row or {}).items()
            if k in ('created_at', 'updated_at', 'timestamp', 'last_edited_at', 'edited_at', 'subscribed_at')
            and (t := stamp(v)) is not None]


def diff_rows(live, pristine, keys, cutoff):
    def index(rows):
        out = {}
        for r in rows:
            key = tuple(r[k] for k in keys)
            if key in out:
                raise ValueError('duplicate primary key')
            out[key] = r
        return out
    left, right = index(live), index(pristine)
    result, excluded = [], []
    for key in sorted(left.keys() | right.keys(), key=str):
        a, b = left.get(key), right.get(key)
        if a == b:
            continue
        entry = {'key': dict(zip(keys, key)), 'kind': 'only_live' if b is None else 'only_pristine' if a is None else 'changed',
                 'live': a, 'pristine': b,
                 'changes': {k: {'live': (a or {}).get(k), 'pristine': (b or {}).get(k)}
                             for k in sorted((a or {}).keys() | (b or {}).keys())
                             if (a or {}).get(k) != (b or {}).get(k)}}
        if any(t >= cutoff for t in row_times(a)):
            excluded.append(entry)
        else:
            result.append(entry)
    return result, excluded


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def read_lines(path):
    if not path.exists():
        return []
    return [json.loads(s) for s in path.read_text().splitlines(keepends=True) if s.endswith('\n') and s.strip()]


def identities(site, table, row):
    if not row:
        return set()
    if site == 'shopping_admin':
        if table in ('review', 'review_detail'):
            return {('review', str(row['review_id'])), ('product', str(row['entity_pk_value']))}
        return {('product', str(row.get('product_id', row.get('entity_id'))))}
    out = set()
    types = {'submission_id': 'submission', 'comment_id': 'comment', 'forum_id': 'forum'}
    for column, kind in types.items():
        if row.get(column) is not None:
            out.add((kind, str(row[column])))
    if table in ('submissions', 'comments', 'users'):
        out.add(({'submissions': 'submission', 'comments': 'comment', 'users': 'user'}[table], str(row['id'])))
    return out


def url_entities(url, forums):
    path = urlsplit(url or '').path
    out = set()
    patterns = [('submission', r'/sv/(\d+)'), ('submission', r'/f/[^/]+/(\d+)'),
                ('comment', r'/comment/(\d+)'), ('product', r'/catalog/product/[^/]+/id/(\d+)'),
                ('review', r'/review/[^/]+/(?:id|review_id)/(\d+)')]
    for kind, pattern in patterns:
        out.update((kind, x) for x in re.findall(pattern, path))
    match = re.search(r'/f/([^/]+)', path)
    if match and match[1].lower() in forums:
        out.add(('forum', forums[match[1].lower()]))
    if re.search(r'/user/marvelsgrantman136(?:/|$)', path, re.I):
        out.add(('user', '13915'))
    return out


def baseline_entities(b):
    out = set()
    for k, kind in [('submission_id', 'submission'), ('forum_id', 'forum')]:
        if k in b:
            out.add((kind, str(b[k])))
    for row in b.get('rows', {}).get('submissions', []):
        if row.get('id') == b.get('submission_id') and row.get('forum_id') is not None:
            out.add(('forum', str(row['forum_id'])))
    for i in b.get('target_ids', []):
        out.add(('product', str(i)))
    if b.get('task_id') in range(399, 404):
        out.add(('user', '13915'))
    return out


def task_targets(forums):
    import yaml
    catalog_ids = {}
    for site in SITES:
        for entry in yaml.safe_load((ROOT / f'catalog/{site}.yaml').read_text())['entries']:
            for tid in entry.get('task_ids', []):
                catalog_ids[tid] = entry['id']
    source = {t['task_id']: t for t in read_json(ROOT / 'catalog/site_tasks.json')}
    result = {}
    for path in sorted((ROOT / 'tasks').glob('*.json')):
        t = read_json(path)
        if not isinstance(t, dict) or 'webarena_task_id' not in t:
            continue
        tid = t['webarena_task_id']
        entities = set()
        for e in source[tid].get('eval', []):
            expected = e.get('expected', {})
            urls = expected.get('url', [])
            if isinstance(urls, str):
                urls = [urls]
            for url in urls:
                entities |= url_entities(url, forums)
            if 'submission[forum]' in expected.get('post_data', {}):
                entities.add(('forum', str(expected['post_data']['submission[forum]'])))
        forum = source[tid].get('instantiation_dict', {}).get('forum', '')
        normalized = re.sub(r'\s+', '', forum.lower())
        if normalized in forums:
            entities.add(('forum', forums[normalized]))
        result[t['id']] = {'entities': entities, 'family': catalog_ids.get(tid), 'site': t['site']}
    return result


def form_fields(post):
    # Extract only audit-relevant values; never copy form/session keys or headers.
    text = post.get('text', '').replace('\\r\\n', '\r\n')
    fields = {k: v[-1] for k, v in parse_qs(text).items()} if 'urlencoded' in post.get('mimeType', '') else dict(
        re.findall(r'name="([^"\n]+)"\r\n\r\n(.*?)(?=\r\n--)', text, re.S))
    fields.update({x['name']: x.get('value', '') for x in post.get('params', [])})
    return {k: v for k, v in fields.items() if k in ('search', 'excluded', 'attributes[price]',
            'attributes[status]', 'product[price]', 'product[status]', 'product[description]',
            'product[short_description]', 'product[name]') or re.fullmatch(r'selected\[\d*\]', k)}


def enrich_har(directory, flows, names):
    if not any('/catalog/' in f['path'] and f.get('method') == 'POST' for f in flows):
        return
    entries = read_json(directory / 'network.har', {}).get('log', {}).get('entries', [])
    selection = set()
    for entry in entries:
        request = entry.get('request', {})
        if request.get('method') != 'POST':
            continue
        at = stamp(entry.get('startedDateTime'))
        if at is None:
            continue
        path = re.sub(r'/key/[^/]+', '/key/[REDACTED]', urlsplit(request.get('url', '')).path)
        matches = [f for f in flows if f['path'] == path and abs(f['at'] - at) < 2]
        if not matches:
            continue
        f = min(matches, key=lambda f: abs(f['at'] - at))
        fields = form_fields(request.get('postData', {}))
        f['fields'] = fields
        if 'massStatus' in path or 'product_action_attribute/edit' in path:
            selection = {('product', str(v)) for k, v in fields.items() if k.startswith('selected[')}
            if fields.get('excluded') == 'false' and fields.get('search'):
                selection |= {('product', str(i)) for i, name in names.items() if fields['search'].lower() in name.lower()}
        if 'massStatus' in path or 'product_action_attribute/save' in path:
            f['entities'] |= selection
            f['selection_evidence'] = 'HAR selected IDs/search (search matches inferred)'


def episodes(forums, tasks, cutoff, names=None):
    result = []
    excluded = superseded_ids(ROOT)
    for directory in sorted((ROOT / 'runs').iterdir()):
        if not directory.is_dir():
            continue
        record = read_json(directory / 'record.json', {})
        baseline = read_json(directory / 'baseline.json', {})
        requests = read_lines(directory / 'proxy_requests.jsonl')
        flows = read_lines(directory / 'proxy.jsonl')
        if not (record or baseline or requests or flows):
            continue
        by_flow = {r['flow_id']: r for r in requests if 'flow_id' in r}
        for r in flows:
            request = by_flow.get(r.get('flow_id'), {})
            r['at'] = request.get('request_ts', r.get('request_ts', r.get('ts')))
            r['entities'] = url_entities(r.get('url'), forums)
            # Persist paths only: Magento URLs may contain session/form keys.
            r['path'] = re.sub(r'/key/[^/]+', '/key/[REDACTED]', urlsplit(r.get('url', '')).path)
        flows = [f for f in flows if f.get('at') is not None and f['at'] <= cutoff]
        enrich_har(directory, flows, names or {})
        times = [float(r['at']) for r in flows if r.get('at') is not None]
        times += [float(r.get('request_ts', r.get('ts'))) for r in requests if r.get('request_ts', r.get('ts'))]
        if not times:
            # Baselines establish an observation, but mtime isn't a reliable request timestamp.
            start = (directory / 'baseline.json').stat().st_mtime if baseline else (directory / 'record.json').stat().st_mtime if record else 0
        else:
            start = min(times)
        if start > cutoff:
            continue
        task = record.get('task', '')
        if not task and baseline:
            task = f"{baseline.get('site')}-{baseline.get('task_id')}"
        result.append({'id': directory.name, 'record': record, 'baseline': baseline, 'flows': flows,
                       'start': start, 'end': max(times, default=start),
                       'targets': baseline_entities(baseline) | tasks.get(task, {}).get('entities', set()),
                       'task': task, 'site': record.get('site', baseline.get('site')),
                       'completed': bool(record) and (directory / 'record.json').stat().st_mtime <= cutoff, 'superseded': directory.name in excluded,
                       'dry_run': bool(record.get('dry_run'))})
    return result


def core_entities(site, table, row):
    ids = identities(site, table, row)
    # Forum co-location is indirect exposure, not evidence of the write's target.
    if table not in ('forum_subscriptions', 'hidden_forums', 'moderators', 'forum_bans'):
        ids = {i for i in ids if i[0] != 'forum'}
    if table in ('comment_votes', 'comments'):
        ids = {i for i in ids if i[0] != 'submission'}
    return ids


def attribute(drift, eps, tasks):
    for d in drift:
        site, table = d['site'], d['table']
        row = d['live'] or d['pristine']
        core = core_entities(site, table, row)
        entities = identities(site, table, row)
        created = d.get('mutation_time') or stamp(row.get('subscribed_at')) or stamp(row.get('timestamp')) or stamp(row.get('created_at'))
        candidates = []
        restored_candidates = []
        for e in eps:
            if e['site'] != site:
                continue
            for f in e['flows']:
                if f.get('method') in ('GET', 'HEAD', 'OPTIONS') or not (f['entities'] & core):
                    continue
                if '/validate/' in f['path'] or '/product_action_attribute/edit/' in f['path']:
                    continue
                if f.get('forwarded') is False or (f.get('status') or 0) >= 400:
                    continue
                fields = f.get('fields', {})
                if table == 'catalog_product_entity_text':
                    field = 'product[' + row['attribute_code'] + ']'
                    if fields.get(field) != row.get('value'):
                        continue
                restored = e.get('record', {}).get('restore_ok') is True and d['pristine'] is not None and any(
                    all(r.get(k) == v for k, v in d['key'].items()) and
                    all(r.get(k) == v['pristine'] for k, v in d['changes'].items() if k in r)
                    for r in e.get('baseline', {}).get('rows', {}).get(table, []))
                if restored:
                    restored_candidates.append(e['id'])
                    continue
                distance = abs(f['at'] - created) if created else None
                candidates.append({'episode': e['id'], 'timestamp': f['at'], 'path': f['path'],
                                   'status': f.get('status'), 'distance_s': distance, 'fields': f.get('fields', {}),
                                   'selection_evidence': f.get('selection_evidence'),
                                   'evidence': 'entity + timestamp' if distance is not None and distance <= 5 else 'entity only'})
        exact = [c for c in candidates if c['evidence'] == 'entity + timestamp']
        # A vote timestamp may mark its last update, not just creation. Keep all candidate writes.
        d['restored_candidate_episodes'] = sorted(set(restored_candidates))
        d['causes'] = exact or candidates
        d['attribution'] = 'timestamp-matched candidate' if exact else 'entity-matched candidates' if candidates else 'unresolved'
        entities |= set(tuple(i) for i in d.get('related_entities', []))
        d['entities'] = sorted(entities)
        d['targeting_tasks'] = sorted(t for t, value in tasks.items() if value['site'] == site and value['entities'] & core)
        since = min((c['timestamp'] for c in (exact or candidates)), default=created or 0)
        d['possible_since'] = since
        direct, indirect, related, observed = [], [], [], []
        for e in eps:
            if e['site'] != site or not e['completed'] or e['start'] <= since:
                continue
            if e['targets'] & core:
                direct.append(e['id'])
            elif e['targets'] & set(tuple(i) for i in d.get('related_products', [])):
                related.append(e['id'])
            elif e['targets'] & entities:
                indirect.append(e['id'])
            if any(f['method'] in ('GET', 'HEAD') and f['entities'] & core for f in e['flows']):
                observed.append(e['id'])
        d['possibly_affected_target_episodes'] = sorted(direct)
        d['possibly_affected_same_forum_episodes'] = sorted(indirect)
        d['possibly_affected_related_product_episodes'] = sorted(related)
        d['later_entity_read_episodes'] = sorted(observed)
        # A pre-image containing this exact drift is stronger than task overlap.
        d['baseline_witnesses'] = []
        for e in eps:
            if e['site'] != site:
                continue
            for r in e['baseline'].get('rows', {}).get(table, []):
                if all(r.get(k) == v for k, v in d['key'].items()) and all(
                    r.get(k) == v['live'] for k, v in d['changes'].items() if k in r
                ) and d['live'] is not None:
                    d['baseline_witnesses'].append(e['id'])
                    break


def analyze():
    manifest = read_json(OUT / 'manifest.json')
    live = {s: read_json(OUT / f'{s}_live.json') for s in SITES}
    pristine = {s: read_json(OUT / f'{s}_pristine.json') for s in SITES}
    forums = {re.sub(r'\s+', '', r['name'].lower()): str(r['id']) for r in pristine['reddit']['forums_lookup']}
    tasks = task_targets(forums)
    names = {r['entity_id']: r['value'] for r in pristine['shopping_admin']['catalog_product_entity_varchar']}
    eps = episodes(forums, tasks, manifest['ended_at'], names)
    drift, excluded, summary = [], [], []
    for site in SITES:
        cutoff = manifest['snapshots'][site + '_live']['started_at'] - 1800
        keys = read_json(OUT / f'{site}_keys.json')
        for table, rows in live[site].items():
            if table.endswith('_lookup'):
                continue
            changes, recent = diff_rows(rows, pristine[site][table], keys[table], cutoff)
            for group in (changes, recent):
                for d in group:
                    d.update(site=site, table=table)
            drift.extend(changes)
            excluded.extend(recent)
            summary.append({'site': site, 'table': table, 'live_rows': len(rows), 'pristine_rows': len(pristine[site][table]),
                            'drift': dict(Counter(d['kind'] for d in changes)), 'recent_excluded': len(recent)})
    # EAV/stock rows have no timestamps: conservatively exclude recently touched parents.
    product_times = {str(r['entity_id']): stamp(r['updated_at']) for r in live['shopping_admin']['catalog_product_entity_lookup']}
    cutoff = manifest['snapshots']['shopping_admin_live']['started_at'] - 1800
    for d in list(drift):
        if d['site'] == 'shopping_admin':
            row = d['live'] or d['pristine']
            updated = product_times.get(str(row.get('entity_id', row.get('product_id'))))
            if updated and updated >= cutoff:
                d['exclusion_reason'] = 'product updated within last 30 minutes; field timestamp unavailable'
                drift.remove(d)
                excluded.append(d)
            elif d['table'] in ('catalog_product_entity_int', 'catalog_product_entity_decimal'):
                d['mutation_time'] = updated
    vote_times = {r['submission_id']: stamp(r['timestamp']) for r in live['reddit']['submission_votes']}
    parents = {r['id']: r for r in live['reddit']['submissions']}
    relations = live['shopping_admin'].get('catalog_product_relation_lookup', read_json(OUT / 'shopping_admin_relations.json', [[]])[0])
    for d in drift:
        row = d['live'] or d['pristine']
        if d['site'] == 'reddit':
            sid = row.get('submission_id', row.get('id') if d['table'] == 'submissions' else None)
            if sid in parents:
                d['related_entities'] = [('forum', str(parents[sid]['forum_id']))]
            if d['table'] == 'submissions' and sid in vote_times:
                d['mutation_time'] = vote_times[sid]
        else:
            pid = row.get('entity_id', row.get('product_id'))
            d['related_products'] = [('product', str(r['parent_id'])) for r in relations if str(r['child_id']) == str(pid)]
            d['related_products'] += [('product', str(i)) for i, name in names.items()
                                      if i != pid and names.get(pid, '').startswith(name + '-')]
            d['related_products'] = sorted(set(d['related_products']))
            d['related_entities'] = d['related_products']
            d['product_relation_note'] = 'DB relations or inferred parent from exact name prefix'
    # A recent vote can change an old parent's score without changing its creation timestamp.
    for d in list(drift):
        cutoff = manifest['snapshots'][d['site'] + '_live']['started_at'] - 1800
        if d.get('mutation_time') and d['mutation_time'] >= cutoff:
            d['exclusion_reason'] = 'associated mutation within last 30 minutes'
            drift.remove(d)
            excluded.append(d)
    attribute(drift, eps, tasks)
    save('drift.json', drift)
    save('excluded_recent.json', excluded)
    for item in summary:
        item['drift'] = dict(Counter(d['kind'] for d in drift if d['site'] == item['site'] and d['table'] == item['table']))
        item['recent_excluded'] = sum(d['site'] == item['site'] and d['table'] == item['table'] for d in excluded)
    save('summary.json', summary)
    save('episode_index.json', [{k: e[k] for k in ('id', 'task', 'site', 'start', 'end', 'completed', 'superseded', 'dry_run')} for e in eps])
    save('task_targets.json', {k: {**v, 'entities': sorted(v['entities'])} for k, v in tasks.items()})
    write_report(manifest, drift, excluded, summary, eps)
    print(json.dumps({'drift_rows': len(drift), 'excluded_recent': len(excluded), 'tables': summary}, indent=2))


def write_report(manifest, drift, excluded, summary, eps):
    categories = {
        'task_target': 'possibly_affected_target_episodes',
        'same_forum': 'possibly_affected_same_forum_episodes',
        'related_product': 'possibly_affected_related_product_episodes',
        'entity_read': 'later_entity_read_episodes',
    }
    groups = {name: set(i for d in drift for i in d[field]) for name, field in categories.items()}
    affected = set().union(*groups.values())
    index = {e['id']: e for e in eps}
    retained = {i for i in affected if not index[i]['superseded'] and not index[i]['dry_run']}
    with (OUT / 'affected_episodes.csv').open('w') as out:
        writer = csv.writer(out, lineterminator='\n')
        writer.writerow(['episode_id', 'task', 'site', 'superseded', 'dry_run', 'possible_exposure', 'drift_rows'])
        for i in sorted(affected):
            e = index[i]
            rows = [d['site'] + '.' + d['table'] + ':' + json.dumps(d['key'], sort_keys=True)
                    for d in drift if any(i in d[field] for field in categories.values())]
            writer.writerow([i, e['task'], e['site'], e['superseded'], e['dry_run'],
                             ';'.join(k for k, v in groups.items() if i in v), ';'.join(rows)])
    save('rerun_estimate.json', {'all_completed_possible': len(affected), 'retained_nondry_possible': len(retained),
        'by_category_all': {k: len(v) for k, v in groups.items()},
        'by_category_retained': {k: len(v & retained) for k, v in groups.items()},
        'episode_ids': sorted(retained), 'interpretation': 'conservative possible exposure, not proven changed outcomes'})
    date = lambda ts: datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec='seconds')
    lines = [
        '# M5c state drift audit',
        f"Snapshot: {date(manifest['snapshots']['reddit_live']['started_at'])} (Reddit), {date(manifest['snapshots']['shopping_admin_live']['started_at'])} (Magento); all times UTC.",
        'Live reads used SELECTs inside repeatable-read READ ONLY transactions; no repair, restore, sweep changes, or live-container lifecycle operations.',
        'References used the exact live image IDs, network=none, no published ports; all audit containers from both attempts were removed.',
        'Image IDs, collection times and cleanup: [manifest](m5c_audit/manifest.json). Collection uses joined parent lookups and the MariaDB timeout setting.',
        'Scope: every non-log Reddit table with user_id=13915, users.biography, and actor-related parent submissions (union queried on both sides); all specified Magento EAV attributes, stock and reviews.',
        'Recent exclusion: last 30 minutes at each live snapshot, including product updated_at for untimestamped EAV/stock. Operational logs/sessions/caches omitted.',
        f"Excluded {len(excluded)} row differences: [recent rows](m5c_audit/excluded_recent.json); no drift row was silently normalized.",
        '', '| Site/table | Only live | Only pristine | Changed | Recent excluded |', '|---|---:|---:|---:|---:|'
    ]
    for item in summary:
        n = item['drift']
        lines.append(f"| {item['site']}.{item['table']} | {n.get('only_live', 0)} | {n.get('only_pristine', 0)} | {n.get('changed', 0)} | {item['recent_excluded']} |")
    lines += ['', f"Total: **{len(drift)} older drifted rows**. Exact primary keys, entity IDs, attributes, live/pristine values, per-row causes and downstream IDs: [drift.json](m5c_audit/drift.json).",
              'Reddit extra-vote submission IDs: ' + ', '.join(str(d['live']['submission_id']) for d in drift if d['table'] == 'submission_votes' and d['kind'] == 'only_live') + '; extra-subscription forum IDs: ' + ', '.join(str(d['live']['forum_id']) for d in drift if d['table'] == 'forum_subscriptions' and d['kind'] == 'only_live') + '.',
              'Magento changed entities by attribute: ' + '; '.join(code + '=' + ','.join(str(d['live']['entity_id']) for d in drift if d['site'] == 'shopping_admin' and (d['live'] or {}).get('attribute_code') == code) for code in ('status', 'price', 'description', 'short_description', 'name') if any((d['live'] or {}).get('attribute_code') == code for d in drift)) + '; exact before/after values are in the row evidence.',
              '', 'Cause candidates (request times joined through proxy_requests.jsonl flow_id; proxy.jsonl supplies paths/status; sanitized HAR fields supply bulk selection/value evidence):']
    causes = {}
    for d in drift:
        for c in d['causes']:
            causes.setdefault(c['episode'], []).append((d, c))
    for episode, values in sorted(causes.items()):
        tables = ', '.join(f"{t} ×{n}" for t, n in sorted(Counter(d['table'] for d, c in values).items()))
        evidence = 'timestamp + entity' if all(c['evidence'] == 'entity + timestamp' for d, c in values) else 'entity + matching value; no field timestamp'
        lines.append(f'- `{episode}`: {tables}; {evidence}.')
    lines += [
        'Bulk action evidence retains allowlisted HAR search/selection/value fields and request paths per cause. Search-to-entity expansion is an inference supported by changed rows and matching timestamps.',
        'Description attribution excludes earlier matching saves with restore_ok=true and pristine pre-images; the remaining off-target save is a candidate, not a proof of unique provenance.',
        '', f"Possibly affected completed episodes: **{len(affected)} total / {len(retained)} retained non-dry episodes**; all IDs and reasons are listed in [affected_episodes.csv](m5c_audit/affected_episodes.csv).",
        'Category counts overlap: ' + '; '.join(f'{k}={len(v)} ({len(v & retained)} retained)' for k, v in groups.items()) + '.',
        'Task-target matches use tasks/*.json, catalog task_ids/evaluator URLs and saved probe targets. Same-forum and related-product matches are possible UI exposure, not changed evaluator outcomes.',
        'Variant/parent association includes exact product-name prefixes when relationships are absent; this is conservatively marked inferred.',
        'Per-row targeting_tasks and downstream lists distinguish absent direct overlap from possible same-forum or product-list navigation; bulk selection inference is retained in each cause.',
        f"Rerun estimate: **up to {len(retained)} retained episodes** under the documented exposure rule; {len(affected)-len(retained)} additional completed artifact(s) are superseded/dry. This is not a proven corruption count. Exact rerun IDs: [estimate](m5c_audit/rerun_estimate.json).",
        'Recommended fix (not applied): initialize one pristine baseline, restore all actor-13915 rows plus affected parent counters, and cover all family product IDs/variants and bulk-action targets; compare against the fixed baseline periodically and before resuming affected cells.',
        'Widening per-episode snapshots alone preserves existing contamination: reconcile drift once before reruns, then enforce the wider restore scope and rerun the listed exposure set.',
        'Limits: a point-in-time scoped audit cannot detect drift already repaired or prove when untimestamped fields first changed. Concurrent activity and missing historical artifacts limit attribution; unrelated-author Reddit rows outside actor-related parents are outside scope.',
        'Completed means record.json existed by the snapshot cutoff; superseded and dry-run artifacts remain separately flagged. Runs created or completed after the snapshot are outside this estimate.',
        'Reproduce: `.venv/bin/python -m tools.background m5c_audit .venv/bin/python -m tools.state_audit collect`, then `.venv/bin/python -m tools.state_audit_report` after collection exits.',
    ]
    if len(lines) > 80:
        raise ValueError('audit report exceeds 80 lines')
    (ROOT / 'logs/m5c_audit.md').write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    analyze()
