"""Synthetic M6 analysis: no sweep, browser, network, or credential access."""
import csv
import hashlib
import json
from itertools import product
from pathlib import Path
import sqlite3

import pytest

from tools import analyze as a


@pytest.fixture
def study(tmp_path):
    (tmp_path / 'tasks').mkdir()
    (tmp_path / 'runs').mkdir()
    (tmp_path / 'tasks/INDEX.md').write_text('[1](reddit_1.json)\n[2](reddit_2.json)\n')
    for task in (1, 2, 3):
        (tmp_path / f'tasks/reddit_{task}.json').write_text(json.dumps(
            dict(id=f'reddit-{task}', family='fam-6', intent=f'Change biography {task}.')))
    db = sqlite3.connect(tmp_path / 'runs/m5_queue.sqlite')
    db.executescript('''CREATE TABLE queue (id INTEGER PRIMARY KEY, name TEXT, task TEXT,
        model TEXT, policy TEXT, condition TEXT, seed INTEGER, status TEXT,
        episode_id TEXT, started_at REAL);
        CREATE TABLE history (queue_id INTEGER, episode_id TEXT, error TEXT, timestamp REAL);''')
    (tmp_path / 'runs/records.jsonl').touch()
    (tmp_path / 'runs/superseded.jsonl').touch()

    def add(episode, task=1, stream='m5-main', status='done', publish=True, trace=True, **kw):
        row = dict(episode=episode, task=f'reddit-{task}', model=a.MITIGATION_MODEL,
                   policy='B0', condition='C0', seed=0, dry_run=False, error_kind=None,
                   error='', G=True, D=0, eval_pass=True, restore_ok=True, browser_killed=False,
                   fault_fired=False, n_matched_writes=1, readback_agent=False,
                   rollback_before_claim=None, claim='DONE', family='fam-6', steps=2,
                   cost=.01, seconds=3., final_message='DONE: saved')
        row.update(kw)
        key = (stream, f'tasks/reddit_{task}.json', row['model'], row['policy'], row['condition'], row['seed'])
        existing = db.execute('SELECT id FROM queue WHERE name=? AND task=? AND model=? AND policy=? AND condition=? AND seed=?', key).fetchone()
        if existing:
            qid = existing[0]
            db.execute('UPDATE queue SET episode_id=?,status=? WHERE id=?', (episode, status, qid))
        else:
            qid = db.execute('INSERT INTO queue (name,task,model,policy,condition,seed,status,episode_id,started_at) VALUES (?,?,?,?,?,?,?,?,?)',
                             (*key, status, episode, 0)).lastrowid
        if status != 'running':
            db.execute('INSERT INTO history VALUES (?,?,?,?)', (qid, episode, '', qid))
        db.commit()
        directory = tmp_path / 'runs' / episode
        directory.mkdir(exist_ok=True)
        if publish:
            (directory / 'record.json').write_text(json.dumps(row))
        if trace:
            (directory / 'trace.jsonl').write_text(json.dumps(dict(step=1, action="fill('x', '"+'z'*150+"')"))+'\n')
        with (tmp_path / 'runs/records.jsonl').open('a') as out:
            out.write(json.dumps(row)+'\n')
        return row
    yield tmp_path, add, db
    db.close()


def test_selection_dedupe_superseded_and_harness(study):
    root, add, _ = study
    add('older')
    add('newer', G=False)  # latest valid plan wins
    add('error-after-valid', error_kind='rate_limit', G=None)
    add('superseded', condition='F1', fault_fired=True)
    (root / 'runs/superseded.jsonl').write_text('{"episode_id":"superseded"}\n')
    add('dry', dry_run=True)
    add('extra', task=3)
    add('unrelated', stream='pilot')
    add('wrong-main-policy', policy='B2')
    add('running', task=2, status='running')
    add('missing-record', task=2, condition='F1', publish=False)
    add('missing-trace', task=2, condition='F2', trace=False)
    add('mitigation', stream='m5-mitigation', policy='B1', condition='F1', fault_fired=True)
    add('action-error', task=2, condition='F3', fault_fired=True,
        error_kind='action_error', error='invalid click')
    add('restore-failure', task=2, condition='F4', browser_killed=True, restore_ok=False)
    add('missing-outcome', task=2, condition='F5', eval_pass=None)
    add('unexposed', task=2, condition='F6', fault_fired=False)
    # A torn append must not prevent analysis of the published prefix.
    with (root / 'runs/records.jsonl').open('ab') as out:
        out.write(b'{"episode":')
    rows, errors, _, _, _, _, meta = a.capture(root)
    assert {r['episode'] for r in rows} == {'newer', 'mitigation', 'action-error', 'unexposed'}
    assert {r['episode'] for r in errors} == {'error-after-valid', 'restore-failure', 'missing-outcome'}
    counts = meta['selection_counts']
    assert counts['duplicate_valid_plans'] == 1
    assert counts['superseded'] == counts['dry_run'] == counts['outside_index'] == 1
    assert counts['outside_sweeps'] == 2
    assert counts['incomplete_or_active'] == 3
    assert counts['partial_records_tail'] == 1
    assert counts['valid_selected'] == 4 and counts['exposed'] == 3
    assert meta['queued_tasks_outside_index'] == ['tasks/reddit_3.json']
    assert meta['duplicate_plans'][0]['discarded'] == 1


def test_wilson_and_exact_mcnemar():
    assert a.wilson(0, 0) == (None, None)
    assert a.wilson(0, 10) == pytest.approx((0, .2775327998628892))
    assert a.wilson(5, 10) == pytest.approx((.236593090512564, .763406909487436))
    assert a.wilson(10, 10) == pytest.approx((.722467200137111, 1))
    assert a.mcnemar(0, 0) == 1
    assert a.mcnemar(0, 5) == .0625
    assert a.mcnemar(1, 7) == .0703125
    assert a.mcnemar(7, 1) == a.mcnemar(1, 7)
    assert a.mcnemar(10, 10) == 1


@pytest.mark.parametrize('stream,policy', [('m5-main', 'B0'), ('m5-mitigation', 'B1')])
def test_latest_harness_failures_and_superseded_cell_counts(study, stream, policy):
    root, add, _ = study
    def attempt(episode, **kw):
        return add(episode, stream=stream, policy=policy, **kw)

    attempt('recovered-error', error_kind='rate_limit')
    attempt('recovered')
    superseded_error = attempt('superseded-error', error_kind='infrastructure')
    attempt('old-restore-error', seed=1, error_kind='infrastructure')
    attempt('latest-restore-error', seed=1, restore_ok=False)
    attempt('action-error', seed=2, error_kind='action_error', error='invalid click')
    attempt('latest-invalid', seed=3, eval_pass=None)
    attempt('superseded-valid', seed=4, publish=False, trace=False)
    attempt('superseded-other-model', model='other')
    attempt('superseded-dry', dry_run=True)
    attempt('superseded-outside-index', task=3)
    add('superseded-outside-sweep', stream='pilot')
    # Neither duplicate publication nor duplicate supersession multiplies counts.
    with (root / 'runs/records.jsonl').open('a') as out:
        out.write(json.dumps(superseded_error) + '\n')
    ids = ['superseded-error', 'superseded-error', 'superseded-valid',
           'superseded-other-model', 'superseded-dry', 'superseded-outside-index',
           'superseded-outside-sweep']
    (root / 'runs/superseded.jsonl').write_text(''.join(
        json.dumps({'episode_id': episode}) + '\n' for episode in ids))

    rows, errors, superseded, _, _, _, meta = a.capture(root)
    assert {r['episode'] for r in rows} == {'recovered', 'action-error'}
    assert {r['episode'] for r in errors} == {'latest-restore-error', 'latest-invalid'}
    assert {r['episode'] for r in superseded} == {
        'superseded-error', 'superseded-valid', 'superseded-other-model'}
    assert meta['selection_counts']['harness_errors'] == 2
    assert meta['selection_counts']['superseded'] == 3
    assert meta['harness_error_kinds'] == {'restore_failure': 1, 'missing_or_invalid_outcome': 1}

    _, tables = a.analyze(root)
    names = ['mitigation', 'mitigation_itt']
    if stream == 'm5-main':
        names += ['main_by_model_condition', 'main_itt']
    for name in names:
        for row in tables[name]:
            in_cell = (row['condition'] == 'C0' and row.get('policy', policy) == policy
                       and not (stream == 'm5-main' and name.startswith('mitigation')))
            expected = (2, 2, 3 if row['model'] == 'ALL' else 2) if in_cell else (0, 0, 0)
            if row['model'] == 'other' and in_cell:
                expected = (0, 0, 1)
            assert tuple(row[k] for k in ('n', 'n_harness_err', 'n_superseded')) == expected
        with (root / f'analysis/{name}.csv').open() as source:
            exported = list(csv.DictReader(source))
        assert [int(r['n_superseded']) for r in exported] == [r['n_superseded'] for r in tables[name]]
        assert r'n\_superseded' in (root / f'analysis/{name}.tex').read_text()


@pytest.mark.parametrize('stream,policy', [('m5-main', 'B0'), ('m5-mitigation', 'B1')])
def test_budget_deferrals_are_not_episodes(study, stream, policy):
    root, add, _ = study

    def attempt(episode, **kw):
        return add(episode, stream=stream, policy=policy, **kw)

    attempt('valid')
    attempt('harness', seed=1, error_kind='infrastructure')
    # A deferral must neither replace a valid outcome nor hide a genuine failure.
    deferred = attempt('budget-after-valid', error='BudgetExceeded: window full')
    attempt('budget-after-harness', seed=1, error_kind='budget', G=None, D=None)
    for seed, kind in enumerate(('budget_deferred', 'budget_deferral', 'budget_wait'), 2):
        attempt(f'budget-{kind}', seed=seed, error_kind=kind)
    attempt('budget-recovered', seed=5, error_kind='budget')
    attempt('recovered', seed=5)
    attempt('superseded-budget', seed=6, error_kind='budget', publish=False, trace=False)
    (root / 'runs/superseded.jsonl').write_text('{"episode_id":"superseded-budget"}\n')
    attempt('budget-dry', seed=7, error_kind='budget', dry_run=True)
    attempt('budget-outside-index', task=3, error_kind='budget')
    add('budget-outside-sweep', stream='pilot', error_kind='budget')
    attempt('budget-incomplete', seed=8, error_kind='budget', publish=False)
    with (root / 'runs/records.jsonl').open('a') as out:
        out.write(json.dumps(deferred) + '\n')

    rows, errors, superseded, deferred, _, _, meta = a.capture(root)
    assert {r['episode'] for r in rows} == {'valid', 'recovered'}
    assert [r['episode'] for r in errors] == ['harness']
    assert [r['episode'] for r in superseded] == ['superseded-budget']
    assert len(deferred) == meta['selection_counts']['n_budget_deferred'] == 6
    assert meta['harness_error_kinds'] == {'infrastructure': 1}
    _, tables = a.analyze(root)
    for name in ('main_by_model_condition', 'main_itt', 'mitigation', 'mitigation_itt'):
        for row in tables[name]:
            in_cell = (row['condition'] == 'C0' and row.get('policy', policy) == policy
                       and (stream == 'm5-main' or name.startswith('mitigation')))
            in_cell = in_cell and not (stream == 'm5-main' and name.startswith('mitigation'))
            assert (row['n'], row['n_harness_err'], row['n_superseded'], row['n_budget_deferred']) == (
                (2, 1, 1, 6) if in_cell else (0, 0, 0, 0))
        with (root / f'analysis/{name}.csv').open() as source:
            exported = list(csv.DictReader(source))
        assert [int(r['n_budget_deferred']) for r in exported] == [
            r['n_budget_deferred'] for r in tables[name]]
        assert r'n\_budget\_deferred' in (root / f'analysis/{name}.tex').read_text()

    # Removing only deferrals must leave every other table value unchanged.
    records = root / 'runs/records.jsonl'
    records.write_text(''.join(line + '\n' for line in records.read_text().splitlines()
                             if not a.budget_deferred(json.loads(line))
                             or json.loads(line)['episode'] == 'superseded-budget'))
    _, without = a.analyze(root)
    for name, table in tables.items():
        assert [{k: v for k, v in row.items() if k != 'n_budget_deferred'} for row in table] == [
            {k: v for k, v in row.items() if k != 'n_budget_deferred'} for row in without[name]]


@pytest.mark.parametrize('goal,damage,claim,eval_pass,expected', [
    (False, 0, 'DONE', True, (False, True, False, False, True, False)),
    (True, 0, 'NOT DONE', False, (False, False, True, False, False, True)),
    (True, 0, 'NONE', True, (False, False, True, False, False, False)),
    (True, 0, 'DONE', True, (True, False, False, True, False, False)),
    (True, 2, 'DONE', True, (False, False, False, True, True, False)),
    (False, 0, 'NONE', False, (False, False, False, True, False, False)),
])
def test_derived_fields(goal, damage, claim, eval_pass, expected):
    row = dict(G=goal, D=damage, claim=claim, eval_pass=eval_pass, condition='F3',
               fault_fired=True, n_matched_writes=3, readback_agent=True, rollback_before_claim=False)
    result = a.derive(row)
    assert tuple(result[k] for k in ('CS', 'FS', 'MS', 'claim_ok', 'EFP', 'EFF')) == expected
    assert result['W'] and result['exposed'] and result['retry_storm'] and result['RB']
    assert result['n_writes'] == 3 and result['claim_before_rollback']
    assert not a.derive(row | {'n_matched_writes': 0})['exposed']
    assert not a.derive(row | {'fault_fired': False})['exposed']
    assert a.derive(row | {'condition': 'C0', 'fault_fired': False})['exposed']
    assert a.derive(row | {'condition': 'C0'})['claim_before_rollback'] is None
    assert a.derive(row | {'rollback_before_claim': None})['claim_before_rollback'] is True
    assert a.derive(row | {'claim': 'unknown'})['claim'] == 'NONE'


def test_outputs_pairing_and_input_immutability(study):
    root, add, db = study
    add('c0')
    add('f1', condition='F1', G=False, eval_pass=True, fault_fired=True, n_matched_writes=3)
    add('c0-2', task=2, G=False, eval_pass=False)
    add('f1-2', task=2, condition='F1', fault_fired=False)  # unexposed is excluded
    add('harness', task=2, condition='F1', error_kind='infrastructure')
    add('b1', stream='m5-mitigation', policy='B1', condition='F1', fault_fired=True,
        n_verifications=2, n_done_refused=1, cbr_prompted=1, extra_llm_calls=2, policy_cost=.005)
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / 'runs').rglob('*') if p.is_file()}
    metadata, tables = a.analyze(root)
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / 'runs').rglob('*') if p.is_file()}
    assert before == after
    assert db.execute('SELECT count(*) FROM history').fetchone()[0] == 6
    all_f1 = next(r for r in tables['main_by_model_condition'] if r['model'] == 'ALL' and r['condition'] == 'F1')
    assert (all_f1['n'], all_f1['n_exposed'], all_f1['n_harness_err']) == (2, 1, 1)
    assert all_f1['FS'] == all_f1['retry_storm'] == 1
    assert all_f1['mean_n_writes'] == 3
    paired = next(r for r in tables['main_paired'] if r['model'] == 'ALL' and r['condition'] == 'F1')
    assert paired['n_pairs'] == 1 and paired['FS_b'] == 0 and paired['FS_c'] == 1
    assert paired['CS_b'] == 1 and paired['CS_c'] == 0 and paired['CS_p'] == 1
    mp = next(r for r in tables['mitigation_paired'] if r['policy'] == 'B1')
    assert mp['n_pairs'] == 1 and mp['FS_b'] == 1 and mp['CS_c'] == 1
    mit = next(r for r in tables['mitigation'] if r['policy'] == 'B1' and r['condition'] == 'F1')
    assert mit['n_verifications'] == 2 and mit['policy_cost'] == .005
    data = json.loads((root / 'analysis/headline.json').read_text())
    assert data['fault_FS']['rate'] == 1 and data['C0_FS']['rate'] == .5
    assert data['policies']['B1']['CS']['rate'] == 1
    assert data['MS_F4_F5']['n'] == 0 and data['MS_F4_F5']['rate'] is None
    assert data['metadata'] == metadata
    for name in tables:
        assert (root / f'analysis/{name}.tex').read_text().count('\\toprule') >= 1
        assert list(csv.DictReader((root / f'analysis/{name}.csv').open()))
    parsed = list(csv.DictReader((root / 'analysis/main_by_model_condition.csv').open()))
    assert next(r for r in parsed if r['model'] == 'ALL' and r['condition'] == 'F1')['FS'] == '100.0'
    example = (root / 'analysis/examples.md').read_text()
    assert '### f1' in example and '### b1' not in example
    assert len(next(line[3:] for line in example.splitlines() if line.startswith('1. '))) == 100
    assert 'DONE: saved' in example and 'Change biography 1.' in example
    first = {p.name: p.read_bytes() for p in (root / 'analysis').iterdir() if p.name != 'headline.json'}
    a.analyze(root)
    assert first == {p.name: p.read_bytes() for p in (root / 'analysis').iterdir() if p.name != 'headline.json'}


def test_active_changed_and_mismatching_artifacts(study, monkeypatch):
    root, add, _ = study
    add('active')
    (root / 'runs/active_reddit.json').write_text(json.dumps(dict(baseline_path=str(root/'runs/active/baseline.json'))))
    add('mismatch', condition='F1')
    (root / 'runs/mismatch/record.json').write_text('{}')
    add('torn', condition='F2')
    (root / 'runs/torn/trace.jsonl').write_text('{"step":')
    add('stable', condition='F3')
    rows, _, _, _, _, _, meta = a.capture(root)
    assert [r['episode'] for r in rows] == ['stable']
    assert meta['selection_counts']['incomplete_or_active'] == 3
    original = a.manifest
    calls = []
    def changing(directory):
        result = original(directory)
        if directory.name == 'stable':
            calls.append(1)
            if len(calls) == 3:
                result['new-file'] = (1, 1, 1, 1)
        return result
    monkeypatch.setattr(a, 'manifest', changing)
    with pytest.raises(a.SnapshotChanged):
        a.capture(root)


def test_aggregate_append_uses_captured_prefix(study, monkeypatch):
    root, add, _ = study
    add('first')
    original = a.bounded_read
    def append_after_read(path):
        data = original(path)
        if path.name == 'records.jsonl':
            add('after', task=2)
        return data
    monkeypatch.setattr(a, 'bounded_read', append_after_read)
    rows, _, _, _, _, _, meta = a.capture(root)
    assert [r['episode'] for r in rows] == ['first']
    assert meta['selection_counts']['records_lines'] == 1


@pytest.mark.parametrize('exposed_only', [True, False])
def test_pair_keys_include_model_and_common_seed(exposed_only):
    rows = []
    for model, condition, seed, position in [('a', 'C0', 0, 1), ('a', 'F1', 0, 2),
        ('a', 'C0', 1, 3), ('a', 'F1', 1, 4), ('a', 'C0', 2, 5),
        ('b', 'C0', 0, 6), ('b', 'F1', 1, 7), ('c', 'F1', 0, 8)]:
        rows.append(dict(model=model, task='same', condition=condition, seed=seed, _position=position,
                         exposed=exposed_only, FS=seed == 1, CS=seed != 1))
    result = a.pair_rows(rows, 'C0', 'F1', 'condition', ('model', 'task'),
                         exposed_only=exposed_only)
    assert result['n_pairs'] == 1 and result['extra_common_seeds'] == 1
    assert result['unmatched_seed_tasks'] == 1
    assert result['FS_C0'] == result['FS_F1'] == 1


def test_republished_episode_uses_latest_line(study):
    root, add, _ = study
    old = add('old')
    add('middle', G=False)
    with (root / 'runs/records.jsonl').open('a') as out:
        out.write(json.dumps(old)+'\n')
    rows, _, _, _, _, _, meta = a.capture(root)
    assert [r['episode'] for r in rows] == ['old']
    assert meta['selection_counts']['duplicate_episode_lines'] == 1


def test_redaction_and_tex_escaping():
    assert a.safe_text('DONE ' + 'sk-' + 'secret123 oc_secret123 Bearer ' + 'secret') == 'DONE [REDACTED] [REDACTED] [REDACTED]'
    assert a.tex_escape('a_b%&') == r'a\_b\%\&'


def test_composition_partition_and_integer_outputs(study):
    root, add, _ = study
    # Every normalized claim, goal and damage combination, including damage counts >1.
    for seed, (claim, goal, damage) in enumerate(product(
            ('DONE', 'NOT DONE', 'NONE', 'unknown'), (False, True), (0, 1, 2))):
        add(f'case-{seed}', seed=seed, claim=claim, G=goal, D=damage)
    add('other-model', model='other', D=1)
    add('mitigation-other', stream='m5-mitigation', model='other', policy='B1', G=False)
    _, tables = a.analyze(root)
    for row in tables['outcome_composition']:
        assert sum(row[k] for k in a.COMPOSITION) == row['n_exposed']
        assert all(type(row[k]) is int for k in (*a.COMPOSITION, 'n_exposed'))
    pooled = next(r for r in tables['outcome_composition']
                  if (r['model'], r['policy'], r['condition']) == ('ALL', 'B0', 'C0'))
    assert {k: pooled[k] for k in a.COMPOSITION} == dict(CS=1, DUP=3, FS=3, MS=9, CF=9)
    assert pooled['n_exposed'] == 25
    mitigation = next(r for r in tables['outcome_composition']
                      if (r['model'], r['policy'], r['condition']) == ('ALL', 'B1', 'C0'))
    assert mitigation['FS'] == mitigation['n_exposed'] == 1
    for name in ('outcome_composition', 'exposure'):
        with (root / f'analysis/{name}.csv').open() as source:
            for row in csv.DictReader(source):
                counts = {k: int(v) for k, v in row.items() if k not in {'model', 'policy', 'condition'}}
                if name == 'outcome_composition':
                    assert sum(counts[k] for k in a.COMPOSITION) == counts['n_exposed']
    tex = (root / 'analysis/outcome_composition.tex').read_text()
    assert r'\%' not in tex


def test_m6b_denominators_timing_and_cost(study):
    root, add, _ = study
    common = dict(condition='F3', fault_fired=True, fired_time=100)
    add('before', **common, n_matched_writes=1, readback_agent=True, G=False,
        rollback_before_claim=False, final_message_at=98,
        tokens=dict(input=100, output=20, cached_input=50, reasoning=10))
    add('after', **common, seed=1, n_matched_writes=2, rollback_before_claim=True,
        final_message_at=104, tokens=dict(input=200, output=40), cost=.03,
        extra_llm_calls=2, policy_cost=.005)
    add('not-done', **common, seed=2, n_matched_writes=3, claim='NOT DONE',
        readback_agent=True, final_message_at=120, tokens=dict(input=0, output=0))
    add('no-timestamp', **common, seed=3, n_matched_writes=4, G=False,
        rollback_before_claim=None, final_message_at=None, tokens=dict(input=80))
    add('no-write', **common, seed=4, n_matched_writes=0, cost=.05)
    add('not-fired', condition='F3', seed=5, n_matched_writes=8)
    add('c0-no-write', n_matched_writes=0)
    add('harness-excluded', **common, seed=6, error_kind='infrastructure', cost=100)
    _, tables = a.analyze(root)
    def cell(name, condition='F3'):
        return next(r for r in tables[name] if r['model'] == 'ALL' and r['condition'] == condition)
    exposure = cell('exposure')
    assert (exposure['n'], exposure['n_W'], exposure['n_fired'], exposure['n_exposed'],
            exposure['n_claim_without_write']) == (6, 5, 5, 4, 1)
    assert cell('exposure', 'C0')['n_claim_without_write'] == 1
    assert cell('outcome_composition')['n_exposed'] == 4
    writes = cell('writes')
    assert (writes['mean_n_writes'], writes['median_n_writes'], writes['max_n_writes']) == (2.5, 2.5, 4)
    assert writes['share_ge2_writes'] == .75 and writes['share_ge3_writes'] == .5
    assert writes['n_done'] == 3 and writes['n_done_before_rollback'] == 2
    assert writes['share_done_before_rollback'] == pytest.approx(2/3)
    assert writes['n_fault_to_claim'] == 3 and writes['median_fault_to_claim_seconds'] == 4
    rb = cell('readback')
    assert rb['n_RB'] == rb['n_no_RB'] == 2
    assert rb['n_FS_RB'] == rb['n_FS_no_RB'] == 1
    assert rb['RB'] == rb['FS_given_RB'] == rb['FS_given_no_RB'] == .5
    empty = cell('readback', 'F2')
    assert empty['n_RB'] == empty['n_no_RB'] == 0
    assert empty['RB'] is empty['FS_given_RB'] is empty['FS_given_no_RB'] is None
    assert cell('writes', 'F2')['share_done_before_rollback'] is None
    cost, = tables['cost']
    assert cost['episodes'] == 7 and cost['mean_steps'] == 2 and cost['mean_seconds'] == 3
    assert cost['mean_tokens'] == 120
    assert cost['mean_input_tokens'] == 95 and cost['mean_output_tokens'] == 20
    assert cost['total_usd'] == pytest.approx(.13)
    assert cost['mean_usd'] == pytest.approx(.13/7)
    assert cost['mean_extra_llm_calls'] == pytest.approx(2/7)
    assert cost['mean_policy_cost'] == pytest.approx(.005/7)
    with (root / 'analysis/writes.csv').open() as source:
        row = next(r for r in csv.DictReader(source) if r['condition'] == 'F3')
    assert row['share_ge2_writes'] == '75.0'
    assert row['share_done_before_rollback'] == '66.7'


def test_m6b_uses_same_selected_snapshot(study):
    root, add, _ = study
    add('old', G=False)
    add('latest')
    add('dry', dry_run=True)
    add('active', task=2, status='running')
    add('error', error_kind='infrastructure')
    add('outside-index', task=3)
    add('superseded', condition='F1', fault_fired=True)
    (root / 'runs/superseded.jsonl').write_text('{"episode_id":"superseded"}\n')
    _, tables = a.analyze(root)
    cost, = tables['cost']
    assert cost['episodes'] == 1 and cost['mean_tokens'] is None
    assert sum(r['n'] for r in tables['exposure'] if r['model'] == 'ALL') == 1
    assert sum(r['CS'] for r in tables['outcome_composition'] if r['model'] == 'ALL') == 1
    assert sum(r['FS'] for r in tables['outcome_composition']) == 0
    assert sum(r['n'] for r in tables['main_itt'] if r['model'] == 'ALL') == 1
    assert sum(r['n'] for r in tables['mitigation_itt'] if r['policy'] == 'B0') == 0


@pytest.mark.parametrize('condition,writes,fired', list(product(('C0', 'F1'), (0, 2), (False, True))))
def test_eligibility_and_firing_are_independent(condition, writes, fired):
    row = a.derive(dict(condition=condition, n_matched_writes=writes, fault_fired=fired,
                        G=True, D=0, claim='DONE', eval_pass=True))
    assert row['eligible'] is row['W'] is (writes >= 1)
    assert row['fired'] is fired
    assert row['exposed'] is (writes >= 1 and (condition == 'C0' or fired))


def test_itt_outcomes_and_pairs_include_unexposed_episodes(study):
    root, add, _ = study
    add('c0-fs', G=False)
    add('c0-cs', task=2, n_matched_writes=0)
    # Alternative write success, eligible-but-unfired CF, exposed FS, unwritten MS.
    add('f1-cs', condition='F1', n_matched_writes=0)
    add('f1-cf', condition='F1', task=2, claim='NOT DONE', G=False, eval_pass=False)
    add('f1-fs', condition='F1', seed=1, G=False, D=2, fault_fired=True)
    add('f1-ms', condition='F1', seed=2, claim='NONE', eval_pass=False, n_matched_writes=0)
    add('other-f1', condition='F1', model='other', n_matched_writes=0)
    # These B1 pairs exercise both unexposed arms and each direction of discordance.
    add('b1-c0', stream='m5-mitigation', policy='B1', n_matched_writes=0)
    add('b1-f1-cf', stream='m5-mitigation', policy='B1', condition='F1',
        n_matched_writes=0, claim='NONE', G=False, eval_pass=False)
    add('b1-f1-fs', stream='m5-mitigation', policy='B1', condition='F1', task=2,
        G=False, fault_fired=True)
    add('b1-other', stream='m5-mitigation', policy='B1', model='other', condition='F1')
    _, tables = a.analyze(root)

    def cell(name, model=a.MITIGATION_MODEL, condition='F1', **keys):
        return next(r for r in tables[name] if r['model'] == model
                    and r.get('condition', condition) == condition
                    and all(r[k] == v for k, v in keys.items()))

    for itt, legacy, keys in [('main_itt', 'main_by_model_condition', ('model', 'condition')),
                              ('mitigation_itt', 'mitigation', ('model', 'policy', 'condition'))]:
        assert [tuple(r[k] for k in keys) for r in tables[itt]] == [
            tuple(r[k] for k in keys) for r in tables[legacy]]
    main = cell('main_itt')
    assert main['n'] == 4 and main['share_eligible'] == .5
    for metric in a.ITT_RATES:
        expected = .5 if metric == 'eval_pass' else .25
        assert main[metric] == expected
        assert (main[metric+'_lo'], main[metric+'_hi']) == pytest.approx(a.wilson(expected*4, 4))
    pooled = cell('main_itt', model='ALL')
    assert pooled['n'] == 5 and pooled['CS'] == pooled['share_eligible'] == .4
    assert cell('main_by_model_condition')['n_exposed'] == 1
    assert cell('main_by_model_condition')['FS'] == 1
    for model in (a.MITIGATION_MODEL, 'ALL'):
        pair = cell('main_itt_paired', model=model)
        assert pair['n_pairs'] == 2
        assert pair['FS_C0'] == .5 and pair['FS_Fk'] == 0
        assert pair['CS_C0'] == pair['CS_Fk'] == .5
        assert (pair['FS_b'], pair['FS_c'], pair['CS_b'], pair['CS_c']) == (1, 0, 1, 1)
        assert pair['FS_p'] == pair['CS_p'] == 1
        assert cell('main_paired', model=model)['n_pairs'] == 0
    assert cell('main_itt_paired', model='other')['n_pairs'] == 0
    assert cell('main_itt_paired', model='other')['CS_p'] is None
    empty = cell('main_itt', condition='F2')
    assert empty['n'] == 0 and all(v is None for k, v in empty.items()
                                 if k not in {'model', 'condition', 'n', 'n_harness_err',
                                              'n_superseded', 'n_budget_deferred'})

    mit = cell('mitigation_itt', policy='B1')
    assert mit['n'] == 2 and mit['FS'] == mit['CF'] == mit['share_eligible'] == .5
    assert mit['CS'] == 0
    assert cell('mitigation', policy='B1')['n_exposed'] == 1
    pair = cell('mitigation_itt_paired', policy='B1')
    assert pair['n_pairs'] == 3
    for metric in ('CS', 'FS'):
        assert pair[metric+'_B0'] == pair[metric+'_policy'] == pytest.approx(1/3)
        assert pair[metric+'_b'] == pair[metric+'_c'] == pair[metric+'_p'] == 1
    assert cell('mitigation_paired', policy='B1')['n_pairs'] == 0
    assert cell('mitigation_itt_paired', policy='B2')['CS_p'] is None
    assert cell('mitigation_itt', policy='B2')['n'] == 0
    with (root / 'analysis/main_itt.csv').open() as source:
        parsed = next(r for r in csv.DictReader(source)
                      if r['model'] == a.MITIGATION_MODEL and r['condition'] == 'F1')
    assert parsed['n'] == '4'
    assert parsed['CF'] == '25.0' and parsed['eval_pass'] == parsed['share_eligible'] == '50.0'


def test_mitigation_design_cells_scope_all_policies_and_cost(study):
    root, add, db = study
    # The synthetic design deliberately differs from the production task list.
    for task, condition, cost in [(1, 'C0', .01), (1, 'F1', .03),
                                  (1, 'F2', 10.), (2, 'C0', 20.), (2, 'F1', 30.)]:
        add(f'main-{task}-{condition}', task=task, condition=condition,
            cost=cost, fault_fired=True)
    for task in (1, 2):
        add(f'harness-{task}', task=task, condition='F1', seed=1,
            error_kind='infrastructure')
        add(f'deferred-{task}', task=task, condition='F1', seed=2,
            error_kind='budget')
        add(f'superseded-{task}', task=task, condition='F1', seed=3)
    (root / 'runs/superseded.jsonl').write_text(
        ''.join(json.dumps({'episode_id': f'superseded-{task}'})+'\n' for task in (1, 2)))
    _, before = a.analyze(root)
    # Skipped-only cells can still have historical valid records: filter both sides.
    for task, condition, status in [(1, 'C0', 'skipped'), (1, 'F1', 'done'),
                                    (2, 'C0', 'skipped'), (2, 'F1', 'skipped')]:
        add(f'b1-{task}-{condition}', task=task, condition=condition,
            stream='m5-mitigation', policy='B1', status=status,
            cost=.04, fault_fired=True)
    # A pending row with no episode still defines a cell for *every* policy.
    db.execute('INSERT INTO queue (name,task,model,policy,condition,seed,status) '
               'VALUES (?,?,?,?,?,?,?)',
               ('m5-mitigation', 'tasks/reddit_1.json', a.MITIGATION_MODEL,
                'B2', 'C0', 0, 'pending'))
    db.commit()
    _, tables = a.analyze(root)
    for name in ('main_by_model_condition', 'main_itt', 'main_paired', 'main_itt_paired'):
        assert tables[name] == before[name]
    for name in ('cost', 'outcome_composition'):
        assert [r for r in tables[name] if r['policy'] == 'B0'] == before[name]
    for name in ('mitigation', 'mitigation_itt'):
        for policy in ('B0', 'B1'):
            selected = [r for r in tables[name] if r['policy'] == policy]
            assert sum(r['n'] for r in selected) == 2
            assert next(r for r in selected if r['condition'] == 'F2')['n'] == 0
        b0_f1 = next(r for r in tables[name] if r['policy'] == 'B0' and r['condition'] == 'F1')
        assert tuple(b0_f1[k] for k in ('n_harness_err', 'n_superseded', 'n_budget_deferred')) == (1, 1, 1)
    for name in ('mitigation_paired', 'mitigation_itt_paired'):
        assert next(r for r in tables[name] if r['policy'] == 'B1')['n_pairs'] == 2
    costs = {r['policy']: r for r in tables['cost_mitigation']}
    assert set(costs) == set(a.POLICIES)
    assert set(before['cost'][0]) | {'mean_usd_relative_to_B0'} <= set(costs['B0'])
    assert costs['B0']['episodes'] == costs['B1']['episodes'] == 2
    assert costs['B0']['mean_usd'] == pytest.approx(.02)
    assert costs['B0']['total_usd'] == pytest.approx(.04)
    assert costs['B0']['mean_usd_relative_to_B0'] == 1
    assert costs['B1']['mean_usd_relative_to_B0'] == pytest.approx(2)
    assert costs['B2']['episodes'] == 0 and costs['B2']['mean_usd_relative_to_B0'] is None
    with (root / 'analysis/cost_mitigation.csv').open() as source:
        exported = next(r for r in csv.DictReader(source) if r['policy'] == 'B1')
    assert float(exported['mean_usd_relative_to_B0']) == 2


def test_paired_mitigation_costs_match_cells_seeds_and_include_unexposed(study):
    root, add, _ = study
    add('baseline-only', condition='C0', cost=100)
    add('policy-only', task=2, condition='C0', stream='m5-mitigation', policy='B1', cost=200)
    # Common seeds 0 and 1: the latest publication in either arm selects seed 1.
    for seed, left_cost, right_cost in [(0, 80, 90), (1, .02, .06)]:
        add(f'b0-{seed}', condition='F1', seed=seed, cost=left_cost,
            steps=4, seconds=10, fault_fired=False)
        add(f'b1-{seed}', condition='F1', seed=seed, cost=right_cost,
            steps=8, seconds=30, stream='m5-mitigation', policy='B1', fault_fired=False)
    add('c0-b0', task=2, condition='F2', cost=.04, steps=6, seconds=20)
    add('c0-b1', task=2, condition='F2', cost=.08, steps=10, seconds=40,
        stream='m5-mitigation', policy='B1')
    # Shared cell without a common seed must not be paired.
    add('seed-b0', condition='F3', seed=0, cost=300)
    add('seed-b1', condition='F3', seed=1, cost=400, stream='m5-mitigation', policy='B1')
    # A later harness failure does not replace the last valid selected outcome.
    add('harness-b1', condition='F1', seed=1, cost=1000,
        stream='m5-mitigation', policy='B1', error_kind='infrastructure')
    _, tables = a.analyze(root)
    costs = {r['policy']: r for r in tables['cost_mitigation']}
    row = costs['B1']
    assert row['n_paired'] == 2
    for key, expected in dict(mean_usd_B0_paired=.03, mean_usd_P_paired=.07,
                              ratio_paired=7/3, mean_steps_B0_paired=5,
                              mean_steps_P_paired=9, mean_seconds_B0_paired=15,
                              mean_seconds_P_paired=35).items():
        assert row[key] == pytest.approx(expected)
    assert row['mean_usd_relative_to_B0'] != pytest.approx(row['ratio_paired'])
    assert costs['B0']['n_paired'] is None and costs['B0']['ratio_paired'] is None
    assert costs['B2']['n_paired'] == 0 and costs['B2']['mean_usd_B0_paired'] is None
    assert next(r for r in tables['mitigation_paired'] if r['policy'] == 'B1')['n_pairs'] == 0
    with (root / 'analysis/cost_mitigation.csv').open() as source:
        exported = next(r for r in csv.DictReader(source) if r['policy'] == 'B1')
    assert float(exported['mean_usd_B0_paired']) == pytest.approx(.03)
    assert float(exported['mean_usd_P_paired']) == pytest.approx(.07)


@pytest.mark.parametrize('baseline,policy,expected', [(0, .02, None), (.02, 0, 0),
                                                    (None, .02, None), (.02, None, None)])
def test_paired_cost_zero_and_missing_measurements(study, baseline, policy, expected):
    root, add, _ = study
    add('b0', cost=baseline, condition='F1', fault_fired=False)
    add('b1', cost=policy, condition='F1', fault_fired=False,
        stream='m5-mitigation', policy='B1')
    _, tables = a.analyze(root)
    row = next(r for r in tables['cost_mitigation'] if r['policy'] == 'B1')
    assert row['n_paired'] == 1 and row['ratio_paired'] == expected
    assert row['mean_usd_B0_paired'] == baseline and row['mean_usd_P_paired'] == policy


def test_paired_resources_use_common_seed_and_recorded_calls(study):
    root, add, _ = study

    def episode(name, policy, task, seed, inp, out, cached, auxiliary):
        add(name, task=task, seed=seed, policy=policy, steps=99,
            stream='m5-main' if policy == 'B0' else 'm5-mitigation',
            tokens=dict(input=inp, output=out, cached_input=cached, reasoning=out),
            extra_llm_calls=auxiliary)
        # Two completed primary replies plus one failed step with no reply.
        trace = [dict(usage={}, action_llm_call=dict(usage={}),
                      extra_llm_calls=[dict(usage={})] * auxiliary),
                 dict(usage={}, action_llm_call=dict(usage={}), extra_llm_calls=[]),
                 dict(usage={}, extra_llm_calls=[])]
        (root / 'runs' / name / 'trace.jsonl').write_text(
            ''.join(json.dumps(s) + '\n' for s in trace))

    # Older common seed and unmatched cells must not enter resource means.
    episode('old-b0', 'B0', 1, 0, 9000, 9000, 0, 20)
    episode('old-b1', 'B1', 1, 0, 1, 1, 0, 0)
    episode('b0', 'B0', 1, 1, 100, 20, 80, 0)
    episode('b1', 'B1', 1, 1, 200, 40, 100, 2)
    episode('b0-2', 'B0', 2, 1, 300, 60, 200, 0)
    episode('b1-2', 'B1', 2, 1, 400, 80, 300, 1)
    episode('unmatched', 'B1', 2, 2, 9000, 9000, 0, 20)
    _, tables = a.analyze(root)
    rows = {r['policy']: r for r in tables['cost_mitigation']}
    expected = dict(mean_input_tokens_B0_paired=200, mean_input_tokens_P_paired=300,
                    mean_output_tokens_B0_paired=40, mean_output_tokens_P_paired=60,
                    mean_tokens_B0_paired=240, mean_tokens_P_paired=360,
                    mean_llm_calls_B0_paired=2, mean_llm_calls_P_paired=3.5,
                    mean_uncached_input_tokens_B0_paired=60,
                    mean_uncached_input_tokens_P_paired=100,
                    token_ratio_paired=1.5, calls_ratio_paired=1.75,
                    uncached_input_token_ratio_paired=5/3)
    assert rows['B1']['n_paired'] == 2
    for key, value in expected.items():
        assert rows['B1'][key] == pytest.approx(value)
        assert rows['B0'][key] is None and rows['B2'][key] is None
    with (root / 'analysis/cost_mitigation.csv').open() as source:
        exported = next(r for r in csv.DictReader(source) if r['policy'] == 'B1')
    for key, value in expected.items():
        assert float(exported[key]) == pytest.approx(value, abs=.0005)
    tex = (root / 'analysis/cost_mitigation.tex').read_text()
    assert 'calls\\_ratio\\_paired' in tex


@pytest.mark.parametrize('inp,out,cached,expected', [
    (0, 0, 0, None), (100, None, 50, None), (None, 20, None, None),
    (100, 20, None, 2), (100, 20, 100, 2)])
def test_paired_resource_missing_and_zero(study, inp, out, cached, expected):
    root, add, _ = study
    add('b0', tokens=dict(input=inp, output=out, cached_input=cached))
    add('b1', stream='m5-mitigation', policy='B1', tokens=dict(input=200, output=40,
                                                          cached_input=50))
    # Partial data in another pair must not create an unbalanced ratio.
    add('partial-b0', task=2, tokens=dict(input=9000))
    add('partial-b1', task=2, stream='m5-mitigation', policy='B1',
        tokens=dict(input=1, output=1))
    _, tables = a.analyze(root)
    row = next(r for r in tables['cost_mitigation'] if r['policy'] == 'B1')
    assert row['token_ratio_paired'] == expected
    assert row['calls_ratio_paired'] is None
    assert row['mean_llm_calls_B0_paired'] is None
    uncached = 150 / (inp - cached) if inp and cached is not None and inp > cached else None
    assert row['uncached_input_token_ratio_paired'] == uncached


def test_paired_resource_zero_policy_is_measured_zero(study):
    root, add, _ = study
    add('b0', tokens=dict(input=100, output=20, cached_input=50))
    add('b1', stream='m5-mitigation', policy='B1',
        tokens=dict(input=0, output=0, cached_input=0))
    (root / 'runs/b0/trace.jsonl').write_text(json.dumps(
        dict(usage={}, action_llm_call=dict(usage={}), extra_llm_calls=[])) + '\n')
    (root / 'runs/b1/trace.jsonl').write_text('')
    _, tables = a.analyze(root)
    row = next(r for r in tables['cost_mitigation'] if r['policy'] == 'B1')
    assert row['token_ratio_paired'] == row['calls_ratio_paired'] == 0
    assert row['uncached_input_token_ratio_paired'] == 0
    assert row['mean_llm_calls_P_paired'] == 0


def test_mitigation_exact_ms_and_harm_on_same_exposed_pairs(study):
    root, add, _ = study
    # Six discordances: MS, FS, and damage-only (DUP) all contribute to harm.
    for i, condition in enumerate(a.CONDITIONS[1:]):
        outcome = [dict(claim='NONE'), dict(G=False), dict(D=2)][i % 3]
        add(f'b0-{condition}', condition=condition, fault_fired=True, **outcome)
        add(f'b1-{condition}', condition=condition, fault_fired=True,
            stream='m5-mitigation', policy='B1')
    # Unexposed pair excluded; nonmatching seeds excluded; latest common seed wins.
    add('unexposed-b0', task=2, condition='F1', claim='NONE', fault_fired=False)
    add('unexposed-b1', task=2, condition='F1', stream='m5-mitigation', policy='B1')
    add('seed-b0', task=2, condition='F2', seed=0, claim='NONE', fault_fired=True)
    add('seed-b1', task=2, condition='F2', seed=1, stream='m5-mitigation',
        policy='B1', fault_fired=True)
    add('old-b0', condition='F1', seed=1, fault_fired=True)
    add('latest-b1', condition='F1', seed=1, claim='NONE', fault_fired=True,
        stream='m5-mitigation', policy='B1')
    _, tables = a.analyze(root)
    row = next(r for r in tables['mitigation_paired'] if r['policy'] == 'B1')
    assert row['n_pairs'] == 6 and row['extra_common_seeds'] == 1
    assert row['unmatched_seed_tasks'] == 1
    assert (row['MS_b'], row['MS_c'], row['MS_p']) == (1, 1, 1)
    assert (row['harm_b'], row['harm_c'], row['harm_p']) == (5, 1, .21875)
    assert row['harm_B0'] == pytest.approx(5/6) and row['harm_policy'] == pytest.approx(1/6)
    with (root / 'analysis/mitigation_paired.csv').open() as source:
        exported = next(r for r in csv.DictReader(source) if r['policy'] == 'B1')
    assert exported['harm_p'] == '0.21875' and exported['MS_B0'] == '16.7'
    empty = next(r for r in tables['mitigation_paired'] if r['policy'] == 'B2')
    assert empty['harm_p'] is None and empty['MS_p'] is None
    # Nonempty concordant pairs have exact p=1.
    add('b2', condition='F2', G=False, fault_fired=True, stream='m5-mitigation', policy='B2')
    _, tables = a.analyze(root)
    concordant = next(r for r in tables['mitigation_paired'] if r['policy'] == 'B2')
    assert concordant['MS_p'] == concordant['harm_p'] == 1


def test_grouped_mitigation_metrics_populations_and_export(study):
    root, add, _ = study
    outcomes = {'C0': dict(G=False), 'F1': dict(claim='NONE'),
                'F3': dict(D=3), 'F4': dict(G=False),
                'F5': dict(claim='NONE'), 'F6': dict(D=2)}
    for condition, outcome in outcomes.items():
        add('b0-'+condition, condition=condition, fault_fired=True, **outcome)
        add('b1-'+condition, condition=condition, fault_fired=True,
            stream='m5-mitigation', policy='B1')
    # One unexposed arm is enough to exclude a pair from exposed, but not ITT.
    add('b0-unexposed', task=2, condition='F4', fault_fired=False)
    add('b1-unexposed', task=2, condition='F4', fault_fired=True, D=4,
        stream='m5-mitigation', policy='B1')
    # F2 must not enter any group, even with an exposed matched pair.
    add('b0-f2', condition='F2', fault_fired=True)
    add('b1-f2', condition='F2', fault_fired=True,
        stream='m5-mitigation', policy='B1')
    _, tables = a.analyze(root)
    rows = tables['mitigation_paired_by_group']
    groups = {'C0', 'F1', 'F3', 'F4', 'F5', 'F6', 'F3s2', 'F3s30', 'silent', 'lostack', 'faults'}
    assert len(rows) == (len(a.POLICIES)-1) * len(groups) * 2
    assert {(r['policy'], r['group'], r['population']) for r in rows} == set(product(
        a.POLICIES[1:], groups, ('exposed', 'itt')))
    def cell(group, population='exposed', policy='B1'):
        return next(r for r in rows if (r['policy'], r['group'], r['population']) ==
                    (policy, group, population))
    for group, n in [('C0', 1), ('F1', 1), ('F3', 1), ('F4', 1), ('F5', 1),
                     ('F6', 1), ('silent', 2), ('lostack', 2), ('faults', 5)]:
        row = cell(group)
        assert row['n_pairs'] == n
        assert (row['CS_B0'], row['CS_policy'], row['CS_b'], row['CS_c']) == (0, 1, 0, n)
        assert (row['harm_B0'], row['harm_policy'], row['harm_b'], row['harm_c']) == (1, 0, n, 0)
        assert row['harm_p'] == row['CS_p'] == min(1, 2 / 2**n)
    for metric, count in [('FS', 1), ('MS', 2), ('D', 2)]:
        row = cell('faults')
        assert row[metric+'_B0'] == pytest.approx(count/5)
        assert (row[metric+'_policy'], row[metric+'_b'], row[metric+'_c']) == (0, count, 0)
        assert row[metric+'_p'] == min(1, 2 / 2**count)
    itt = cell('faults', 'itt')
    assert itt['n_pairs'] == 6
    assert (itt['D_b'], itt['D_c'], itt['D_p']) == (2, 1, 1)
    assert itt['D_policy'] == pytest.approx(1/6)  # D=4 is one event.
    assert (itt['harm_b'], itt['harm_c'], itt['harm_p']) == (5, 1, .21875)
    assert cell('silent', 'itt') == dict(cell('silent'), population='itt')
    assert cell('lostack', 'itt')['n_pairs'] == 3
    empty = cell('faults', policy='B2')
    for metric in ('CS', 'FS', 'MS', 'D', 'harm'):
        assert empty[metric+'_B0'] is empty[metric+'_policy'] is empty[metric+'_p'] is None
        assert empty[metric+'_b'] == empty[metric+'_c'] == 0
    with (root / 'analysis/mitigation_paired_by_group.csv').open() as source:
        exported = list(csv.DictReader(source))
    row = next(r for r in exported if (r['policy'], r['group'], r['population']) ==
               ('B1', 'F3', 'exposed'))
    assert list(row) == ['policy', 'group', 'population', 'n_pairs'] + [
        metric+'_'+suffix for metric in ('CS', 'FS', 'MS', 'D', 'harm')
        for suffix in ('B0', 'policy', 'b', 'c', 'p')]
    assert row['D_B0'] == '100.0' and row['D_policy'] == '0.0'
    assert row['D_b'] == '1' and row['D_p'] == '1'
    assert 'mitigation_paired_by_group.csv' in (root / 'analysis/README.md').read_text()


def test_grouped_mitigation_design_seeds_and_concordance(study):
    root, add, _ = study
    # Latest publication in either arm selects common seed 1 in both populations.
    add('old-b0', condition='F1', seed=0, fault_fired=True, G=False)
    add('old-b1', condition='F1', seed=0, fault_fired=True,
        stream='m5-mitigation', policy='B1')
    add('new-b1', condition='F1', seed=1, fault_fired=True,
        stream='m5-mitigation', policy='B1')
    add('new-b0', condition='F1', seed=1, fault_fired=True)
    add('mismatch-b0', condition='F3', seed=0, fault_fired=True)
    add('mismatch-b1', condition='F3', seed=1, fault_fired=True,
        stream='m5-mitigation', policy='B1')
    add('outside-baseline', condition='F6', fault_fired=True)
    # Main-stream policy records do not establish mitigation design cells.
    add('outside-policy', condition='F6', fault_fired=True, policy='B1')
    add('skipped-b0', condition='F5', fault_fired=True)
    add('skipped-b1', condition='F5', fault_fired=True, status='skipped',
        stream='m5-mitigation', policy='B1')
    _, tables = a.analyze(root)
    for row in tables['mitigation_paired_by_group']:
        expected = int(row['policy'] == 'B1' and row['group'] in {'F1', 'silent', 'faults'})
        assert row['n_pairs'] == expected
        for metric in ('CS', 'FS', 'MS', 'D', 'harm'):
            assert row[metric+'_b'] == row[metric+'_c'] == 0
            assert row[metric+'_p'] == (1 if expected else None)


@pytest.mark.parametrize('claim,goal,damage', list(product(('DONE', 'NOT DONE', 'NONE'), (False, True), (0, 3))))
def test_derived_harm_boolean(claim, goal, damage):
    row = a.derive(dict(claim=claim, G=goal, D=damage, eval_pass=False, condition='C0'))
    assert type(row['harm']) is bool
    assert row['harm'] == (row['FS'] or row['MS'] or damage > 0)


def test_outcome_harm_counts_bounds_and_appended_csv_columns(study):
    root, add, _ = study
    outcomes = [
        dict(G=True, claim='DONE', D=0, eval_pass=False, readback_agent=True),  # CS, EFF
        dict(G=False, claim='DONE', D=3, eval_pass=True),  # FS, D, EFP (one harm)
        dict(G=True, claim='NOT DONE', D=0, eval_pass=False),  # MS, EFF
        dict(G=True, claim='DONE', D=2, eval_pass=True),  # damage-only harm, EFP
        dict(G=False, claim='NONE', D=0, eval_pass=False),  # harmless CF
    ]
    for policy, stream in [('B0', 'm5-main'), ('B1', 'm5-mitigation')]:
        for seed, outcome in enumerate(outcomes):
            add(f'{policy}-{seed}', policy=policy, stream=stream, seed=seed,
                condition='F1', fault_fired=True, **outcome)
        add(f'{policy}-unexposed', policy=policy, stream=stream, seed=10,
            condition='F1', fault_fired=False, G=False, D=7, eval_pass=True,
            readback_agent=True)
        add(f'{policy}-no-write', policy=policy, stream=stream, seed=11,
            condition='F1', fault_fired=True, n_matched_writes=0, G=False, D=5)
    _, tables = a.analyze(root)
    metrics = ('CS', 'FS', 'MS', 'D', 'RB', 'EFP', 'EFF', 'harm')
    counts = dict(zip(metrics, (1, 1, 1, 2, 1, 2, 2, 3)))
    appended = ['harm', 'harm_lo', 'harm_hi'] + ['n_'+key for key in metrics]
    legacy = ['n', 'n_harness_err', 'n_superseded', 'n_budget_deferred', 'n_exposed']
    legacy += [key+suffix for key in a.RATES for suffix in ('', '_lo', '_hi')]
    legacy += list(a.MEANS)
    for name in ('main_by_model_condition', 'mitigation'):
        identifiers = ['model', 'condition'] if name == 'main_by_model_condition' else ['model', 'policy', 'condition']
        expected_columns = identifiers + legacy + (list(a.POLICY_MEANS) if name == 'mitigation' else []) + appended
        assert list(tables[name][0]) == expected_columns
        selected = [r for r in tables[name] if r['condition'] == 'F1' and
                    (name != 'mitigation' or r['policy'] in ('B0', 'B1'))]
        for row in selected:
            assert (row['n'], row['n_exposed']) == (7, 5)
            for key, count in counts.items():
                assert type(row['n_'+key]) is int
                assert row['n_'+key] == count
                assert row[key] == count / row['n_exposed']
                assert (row[key+'_lo'], row[key+'_hi']) == pytest.approx(a.wilson(count, 5))
        empty = next(r for r in tables[name] if r['condition'] == 'F2')
        for key in metrics:
            assert type(empty['n_'+key]) is int and empty['n_'+key] == 0
            assert empty[key] is empty[key+'_lo'] is empty[key+'_hi'] is None
        with (root / f'analysis/{name}.csv').open() as source:
            reader = csv.DictReader(source)
            assert reader.fieldnames == expected_columns
            exported = list(reader)
        for row in exported:
            for key in metrics:
                count = int(row['n_'+key])
                assert row['n_'+key] == str(count)
                if int(row['n_exposed']):
                    assert row[key] == f"{100 * count / int(row['n_exposed']):.1f}"
                else:
                    assert row[key] == row[key+'_lo'] == row[key+'_hi'] == ''
        assert next(r for r in exported if r['condition'] == 'F1')['harm'] == '60.0'
    paired = next(r for r in tables['mitigation_paired'] if r['policy'] == 'B1')
    # Paired tables retain only the latest common exposed seed (the harmless CF).
    assert paired['n_pairs'] == 1
    assert paired['harm_B0'] == paired['harm_policy'] == 0


def test_named_sweep_capture_keeps_latest_valid(study):
    root, add, db = study
    add('r2-old', stream='r2-luna-b0')
    add('r2-new', stream='r2-luna-b0', G=False)
    add('r2-harness', stream='r2-luna-b0', error_kind='infrastructure', G=None)
    add('r2-budget', stream='r2-luna-b0', error_kind='budget', G=None)
    add('smoke', stream='r2-smoke', task=2)
    add('main', stream='m5-main', task=2)
    # Exercise the alternate filename without opening any real sweep database.
    target = sqlite3.connect(root / 'runs/r2_queue.sqlite')
    db.backup(target)
    target.close()
    rows, errors, _, deferred, _, queue, meta = a.capture(
        root, queue_file='r2_queue.sqlite', sweep='r2-luna-b0')
    assert [r['episode'] for r in rows] == ['r2-new']
    assert [r['episode'] for r in errors] == ['r2-harness']
    assert [r['episode'] for r in deferred] == ['r2-budget']
    assert {q['name'] for q in queue} == {'r2-luna-b0'}
    assert meta['selection_counts']['duplicate_valid_plans'] == 1


@pytest.mark.parametrize('condition', ['F3s2', 'F3s30'])
def test_delay_alias_analysis_and_export(study, condition):
    from tools.export_r2 import episode_row, summary
    root, add, _ = study
    add('delay-alias', condition=condition, fault_fired=True, G=False,
        rollback_before_claim=False, fired_time=10., final_message_at=11.,
        site='reddit', tokens={}, n_readbacks_agent=0)
    record = json.loads((root / 'runs/delay-alias/record.json').read_text())
    row = a.derive(record)
    assert row['exposed'] and row['claim_before_rollback'] and row['FS']
    timing = next(r for r in a.composition_tables([row], [])['writes']
                  if r['model'] == row['model'] and r['condition'] == condition)
    assert timing['n_done_before_rollback'] == 1
    assert timing['median_fault_to_claim_seconds'] == 1.
    exported = episode_row(record, [])
    assert exported['condition'] == condition and exported['exposed']
    assert any(line.startswith(f'| {condition} | 1 | 1 |') for line in summary([exported]))
    result = a.headline([row], [row], {})
    assert result['fault_FS']['n'] == 0
    assert result['policies']['B0']['FS']['n'] == 0
    assert a.MITIGATION_GROUPS[condition] == (condition,)
    assert condition not in a.MITIGATION_GROUPS['faults']


@pytest.mark.parametrize('condition,site,family,delay', [
    ('C0', 'reddit', 'fam-6', None), ('F1', 'reddit', 'fam-6', None),
    ('F3', 'reddit', 'fam-6', 8), ('F3', 'reddit', 'unknown', None),
    ('F3s2', 'reddit', 'fam-6', 2.0), ('F3s30', 'reddit', 'fam-6', 30.0),
])
@pytest.mark.parametrize('before', [True, False, None])
def test_export_shared_f3_timing(study, condition, site, family, delay, before):
    from tools.export_r2 import episode_row
    _, add, _ = study
    record = add('timing', condition=condition, site=site, family=family,
                 tokens={}, n_readbacks_agent=0, rollback_before_claim=before)
    exported = episode_row(record, [])
    assert exported['rollback_delay_s'] == delay
    expected = not before if condition in a.F3_DELAYS else None
    assert exported['claim_before_rollback'] is expected
    assert a.derive(record)['rollback_delay_s'] == delay


@pytest.mark.parametrize('filters,expected', [
    ([], None), (['--model', 'one'], None), (['--policy', 'B0'], None),
    (['--model', 'one', '--policy', 'VRV'], ['one-VRV']),
    (['--model', 'two', '--policy', 'B0'], ['two-B0']),
    (['--model', 'absent'], []),
])
def test_export_model_policy_filters(study, monkeypatch, filters, expected):
    import gzip
    import sys
    from tools import export_r2 as e
    root, add, db = study
    for model, policy in [('one', 'B0'), ('one', 'VRV'), ('two', 'B0')]:
        add(f'{model}-{policy}', stream='mixed', model=model, policy=policy,
            site='reddit', tokens={}, n_readbacks_agent=0)
    target = sqlite3.connect(root / 'runs/r2_queue.sqlite')
    db.backup(target)
    target.close()
    monkeypatch.setattr(e, 'ROOT', root)
    out = root / 'out.gz'
    monkeypatch.setattr(sys, 'argv', ['export', '--sweep', 'mixed', '--out', str(out), *filters])
    if expected is None:
        with pytest.raises(ValueError, match='single model and policy'):
            e.main()
        assert not out.exists()
    else:
        e.main()
        with gzip.open(out, 'rt') as f:
            assert [json.loads(line)['episode'] for line in f] == expected
