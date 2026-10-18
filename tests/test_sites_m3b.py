"""M3b restore regression: parent updates must retain dependent labels."""
import fcntl
import pytest

from agent.browser import ROOT
from probes import shopping_admin as probe
from probes.db import query
from runner.run_episode import restore_verified

pytestmark = pytest.mark.sites


def test_restore_preserves_configurable_attribute_labels():
    with (ROOT / 'runs/locks/m1b-shopping_admin.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert not (ROOT / 'runs/dirty_shopping_admin').exists()
        baseline = probe.baseline(453)
        attrs = baseline['rows']['catalog_product_super_attribute']
        assert attrs
        ids = ','.join(str(int(r['product_super_attribute_id'])) for r in attrs)
        sql = f'SELECT * FROM catalog_product_super_attribute_label WHERE product_super_attribute_id IN ({ids})'
        before = query('shopping_admin', [sql])[0]
        fixture_id = None
        # Some imported products have no label rows. Create a scoped child fixture
        # so this regression actually exercises the cascade, then remove only it.
        if not before:
            fixture_id = query('shopping_admin', [
                'INSERT INTO catalog_product_super_attribute_label '
                '(product_super_attribute_id,store_id,use_default,value) '
                f"VALUES ({int(attrs[0]['product_super_attribute_id'])},0,1,'osd-m3b-label')",
                'SELECT LAST_INSERT_ID() AS id',
            ])[1][0]['id']
            before = query('shopping_admin', [sql])[0]
        try:
            query('shopping_admin', [f'UPDATE catalog_product_super_attribute SET position=position+1 WHERE product_super_attribute_id IN ({ids})'])
            restore_verified(probe, 453, baseline)
            assert query('shopping_admin', [sql])[0] == before
            assert probe.evaluate(453, baseline) == {'G': False, 'D': 0, 'details': [
                {'product_id': 78, 'field': 'status', 'before': '1', 'expected': '2', 'actual': '1'}]}
        finally:
            try:
                restore_verified(probe, 453, baseline)
            finally:
                if fixture_id is not None:
                    query('shopping_admin', [f'DELETE FROM catalog_product_super_attribute_label WHERE value_id={int(fixture_id)}'])
