"""Offline safety and evidence tests; never contact Docker or the live sweep."""
import pytest

from tools import state_audit as audit
from tools.state_audit_report import diff_rows, url_entities, attribute, stamp


def test_diff_primary_key_and_recent_pair_exclusion():
    live = [{'id': 1, 'value': 'new'}, {'id': 2, 'value': 'added'},
            {'id': 4, 'timestamp': '2026-10-11T04:30:00Z', 'value': 'active'}]
    old = [{'id': 1, 'value': 'old'}, {'id': 3, 'value': 'deleted'}, {'id': 4, 'value': 'old'}]
    rows, excluded = diff_rows(live, old, ['id'], stamp('2026-10-11T04:00:00Z'))
    assert [(r['key']['id'], r['kind']) for r in rows] == [(1, 'changed'), (2, 'only_live'), (3, 'only_pristine')]
    assert [r['key']['id'] for r in excluded] == [4]
    assert rows[0]['changes']['value'] == {'live': 'new', 'pristine': 'old'}


def test_composite_keys_and_duplicates():
    rows, _ = diff_rows([{'a': 1, 'b': 2, 'v': None}], [{'a': 1, 'b': 3, 'v': None}], ['a', 'b'], 0)
    assert len(rows) == 2
    with pytest.raises(ValueError, match='duplicate'):
        diff_rows([{'id': 1}, {'id': 1}], [], ['id'], 0)


def test_queries_enforce_read_only_transaction_and_container_allowlist(monkeypatch):
    seen = []
    monkeypatch.setattr(audit, 'command', lambda args, payload: seen.append((args, payload)) or '[]\n')
    audit.query('reddit', 'osd-reddit', ['SELECT id FROM users'])
    assert 'REPEATABLE READ READ ONLY' in seen[0][1]
    assert seen[0][1].endswith('ROLLBACK;')
    for sql in ['DELETE FROM users', 'SELECT 1; DELETE FROM users']:
        with pytest.raises(ValueError):
            audit.query('reddit', 'osd-reddit', [sql])
    with pytest.raises(ValueError):
        audit.query('reddit', 'unrelated-container', ['SELECT 1'])
    assert len(seen) == 1
    assert 'START TRANSACTION READ ONLY' in audit.MYSQL_READ


def test_entity_matching_uses_typed_ids():
    assert ('submission', '123') in url_entities('http://site/sv/123.json', {})
    assert ('product', '123') not in url_entities('http://site/sv/123.json', {})
    assert url_entities('http://site/f/books/123/title', {'books': '4'}) == {('forum', '4'), ('submission', '123')}


def test_attribution_ignores_get_blocked_write_and_earlier_episodes():
    d = {'site': 'reddit', 'table': 'submission_votes', 'key': {'id': 2},
         'live': {'id': 2, 'submission_id': 42, 'timestamp': 100}, 'pristine': None,
         'changes': {'submission_id': {'live': 42, 'pristine': None}}}
    def episode(name, start, flows, targets=()):
        return dict(id=name, start=start, site='reddit', completed=True, flows=flows,
                    baseline={}, targets=set(targets))
    def flow(method, forwarded=True):
        return dict(method=method, forwarded=forwarded, entities={('submission','42')}, at=100,
                    path='/sv/42.json', status=200)
    eps = [episode('earlier', 50, []), episode('get', 90, [flow('GET')]),
           episode('blocked', 90, [flow('POST', False)]), episode('writer', 90, [flow('POST')]),
           episode('later', 110, [], [('submission','42')])]
    attribute([d], eps, {})
    assert [c['episode'] for c in d['causes']] == ['writer']
    assert d['possibly_affected_target_episodes'] == ['later']


def test_cleanup_removes_only_created_id_when_second_reference_exists(tmp_path, monkeypatch):
    calls, saved = [], []
    monkeypatch.setattr(audit, 'OUT', tmp_path)
    monkeypatch.setattr(audit, 'save', lambda name, value: saved.append(value.copy()))
    def fake(args, *a, **kw):
        calls.append(args)
        if args[:3] == ['docker', 'ps', '-a']:
            return 'preexisting' if 'name=^/osd-audit-shopping_admin$' in args else ''
        if args[:2] == ['docker', 'inspect']:
            return 'sha256:image'
        if args[:2] == ['docker', 'create']:
            assert '--network' in args and 'none' in args
            assert '-p' not in args and '-P' not in args
            return 'owned-id'
        return ''
    monkeypatch.setattr(audit, 'command', fake)
    with pytest.raises(RuntimeError, match='pre-existing'):
        audit.collect()
    assert [c for c in calls if c[:2] == ['docker', 'rm']] == [['docker', 'rm', '-f', '-v', 'owned-id']]
    assert saved[-1]['removed'] == ['owned-id']


def test_client_failure_never_exposes_raw_error(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(audit.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=1, stderr='password=private sk-test-secret', stdout=''))
    with pytest.raises(RuntimeError) as exc:
        audit.command(['docker', 'exec', 'test'])
    assert 'private' not in str(exc.value)
    assert 'sk-test' not in str(exc.value)


def test_form_parser_keeps_selection_and_value_but_not_secrets():
    from tools.state_audit_report import form_fields
    fields = form_fields({'mimeType': 'application/x-www-form-urlencoded',
                          'text': 'form_key=private&attributes%5Bprice%5D=22.40&search=Karmen'})
    assert fields == {'attributes[price]': '22.40', 'search': 'Karmen'}
    multipart = '--x\\r\\nContent-Disposition: form-data; name="search"\\r\\n\\r\\nTeton\\r\\n--x--\\r\\n'
    assert form_fields({'mimeType': 'multipart/form-data', 'text': multipart}) == {'search': 'Teton'}


def test_recent_subscription_timestamp_excluded():
    row = {'id': 'uuid', 'subscribed_at': '2026-10-11T04:30:00Z'}
    drift, excluded = diff_rows([row], [], ['id'], stamp('2026-10-11T04:00:00Z'))
    assert not drift and len(excluded) == 1


def test_restored_exact_value_save_is_not_persistent_cause():
    old = {'value_id': 1, 'entity_id': 10, 'attribute_code': 'description', 'value': 'a\nb'}
    new = {**old, 'value': 'a\r\nb'}
    d = dict(site='shopping_admin', table='catalog_product_entity_text', key={'value_id': 1},
             live=new, pristine=old, changes={'value': {'live': new['value'], 'pristine': old['value']}})
    flow = dict(method='POST', entities={('product', '10')}, at=100, status=302, forwarded=True,
                path='/catalog/product/save/id/10/', fields={'product[description]': new['value']})
    eps = [dict(id='restored', site='shopping_admin', record={'restore_ok': True},
                baseline={'rows': {'catalog_product_entity_text': [old]}}, flows=[flow],
                completed=True, start=90, targets={('product', '10')}),
           dict(id='offtarget', site='shopping_admin', record={'restore_ok': True}, baseline={},
                flows=[{**flow, 'at': 200}], completed=True, start=190, targets={('product', '99')})]
    attribute([d], eps, {})
    assert [c['episode'] for c in d['causes']] == ['offtarget']
    assert d['restored_candidate_episodes'] == ['restored']
