"""Regression coverage for expanded family probes and the per-task study gate."""
import pytest
from probes import shopping_admin
from probes.site_common import task_data


@pytest.mark.parametrize('tid', [777, 778, 779, 780, 781, 782])
def test_config_price_requires_every_target(monkeypatch, tid):
    task = task_data(tid)
    targets = shopping_admin.targets(task)
    ins = task['instantiation_dict']
    from decimal import Decimal
    amount = Decimal(ins['amount'].strip('$%'))
    sign = -1 if ins['action'] == 'Reduce' else 1
    before = []
    after = []
    for pid, fields in targets:
        target = Decimal(str(fields['product[price]']))
        # Derive exact fixture baselines, then test any missing target prevents G.
        value = target / (1 + sign * amount / 100) if '%' in ins['amount'] else target - sign * amount
        row = dict(entity_id=pid, attribute_id=77, store_id=0, value=str(value))
        before.append(row)
        after.append(row | {'value': str(target)})
    table = 'catalog_product_entity_decimal'
    baseline = dict(task_id=tid, rows={table: before}, scopes={table: 'test scope'})
    monkeypatch.setattr(shopping_admin, 'query', lambda *args: [after])
    result = shopping_admin.evaluate(task, baseline)
    assert result['G'] and result['D'] == 0
    after[-1] = before[-1]
    result = shopping_admin.evaluate(task, baseline)
    assert not result['G'] and result['D'] == 0


def test_validation_gate_excludes_evaluator_failure(tmp_path, monkeypatch):
    from tools import validate_tasks
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'tasks').mkdir()
    monkeypatch.setattr(validate_tasks, 'ROOT', tmp_path)
    bad = dict(task=599, site='reddit', family='fam-4', G=True, D=0,
               restore_ok=True, eval_pass=False, validated=False,
               notes=['excluded: upstream URL spelling'])
    good = bad | dict(task=595, eval_pass=True, validated=True, notes=[])
    validate_tasks.report({599: bad, 595: good})
    index = (tmp_path / 'tasks/INDEX.md').read_text()
    assert '[595]' in index and '[599]' not in index
    assert '599' in (tmp_path / 'logs/task_validation.md').read_text()


@pytest.mark.parametrize('tid', [464, 543, 544, 545, 546])
def test_description_goal_requires_exact_task_text(monkeypatch, tid):
    task = task_data(tid)
    pid, fields = shopping_admin.targets(task)[0]
    row = dict(entity_id=pid, attribute_id=76, store_id=0,
               value=fields['product[short_description]'])
    table = 'catalog_product_entity_text'
    before = dict(task_id=tid, scopes={table: 'test'}, rows={table: [row | {'value': 'old'}]})
    monkeypatch.setattr(shopping_admin, 'query', lambda *args: [[row]])
    assert shopping_admin.evaluate(task, before)['G']
    row['value'] += ' incorrect'
    result = shopping_admin.evaluate(task, before)
    assert not result['G'] and result['D'] == 0
