"""M5c read-only state audit. Run via tools.background; never restores live state.

Collection uses independent, repeatable-read, read-only database transactions.
Reference ownership is tracked by container ID; no pre-existing container is reused
or removed. Only explicit, data-only projections are persisted (no credentials).
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from probes.db import PG

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'logs/m5c_audit'
SITES = ('reddit', 'shopping_admin')
PG_READ = PG.replace('statement_timeout=2000', 'statement_timeout=45000').replace('timeout=2.5', 'timeout=50')
MYSQL_READ = r"""
try {
 $c=(include '/var/www/magento2/app/etc/env.php')['db']['connection']['default'];
 $db=new PDO('mysql:host='.$c['host'].';dbname='.$c['dbname'],$c['username'],$c['password'],[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
 $db->exec('SET SESSION max_statement_time=45');
 $db->exec('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');
 $db->exec('START TRANSACTION READ ONLY');
 $out=[];
 foreach(json_decode(stream_get_contents(STDIN),true) as $sql) {
  $out[]=$db->query($sql)->fetchAll(PDO::FETCH_ASSOC);
 }
 $db->rollBack(); echo json_encode($out);
} catch(Throwable $e) {fwrite(STDERR,'read-only database query failed');exit(1);}
"""


def command(args, payload=None, timeout=65):
    try:
        p = subprocess.run(args, input=payload, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError('audit command timed out') from None
    if p.returncode:
        # Never propagate commands, credential-bearing diagnostics or server replies.
        raise RuntimeError('audit command failed (exit %s)' % p.returncode)
    return p.stdout


def query(site, container, statements):
    if container not in (f'osd-{site}', f'osd-audit-{site}') or site not in SITES:
        raise ValueError('container outside audit allowlist')
    if any(not re.match(r'^SELECT\s', s, re.I) or ';' in s for s in statements):
        raise ValueError('only single SELECT statements are allowed')
    if site == 'reddit':
        selects = ["SELECT COALESCE(jsonb_agg(r),'[]'::jsonb) FROM (" + s + ') r' for s in statements]
        payload = 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n' + ';\n'.join(selects) + ';\nROLLBACK;'
        result = command(['docker', 'exec', '-i', container, 'python3', '-c', PG_READ], payload)
        return [json.loads(line) for line in result.splitlines() if line.strip()]
    return json.loads(command(['docker', 'exec', '-i', container, 'php', '-r', MYSQL_READ], json.dumps(statements)))


def save(name, value):
    OUT.mkdir(exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)


def schema(site, container):
    where = "table_schema='public'" if site == 'reddit' else 'table_schema=DATABASE()'
    return query(site, container, [
        f'SELECT table_name,column_name,data_type FROM information_schema.columns WHERE {where}',
        'SELECT k.table_name,k.column_name,k.ordinal_position FROM information_schema.key_column_usage k '
        'JOIN information_schema.table_constraints t ON t.constraint_name=k.constraint_name '
        'AND t.table_schema=k.table_schema AND t.table_name=k.table_name '
        f"WHERE t.constraint_type='PRIMARY KEY' AND k.{where} ORDER BY k.table_name,k.ordinal_position"
    ])


def projections(site, metadata):
    columns, keys = {}, {}
    for r in metadata[0]:
        columns.setdefault(r['table_name'], []).append(r['column_name'])
    for r in metadata[1]:
        keys.setdefault(r['table_name'], []).append(r['column_name'])
    queries = {}
    if site == 'reddit':
        # Include every actor-owned row, excluding operational log/session/cache tables.
        for table, cols in sorted(columns.items()):
            if 'user_id' not in cols or re.search(r'log|session|cache', table):
                continue
            safe = [c for c in cols if not re.search(r'password|token|secret|salt|email|ip_address', c)]
            queries[table] = 'SELECT ' + ','.join('"'+c+'"' for c in safe) + f' FROM "{table}" WHERE user_id=13915'
        queries['users'] = 'SELECT id,biography FROM users WHERE id=13915'
        # Parent scores can drift even when the parent is owned by someone else.
        queries['submissions'] = ('SELECT * FROM submissions WHERE user_id=13915 UNION '
                                  'SELECT s.* FROM submissions s JOIN submission_votes v ON s.id=v.submission_id WHERE v.user_id=13915 UNION '
                                  'SELECT s.* FROM submissions s JOIN comments c ON s.id=c.submission_id WHERE c.user_id=13915')
        queries['forums_lookup'] = 'SELECT id,name FROM forums'
    else:
        for suffix in ('int', 'decimal', 'varchar', 'text'):
            table = 'catalog_product_entity_' + suffix
            queries[table] = (f'SELECT e.*,a.attribute_code FROM {table} e JOIN eav_attribute a '
                              "ON a.attribute_id=e.attribute_id WHERE a.attribute_code IN "
                              "('status','price','name','description','short_description')")
        queries['cataloginventory_stock_item'] = 'SELECT item_id,product_id,stock_id,qty,is_in_stock FROM cataloginventory_stock_item'
        queries['review'] = 'SELECT review_id,entity_id,entity_pk_value,status_id,created_at FROM review'
        queries['review_detail'] = 'SELECT d.*,r.status_id,r.entity_pk_value,r.created_at FROM review_detail d JOIN review r ON r.review_id=d.review_id'
        queries['catalog_product_relation_lookup'] = 'SELECT parent_id,child_id FROM catalog_product_relation'
        queries['catalog_product_entity_lookup'] = 'SELECT entity_id,created_at,updated_at FROM catalog_product_entity'
    return queries, keys


def collect():
    OUT.mkdir(exist_ok=True)
    manifest = {'started_at': time.time(), 'images': {}, 'references': {}, 'removed': [], 'snapshots': {}}
    owned = {}
    try:
        for site in SITES:
            name = 'osd-audit-' + site
            existing = command(['docker', 'ps', '-a', '--filter', f'name=^/{name}$', '--format', '{{.ID}}']).strip()
            if existing:
                raise RuntimeError('pre-existing audit container; refusing to alter it')
            image = command(['docker', 'inspect', '--format', '{{.Image}}', 'osd-' + site]).strip()
            manifest['images'][site] = image
            cid = command(['docker', 'create', '--name', name, '--label', 'osd.audit=m5c', '--network', 'none', image]).strip()
            owned[site] = cid
            manifest['references'][site] = cid
            save('manifest.json', manifest)
            command(['docker', 'start', cid])
        for site in SITES:
            reference = 'osd-audit-' + site
            deadline = time.monotonic() + 240
            while True:
                try:
                    query(site, reference, ['SELECT 1 AS ready'])
                    break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'{site} reference database did not become ready') from None
                    time.sleep(3)
            print(site + ': reference database ready', flush=True)
            metadata = schema(site, reference)
            queries, keys = projections(site, metadata)
            save(site + '_schema.json', metadata)
            save(site + '_keys.json', keys)
            # The union of actor-related parent IDs must be queried on BOTH sides.
            if site == 'reddit':
                parent_ids = set()
                for container in ('osd-' + site, reference):
                    rows = query(site, container, [queries['submissions']])[0]
                    parent_ids.update(r['id'] for r in rows)
                queries['submissions'] = 'SELECT * FROM submissions WHERE user_id=13915' + (
                    ' OR id IN (' + ','.join(map(str, sorted(parent_ids))) + ')' if parent_ids else '')
            for label, container in (('pristine', reference), ('live', 'osd-' + site)):
                before = time.time()
                data = dict(zip(queries, query(site, container, list(queries.values()))))
                save(site + '_' + label + '.json', data)
                manifest['snapshots'][site + '_' + label] = {'started_at': before, 'ended_at': time.time(), 'rows': {t: len(r) for t, r in data.items()},
                    'sha256': hashlib.sha256((OUT / (site + '_' + label + '.json')).read_bytes()).hexdigest()}
                print(site + ': ' + label + ' captured', flush=True)
    finally:
        for site, cid in owned.items():
            # Only IDs successfully created by this invocation can reach removal.
            for attempt in range(3):
                try:
                    command(['docker', 'rm', '-f', '-v', cid])
                    manifest['removed'].append(cid)
                    break
                except RuntimeError:
                    if attempt == 2:
                        manifest.setdefault('cleanup_errors', []).append(cid)
                    time.sleep(1)
        manifest['ended_at'] = time.time()
        save('manifest.json', manifest)
        if manifest.get('cleanup_errors'):
            raise RuntimeError('audit reference cleanup failed; see manifest')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['collect'])
    args = parser.parse_args()
    collect()
