"""Immutable M5d snapshots and transactional, differential global state reset.

Run capture once with tools.background. Snapshot bodies are private and ignored;
only their SHA256 manifest is tracked. Call reset only while holding the site lock.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import fcntl
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

from probes.db import MYSQL, sql_literal as literal
from tools import state_audit as audit
from tools.state_audit_report import diff_rows

ROOT = audit.ROOT
DATA = ROOT / 'data/pristine'
MANIFEST = ROOT / 'config/pristine_manifest.json'
ACTOR = 13915


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Permissions are restricted before the first byte (users includes auth data).
    import os
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, separators=(',', ':'))
        stream.write('\n')


@contextmanager
def site_lock(site):
    path = ROOT / f'runs/locks/m1b-{site}.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def projections(site, metadata):
    queries, keys = audit.projections(site, metadata)
    if site == 'reddit':
        queries = {t: f'SELECT * FROM "{t}" WHERE user_id={ACTOR}'
                   for t in queries if t not in ('users', 'forums_lookup')}
        queries['users'] = f'SELECT * FROM users WHERE id={ACTOR}'
        queries['submission_counters'] = 'SELECT id,net_score,ranking,comment_count,last_active FROM submissions'
        keys['submission_counters'] = ['id']
        # A comment vote changes the parent comment score, even on another author.
        queries['comment_counters'] = 'SELECT id,net_score FROM comments'
        keys['comment_counters'] = ['id']
    else:
        queries = {f'catalog_product_entity_{suffix}':
                   f'SELECT * FROM catalog_product_entity_{suffix} WHERE store_id=0'
                   for suffix in ('int', 'decimal', 'varchar', 'text')}
        queries.update(catalog_product_entity='SELECT entity_id,updated_at FROM catalog_product_entity',
                       cataloginventory_stock_item='SELECT * FROM cataloginventory_stock_item',
                       review='SELECT * FROM review', review_detail='SELECT * FROM review_detail')
    return queries, {t: keys[t] for t in queries}


def capture():
    if MANIFEST.exists() or any(DATA.glob('*.json')):
        raise RuntimeError('pristine snapshot already exists; refusing to replace baseline')
    manifest = {'version': 1, 'started_at': time.time(), 'sites': {}, 'removed': []}
    owned = {}
    try:
        for site in audit.SITES:
            name = 'osd-audit-' + site
            if audit.command(['docker', 'ps', '-a', '--filter', f'name=^/{name}$', '--format', '{{.ID}}']).strip():
                raise RuntimeError('pre-existing audit container; refusing to alter it')
            image = audit.command(['docker', 'inspect', '--format', '{{.Image}}', 'osd-' + site]).strip()
            # No published ports; check the exact intended host binding set is empty.
            cid = audit.command(['docker', 'create', '--name', name, '--label', 'osd.audit=m5d',
                                 '--network', 'none', image]).strip()
            owned[site] = cid
            bindings = json.loads(audit.command(['docker', 'inspect', '--format', '{{json .HostConfig.PortBindings}}', cid]))
            if bindings:
                raise RuntimeError('reference must not publish host ports')
            audit.command(['docker', 'start', cid])
            deadline = time.monotonic() + 240
            while True:
                try:
                    metadata = audit.schema(site, name)
                    break
                except RuntimeError:
                    if time.monotonic() > deadline:
                        raise RuntimeError('reference database not ready') from None
                    time.sleep(3)
            queries, keys = projections(site, metadata)
            rows = dict(zip(queries, audit.query(site, name, list(queries.values()))))
            # Also retain the original M5c projections for an independent check.
            old_queries, old_keys = audit.projections(site, metadata)
            old_rows = dict(zip(old_queries, audit.query(site, name, list(old_queries.values()))))
            # M5c parent union on both sides: all submission rows allow later union lookup.
            if site == 'reddit':
                old_rows['submissions'] = audit.query(site, name, ['SELECT * FROM submissions'])[0]
            snapshot = {'site': site, 'image': image, 'queries': queries, 'keys': keys, 'rows': rows,
                        'audit_queries': old_queries, 'audit_keys': old_keys, 'audit_rows': old_rows}
            path = DATA / f'{site}.json'
            save(path, snapshot)
            manifest['sites'][site] = {'image': image, 'container': cid,
                'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'rows': {t: len(r) for t, r in rows.items()}, 'captured_at': time.time()}
            print(site + ': pristine snapshot captured', flush=True)
    finally:
        for cid in owned.values():
            audit.command(['docker', 'rm', '-f', '-v', cid])
            manifest['removed'].append(cid)
        save(ROOT / 'logs/m5d_capture.json', manifest)
    if len(manifest['sites']) != 2:
        raise RuntimeError('incomplete pristine capture')
    manifest['study_start'] = '2026-10-01 00:00:00'  # conservative: includes the entire M5 study
    manifest['completed_at'] = time.time()
    save(MANIFEST, manifest)
    prepare_runtime()


def prepare_runtime():
    """Index the once-captured evidence without changing its immutable bytes.

    Reddit has millions of comments. Only comments voted on by the actor need
    foreign-parent score restoration; query their pristine scores by primary key.
    Keep M5c's full parent records in the archive, out of the hot episode path.
    """
    manifest = json.loads(MANIFEST.read_text())
    for site, entry in manifest['sites'].items():
        if 'runtime_path' in entry:
            continue
        raw = (ROOT / entry['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise RuntimeError('pristine checksum mismatch')
        snapshot = json.loads(raw)
        for key in ('audit_rows', 'audit_queries', 'audit_keys'):
            snapshot.pop(key)
        if site == 'reddit':
            index_path = DATA / 'reddit_comment_counters.sqlite'
            if index_path.exists():
                raise RuntimeError('refusing to overwrite pristine comment index')
            with sqlite3.connect(index_path) as db:
                db.execute('CREATE TABLE counters (id INTEGER PRIMARY KEY, net_score INTEGER)')
                db.executemany('INSERT INTO counters VALUES (?,?)',
                               ((r['id'], r['net_score']) for r in snapshot['rows'].pop('comment_counters')))
            index_path.chmod(0o600)
            entry['comment_index_path'] = str(index_path.relative_to(ROOT))
            entry['comment_index_sha256'] = hashlib.sha256(index_path.read_bytes()).hexdigest()
            snapshot['queries'].pop('comment_counters')
            snapshot['keys'].pop('comment_counters')
        path = DATA / f'{site}_runtime.json'
        save(path, snapshot)
        entry['runtime_path'] = str(path.relative_to(ROOT))
        entry['runtime_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    save(MANIFEST, manifest)


def load(site, archive=False):
    manifest = json.loads(MANIFEST.read_text())
    entry = manifest['sites'][site]
    prefix = '' if archive else 'runtime_'
    raw = (ROOT / entry[prefix + 'path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry[prefix + 'sha256']:
        raise RuntimeError('pristine checksum mismatch')
    snapshot = json.loads(raw)
    live_image = audit.command(['docker', 'inspect', '--format', '{{.Image}}', 'osd-' + site]).strip()
    if live_image != entry['image'] or snapshot['image'] != live_image:
        raise RuntimeError('pristine/live image mismatch')
    if site == 'reddit' and not archive:
        # Capture the union before scoped undo can discard current votes.
        votes = audit.query(site, 'osd-reddit', [f'SELECT comment_id FROM comment_votes WHERE user_id={ACTOR}'])[0]
        ids = {int(r['comment_id']) for r in votes + snapshot['rows']['comment_votes']}
        ids |= {int(r['id']) for r in snapshot['rows']['comments']}
        index = ROOT / entry['comment_index_path']
        if hashlib.sha256(index.read_bytes()).hexdigest() != entry['comment_index_sha256']:
            raise RuntimeError('pristine comment index checksum mismatch')
        with sqlite3.connect(f'file:{index}?mode=ro', uri=True) as db:
            rows = [dict(id=r[0], net_score=r[1]) for r in db.execute(
                'SELECT id,net_score FROM counters WHERE id IN (' + ','.join('?' for _ in ids) + ')', sorted(ids))]
        snapshot['rows']['comment_counters'] = rows
        snapshot['keys']['comment_counters'] = ['id']
        snapshot['queries']['comment_counters'] = 'SELECT id,net_score FROM comments WHERE id IN (' + (','.join(map(str, sorted(ids))) or 'NULL') + ')'
    return snapshot


def current(snapshot):
    queries = snapshot['queries']
    return dict(zip(queries, audit.query(snapshot['site'], 'osd-' + snapshot['site'], list(queries.values()))))


def differences(snapshot, rows):
    # Exact M5c primary-key method, with NO recent-row exclusions while quiescent.
    return {t: diff_rows(rows[t], old, snapshot['keys'][t], float('inf'))[0]
            for t, old in snapshot['rows'].items()}


def summary(diffs):
    # Persist identities and changed column names, never private row values.
    return {'drift': sum(map(len, diffs.values())),
            'tables': {t: len(v) for t, v in diffs.items()},
            'differences': {t: [{'key': d['key'], 'kind': d['kind'], 'columns': sorted(d['changes'])}
                                for d in ds] for t, ds in diffs.items() if ds}}


def ident(name, site):
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]*', name):
        raise ValueError('unsafe SQL identifier')
    q = '"' if site == 'reddit' else '`'
    return q + name + q


def row_operations(site, table, diffs, keys):
    """Separate deletes from upserts so parents keep their existing identities."""
    table = ident(table, site)
    deletes, upserts = [], []
    for d in diffs:
        row = d['pristine']
        where = ' AND '.join(f'{ident(k, site)}={literal(v)}' for k, v in d['key'].items())
        if row is None:
            deletes.append(f'DELETE FROM {table} WHERE {where}')
        elif d['live'] is None:
            upserts.append(f'INSERT INTO {table} (' + ','.join(ident(k, site) for k in row)
                           + ') VALUES (' + ','.join(literal(v) for v in row.values()) + ')')
        else:
            changed = [k for k in d['changes'] if k not in keys]
            if changed:
                upserts.append(f'UPDATE {table} SET ' + ','.join(f'{ident(k, site)}={literal(row[k])}' for k in changed)
                               + ' WHERE ' + where)
    return deletes, upserts


def operations(snapshot, diffs):
    site = snapshot['site']
    tables = list(diffs)
    prelude = []
    if site == 'reddit':
        # Extra posts/comments may have dependent rows by another actor. Delete
        # only their descendant closure; existing comments/posts remain intact.
        post_ids = [d['key']['id'] for d in diffs.get('submissions', []) if d['kind'] == 'only_live']
        comment_ids = [d['key']['id'] for d in diffs.get('comments', []) if d['kind'] == 'only_live']
        if post_ids or comment_ids:
            posts = ','.join(map(str, post_ids)) or 'NULL'
            comments = ','.join(map(str, comment_ids)) or 'NULL'
            prelude = ['CREATE TEMP TABLE osd_reset_comments ON COMMIT DROP AS WITH RECURSIVE doomed AS ('
                       f'SELECT id FROM comments WHERE id IN ({comments}) OR submission_id IN ({posts}) UNION '
                       'SELECT c.id FROM comments c JOIN doomed d ON c.parent_id=d.id) SELECT id FROM doomed',
                       'DELETE FROM notifications WHERE comment_id IN (SELECT id FROM osd_reset_comments) '
                       f'OR submission_id IN ({posts})',
                       'DELETE FROM comment_votes WHERE comment_id IN (SELECT id FROM osd_reset_comments)',
                       'DELETE FROM comments WHERE id IN (SELECT id FROM osd_reset_comments)',
                       f'DELETE FROM submission_votes WHERE submission_id IN ({posts})',
                       f'DELETE FROM submissions_flairs WHERE submission_id IN ({posts})']
        order = ['users', 'submissions', 'comments']
        tables = order + [t for t in tables if t not in order and not t.endswith('_counters')]
        # Insert a missing comment's parent first (PK order need not be chronological).
        pending = {d['key']['id']: d for d in diffs.get('comments', []) if d['kind'] == 'only_pristine'}
        ordered = []
        while pending:
            ready = [k for k, d in pending.items() if d['pristine'].get('parent_id') not in pending]
            if not ready:
                raise RuntimeError('cyclic pristine comment tree')
            ordered.extend(pending.pop(k) for k in ready)
        diffs = dict(diffs)
        diffs['comments'] = ordered + [d for d in diffs.get('comments', []) if d['kind'] != 'only_pristine']
    else:
        tables = ['catalog_product_entity'] + [t for t in tables if t not in ('catalog_product_entity', 'review', 'review_detail')] + ['review', 'review_detail']
        if any(d['kind'] != 'changed' for d in diffs['catalog_product_entity']):
            # The snapshot intentionally projects only updated_at; never delete
            # a product and cascade unrelated relations, or insert an incomplete one.
            raise RuntimeError('product identity drift requires manual reconciliation')
    pairs = {t: row_operations(site, t, diffs[t], snapshot['keys'][t]) for t in tables}
    ops = prelude + [s for t in reversed(tables) for s in pairs[t][0]]
    ops += [s for t in tables for s in pairs[t][1]]
    if site == 'reddit':
        for logical, table in [('submission_counters', 'submissions'), ('comment_counters', 'comments')]:
            # Extra actor-created parents disappear above. Missing unowned parents
            # cannot be recreated safely from counter-only projections.
            owned = {d['key']['id'] for d in diffs[table] if d['kind'] == 'only_pristine'}
            if any(d['kind'] == 'only_pristine' and d['key']['id'] not in owned for d in diffs[logical]):
                raise RuntimeError('unowned parent identity drift')
            changed = [d for d in diffs[logical] if d['kind'] == 'changed']
            ops += row_operations(site, table, changed, ['id'])[1]
    return ops


def execute(site, statements):
    if not statements:
        return
    if site == 'reddit':
        payload = 'BEGIN;\n' + ';\n'.join(statements) + ';\nCOMMIT;'
        audit.command(['docker', 'exec', '-i', 'osd-reddit', 'python3', '-c', audit.PG_READ], payload)
    else:
        audit.command(['docker', 'exec', '-i', 'osd-shopping_admin', 'php', '-r', MYSQL],
                      json.dumps(["SET SESSION sql_mode='NO_BACKSLASH_ESCAPES'", *statements]))


def write_targets(directory):
    """HAR body selection plus proxy paths; no credential fields are retained.

    Repeated selected[] values must not collapse to the last value. An excluded
    selection or incomplete HAR is safe because reset also diffs ALL products.
    """
    ids = set()
    if directory is None:
        return ids
    directory = Path(directory)
    requests = []
    har = directory / 'network.har'
    if har.exists():
        requests += [e['request'] for e in json.loads(har.read_text()).get('log', {}).get('entries', [])]
    for name in ('proxy_requests.jsonl', 'proxy.jsonl'):
        proxy = directory / name
        if proxy.exists():
            for line in proxy.read_text().splitlines():
                try:
                    requests.append(json.loads(line))
                except ValueError:
                    continue  # an interrupted request journal may end mid-line
    for request in requests:
        if request.get('method', '').upper() in ('GET', 'HEAD', 'OPTIONS', ''):
            continue
        path = urlsplit(request.get('url', request.get('path', ''))).path
        if '/catalog/product' not in path:
            continue
        ids.update(int(x) for x in re.findall(r'/(?:id|product_id)/(\d+)', path))
        post = request.get('postData', {})
        fields = list(parse_qs(urlsplit(request.get('url', '')).query).items())
        fields += list(parse_qs(post.get('text', '')).items())
        fields += [(p['name'], [p.get('value', '')]) for p in post.get('params', [])]
        fields += [(k, [v]) for k, v in re.findall(r'name="([^"\n]+)"\r\n\r\n(.*?)(?=\r\n--)', post.get('text', ''), re.S)]
        for key, values in fields:
            if re.fullmatch(r'(?:selected|product|products|product_ids|ids)(?:\[\d*\])?', key):
                for value in values:
                    ids.update(int(n) for n in re.findall(r'\b\d+\b', value))
    return ids


def check(site, snapshot=None):
    snapshot = snapshot or load(site)
    return summary(differences(snapshot, current(snapshot)))


def reset(site, episode_dir=None, started_at=None):
    start = time.monotonic()
    snapshot = load(site)
    rows = current(snapshot)
    diffs = differences(snapshot, rows)
    targets = write_targets(episode_dir) if site == 'shopping_admin' else set()
    if site == 'shopping_admin' and started_at is not None:
        cutoff = datetime.fromtimestamp(started_at, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        targets.update(int(r['entity_id']) for r in rows['catalog_product_entity'] if str(r['updated_at']) >= cutoff)
    ops = operations(snapshot, diffs)
    execute(site, ops)
    after = check(site, snapshot)
    report = {'site': site, 'at': time.time(), 'before': summary(diffs), 'after': after,
              'statements': len(ops), 'write_or_timestamp_targets': sorted(targets),
              'scope': 'all pristine projections (superset of episode/study targets)',
              'seconds': round(time.monotonic() - start, 3)}
    if episode_dir:
        save(Path(episode_dir) / 'global_reset.json', report)
    if after['drift']:
        raise RuntimeError(f'global reset drift: {after["drift"]} rows')
    return report


def audit_check(site):
    """Repeat M5c projections against the fixed image with no recent exclusion."""
    snapshot = load(site, archive=True)
    queries = dict(snapshot['audit_queries'])
    old = dict(snapshot['audit_rows'])
    if site == 'reddit':
        live_related = audit.query(site, 'osd-' + site, [queries['submissions']])[0]
        actor = snapshot['rows']
        ids = {r['id'] for r in live_related} | {r['id'] for r in actor['submissions']}
        ids |= {r['submission_id'] for t in ('submission_votes', 'comments') for r in actor[t]}
        queries['submissions'] = 'SELECT * FROM submissions WHERE id IN (' + ','.join(map(str, sorted(ids))) + ')'
        old['submissions'] = [r for r in old['submissions'] if r['id'] in ids]
    rows = dict(zip(queries, audit.query(site, 'osd-' + site, list(queries.values()))))
    diffs = {t: diff_rows(rows[t], old[t], snapshot['audit_keys'][t], float('inf'))[0]
             for t in queries if not t.endswith('_lookup')}
    return summary(diffs)


def reconcile():
    report = {'started_at': time.time(), 'sites': {}}
    for site in audit.SITES:
        with site_lock(site):
            report['sites'][site] = reset(site)
            report['sites'][site]['m5c_check'] = audit_check(site)
        save(ROOT / 'logs/m5d_reconcile.json', report)
        if report['sites'][site]['m5c_check']['drift']:
            raise RuntimeError('M5c check still has drift')
    report['completed_at'] = time.time()
    save(ROOT / 'logs/m5d_reconcile.json', report)
    print(json.dumps({s: r['after']['drift'] for s, r in report['sites'].items()}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['capture', 'reconcile', 'check'])
    args = p.parse_args()
    if args.action == 'capture':
        capture()
    elif args.action == 'reconcile':
        reconcile()
    else:
        for site in audit.SITES:
            with site_lock(site):
                result = check(site)
            print(json.dumps({'site': site, **result}))
            if result['drift']:
                raise SystemExit(1)
