"""Read-only M6 snapshot analysis, including grouped exposed/ITT mitigation pairs.

Run with python -m tools.analyze; see analysis/README.md for output definitions.
"""
import argparse
from functools import lru_cache
import yaml
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import statistics
import time

ROOT = Path(__file__).resolve().parents[1]
from proxy.plan import CONDITIONS, F3_DELAYS
HISTORICAL_CONDITIONS = ('C0', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6')
POLICIES = ('B0', 'B1', 'B2', 'VRV', 'VRV-modeltrigger', 'VRV-samepage', 'VRV-nocbr', 'Oracle')
MITIGATION_MODEL = 'deepseek-v4.1-flash'
MITIGATION_GROUPS = {**{c: (c,) for c in ('C0', 'F1', 'F3', 'F4', 'F5', 'F6', 'F3s2', 'F3s30')},
                     'silent': ('F1', 'F3'), 'lostack': ('F4', 'F5'),
                     'faults': ('F1', 'F3', 'F4', 'F5', 'F6')}
RATES = ('CS', 'FS', 'MS', 'D', 'claim_ok', 'RB', 'EFP', 'EFF', 'retry_storm')
ITT_RATES = ('CS', 'FS', 'MS', 'CF', 'D', 'eval_pass', 'EFP', 'EFF')
MEANS = {'mean_n_writes': 'n_writes', 'mean_steps': 'steps', 'mean_usd': 'cost',
         'mean_seconds': 'seconds'}
POLICY_MEANS = ('n_verifications', 'n_done_refused', 'cbr_prompted', 'extra_llm_calls', 'policy_cost')
COMPOSITION = ('CS', 'DUP', 'FS', 'MS', 'CF')
EXTRA_RATES = {'share_ge2_writes', 'share_ge3_writes', 'share_done_before_rollback',
               'FS_given_RB', 'FS_given_no_RB', 'CF', 'eval_pass', 'share_eligible', 'harm'}
ACTION_ERRORS = {'action', 'action_error', 'agent_action', 'agent_action_error'}
BUDGET_DEFERRALS = {'budget', 'budget_deferred', 'budget_deferral', 'budget_wait'}


class SnapshotChanged(RuntimeError):
    """An input changed during capture; retry without modifying any input."""


def signature(path):
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def bounded_read(path):
    """Read only bytes present at open; a concurrent append belongs to next run."""
    with path.open('rb') as source:
        size = path.stat().st_size
        return source.read(size)


def json_lines(data):
    """Only a torn trailing append may be ignored; malformed complete lines fail."""
    lines = data.splitlines(keepends=True)
    partial = bool(lines and not lines[-1].endswith(b'\n'))
    if partial:
        lines.pop()
    return [json.loads(line) for line in lines if line.strip()], int(partial)


def manifest(directory):
    return {str(p.relative_to(directory)): signature(p)
            for p in directory.rglob('*') if p.is_file()}


def indexed_tasks(root):
    text = (root / 'tasks/INDEX.md').read_text()
    tasks, paths = {}, set()
    for name in re.findall(r'\]\(([^)]+\.json)\)', text):
        path = (root / 'tasks' / name).resolve()
        if not path.is_relative_to(root / 'tasks'):
            raise ValueError('INDEX task outside tasks directory')
        task = json.loads(path.read_text())
        tasks[task['id']] = task
        paths.add(str(path.relative_to(root)))
    return tasks, paths


def plan_key(row, task=None):
    return (row['model'], row['policy'], row['condition'], task or row['task'], row['seed'])


def queue_snapshot(path, sweep=None):
    # Do not instantiate runner.sweep.Queue: its constructor writes schema/WAL.
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        where = "(q.name='m5-main' AND q.policy='B0') OR q.name='m5-mitigation'"
        params = ()
        if sweep is not None:
            where, params = 'q.name=?', (sweep,)
        rows = [dict(r) for r in db.execute('SELECT q.* FROM queue q WHERE ' + where, params)]
        history = [dict(r) for r in db.execute('''SELECT h.* FROM history h JOIN queue q
            ON q.id=h.queue_id WHERE ''' + where, params)]
        return rows, history
    finally:
        db.close()


def harness_reason(row):
    kind = row.get('error_kind')
    if kind and kind not in ACTION_ERRORS:
        return kind
    if row.get('restore_ok') is False or (row.get('browser_killed') and row.get('restore_ok') is not True):
        return 'restore_failure'
    if row.get('error') and kind not in ACTION_ERRORS:
        return 'unclassified_error'
    if (row.get('G') not in (True, False) or row.get('eval_pass') not in (True, False)
            or not isinstance(row.get('D'), (int, float))
            or not math.isfinite(row['D']) or row['D'] < 0):
        return 'missing_or_invalid_outcome'
    return None


def budget_deferred(row):
    return (row.get('error_kind') in BUDGET_DEFERRALS
            or re.search(r'\bBudgetExceeded\b', row.get('error') or '') is not None)


@lru_cache(maxsize=None)
def catalog_rollback_delays():
    """Configured historical F3 delays; aliases override these family values."""
    return {(data['site'], entry['id']): entry.get('f3', {}).get('delay_s', 0.5)
            for path in sorted((ROOT / 'catalog').glob('*.yaml'))
            for data in [yaml.safe_load(path.read_text())]
            for entry in data['entries']}


def rollback_delay_s(row):
    condition = row['condition']
    if condition not in F3_DELAYS:
        return None
    if F3_DELAYS[condition] is not None:
        return F3_DELAYS[condition]
    return catalog_rollback_delays().get((row.get('site'), row.get('family')))


def derive(row):
    result = dict(row)
    claim = row.get('claim') if row.get('claim') in {'DONE', 'NOT DONE', 'NONE'} else 'NONE'
    goal, damage, done = bool(row['G']), bool(row['D']), claim == 'DONE'
    writes = row.get('n_matched_writes', 0)
    eligible, fired = writes >= 1, bool(row.get('fault_fired'))
    result.update(rollback_delay_s=rollback_delay_s(row), claim=claim, W=eligible, eligible=eligible, fired=fired,
                  exposed=eligible and (row['condition'] == 'C0' or fired),
                  FS=done and not goal, MS=not done and goal, CS=done and goal and not damage,
                  DUP=done and goal and damage, CF=not done and not goal,
                  claim_ok=done == goal, EFP=bool(row['eval_pass']) and not (goal and not damage),
                  EFF=not row['eval_pass'] and goal and not damage, n_writes=writes,
                  retry_storm=writes >= 3, RB=bool(row.get('readback_agent')),
                  claim_before_rollback=(not row.get('rollback_before_claim')
                      if row['condition'] in F3_DELAYS else None))
    result['harm'] = bool(result['FS'] or result['MS'] or row['D'] > 0)
    return result


def capture(root, *, queue_file='m5_queue.sqlite', sweep=None):
    """Capture queue membership then an append-only prefix; read only closed artifacts.

    Queue history establishes sweep provenance (matching a plan alone is unsafe).
    record.json is published after browser/trace/restore cleanup. Require agreement
    with the aggregate and stable directory metadata, and exclude active/new files.
    """
    root = root.resolve()
    cutoff = time.time_ns()
    tasks, indexed_paths = indexed_tasks(root)
    queue, history = queue_snapshot(root / 'runs' / queue_file, sweep=sweep)
    superseded_path = root / 'runs/superseded.jsonl'
    superseded_data = bounded_read(superseded_path) if superseded_path.exists() else b''
    superseded_rows, superseded_partial = json_lines(superseded_data)
    if superseded_partial:
        raise SnapshotChanged('superseded append is incomplete')
    superseded = {r['episode_id'] for r in superseded_rows}
    data = bounded_read(root / 'runs/records.jsonl')
    records, partial = json_lines(data)
    counts = Counter({key: 0 for key in ('outside_sweeps', 'superseded', 'dry_run',
        'outside_index', 'membership_mismatch', 'incomplete_or_active',
        'duplicate_episode_lines', 'duplicate_valid_plans')})
    counts.update(records_lines=len(records), partial_records_tail=partial)
    by_id = {r['id']: r for r in queue}
    members = {}
    for h in history:
        if h['episode_id'] and h['queue_id'] in by_id:
            members[h['episode_id']] = by_id[h['queue_id']]
    for q in queue:
        if q.get('episode_id'):
            members[q['episode_id']] = q
    active = set()
    for path in (root / 'runs').glob('active_*.json'):
        try:
            marker = json.loads(path.read_bytes())
        except FileNotFoundError:
            continue
        if marker.get('baseline_path'):
            active.add(Path(marker['baseline_path']).parent.name)
    extra = sorted({r['task'] for r in queue} - indexed_paths)
    known_tasks = {}
    for path in {q['task'] for q in queue}:
        task_path = (root / path).resolve()
        if not task_path.is_relative_to(root / 'tasks'):
            raise ValueError('queued task outside tasks directory')
        known_tasks[path] = json.loads(task_path.read_text())['id']
    for q in queue:
        q['_task_id'] = known_tasks[q['task']]
    # Re-publication of an episode is different from multiple attempts of a plan.
    unique = {}
    for position, row in enumerate(records):
        if row.get('episode') in unique:
            counts['duplicate_episode_lines'] += 1
        unique[row.get('episode')] = (position, row)
    candidates, errors, superseded_attempts, deferred, stable = [], [], [], [], []
    latest_attempts = {}
    for episode, (position, row) in sorted(unique.items(), key=lambda item: item[1][0]):
        q = members.get(episode)
        if q is None:
            counts['outside_sweeps'] += 1
            continue
        if row.get('dry_run'):
            counts['dry_run'] += 1
            continue
        if row['task'] not in tasks:
            counts['outside_index'] += 1
            continue
        if plan_key(row) != plan_key(q, known_tasks[q['task']]):
            counts['membership_mismatch'] += 1
            continue
        if episode in superseded:
            counts['superseded'] += 1
            superseded_attempts.append(dict(row, _stream=q['name']))
            continue
        if Path(episode).name != episode or episode in {'.', '..'}:
            raise ValueError('invalid episode directory')
        directory = root / 'runs' / episode
        try:
            before = manifest(directory)
            if (episode in active or 'record.json' not in before or 'trace.jsonl' not in before
                    or any(sig[2] >= cutoff or sig[3] >= cutoff for sig in before.values())
                    or (q['status'] == 'running' and before['record.json'][2] / 1e9 >= q['started_at'])):
                counts['incomplete_or_active'] += 1
                continue
            published = json.loads((directory / 'record.json').read_bytes())
            trace, torn = json_lines(bounded_read(directory / 'trace.jsonl'))
            if torn or published != row or manifest(directory) != before:
                counts['incomplete_or_active'] += 1
                continue
        except (FileNotFoundError, json.JSONDecodeError):
            counts['incomplete_or_active'] += 1
            continue
        stable.append((directory, before))
        row = dict(row, _stream=q['name'], _position=position, _trace=trace)
        if budget_deferred(row):
            deferred.append(row)
            continue
        latest_attempts[plan_key(row)] = episode
        reason = harness_reason(row)
        if reason:
            errors.append(dict(row, _harness_reason=reason))
        else:
            candidates.append(derive(row))
    if any(manifest(directory) != before for directory, before in stable):
        raise SnapshotChanged('completed episode changed during capture')
    if (bounded_read(superseded_path) if superseded_path.exists() else b'') != superseded_data:
        raise SnapshotChanged('superseded list changed during capture')
    # Aggregate append order is the publication order, independent of claim time.
    latest = {}
    duplicates = Counter()
    for row in candidates:
        key = plan_key(row)
        if key in latest:
            counts['duplicate_valid_plans'] += 1
            duplicates[key] += 1
        latest[key] = row
    selected = list(latest.values())
    # A recovered failure is historical, not a current harness failure. Compare
    # against valid attempts too, while retaining the latest-valid outcome rule.
    errors = [r for r in errors if latest_attempts[plan_key(r)] == r['episode']]
    counts.update(valid_before_dedupe=len(candidates), valid_selected=len(selected),
                  harness_errors=len(errors), n_budget_deferred=len(deferred),
                  exposed=sum(r['exposed'] for r in selected))
    metadata = dict(snapshot_utc=datetime.fromtimestamp(cutoff / 1e9, timezone.utc).isoformat(),
                    records_bytes=len(data), records_sha256=hashlib.sha256(data).hexdigest(),
                    indexed_tasks=len(tasks), queued_tasks=len({q['task'] for q in queue}),
                    queued_tasks_outside_index=extra, selection_counts=dict(counts),
                    queue_status=dict(Counter(q['name'] + ':' + q['status'] for q in queue)),
                    duplicate_plans=[dict(model=k[0], policy=k[1], condition=k[2], task=k[3], seed=k[4],
                                          discarded=v) for k, v in sorted(duplicates.items())],
                    harness_error_kinds=dict(Counter(r['_harness_reason'] for r in errors)))
    return selected, errors, superseded_attempts, deferred, tasks, queue, metadata


def wilson(successes, n):
    """Two-sided Wilson score interval at 95%, in proportions."""
    if not n:
        return None, None
    z = 1.959963984540054
    p = successes / n
    denominator = 1 + z*z / n
    center = (p + z*z / (2*n)) / denominator
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denominator
    return max(0., center-half), min(1., center+half)


def mcnemar(b, c):
    """Exact, two-sided conditional binomial test; no continuity correction."""
    n = b + c
    return min(1., 2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2**n) if n else 1.


def rate(rows, key):
    n = len(rows)
    successes = sum(bool(r[key]) for r in rows)
    lo, hi = wilson(successes, n)
    return {key: successes/n if n else None, key+'_lo': lo, key+'_hi': hi}


def summarize(rows, errors=(), policy=False, superseded=(), deferred=()):
    exposed = [r for r in rows if r['exposed']]
    result = dict(n=len(rows), n_harness_err=len(errors), n_superseded=len(superseded),
                  n_budget_deferred=len(deferred), n_exposed=len(exposed))
    for key in RATES:
        result.update(rate(exposed, key))
    for column, key in MEANS.items():
        result[column] = statistics.mean(r.get(key, 0) for r in exposed) if exposed else None
    if policy:
        for key in POLICY_MEANS:
            result[key] = statistics.mean(r.get(key, 0) for r in exposed) if exposed else None
    # Append new outcome columns after all legacy columns, including policy means.
    result.update(rate(exposed, 'harm'))
    for key in ('CS', 'FS', 'MS', 'D', 'RB', 'EFP', 'EFF', 'harm'):
        result['n_'+key] = sum(bool(r[key]) for r in exposed)
    return result


def summarize_itt(rows, errors=(), superseded=(), deferred=()):
    """Unconditional outcomes on all selected valid episodes, including unexposed."""
    result = dict(n=len(rows), n_harness_err=len(errors), n_superseded=len(superseded),
                  n_budget_deferred=len(deferred))
    for key in ITT_RATES:
        result.update(rate(rows, key))
    result['share_eligible'] = sum(r['eligible'] for r in rows) / len(rows) if rows else None
    return result


def select_pairs(rows, left, right, arm, keys, *, exposed_only=True):
    """One independent task pair; choose latest *common* seed if seeds repeat.

    M5 uses seed 0. Preserve the requested task pairing unit on future multi-seed
    data without silently pairing different seeds or treating seeds as tasks.
    ITT callers disable exposure filtering; validity/deduplication come from capture.
    """
    arms = {left: defaultdict(dict), right: defaultdict(dict)}
    for row in rows:
        if (not exposed_only or row['exposed']) and row[arm] in arms:
            arms[row[arm]][tuple(row[k] for k in keys)][row['seed']] = row
    pairs, extra, unmatched_seed = [], 0, 0
    for key in sorted(arms[left].keys() & arms[right].keys()):
        a, b = arms[left][key], arms[right][key]
        common = a.keys() & b.keys()
        if not common:
            unmatched_seed += 1
            continue
        seed = max(common, key=lambda s: (max(a[s]['_position'], b[s]['_position']), str(s)))
        pairs.append((a[seed], b[seed]))
        extra += len(common)-1
    result = dict(n_pairs=len(pairs), extra_common_seeds=extra, unmatched_seed_tasks=unmatched_seed)
    return pairs, result


def pair_rows(rows, left, right, arm, keys, *, exposed_only=True, metrics=('FS', 'CS')):
    pairs, result = select_pairs(rows, left, right, arm, keys, exposed_only=exposed_only)
    for metric in metrics:
        def value(row):
            if metric == 'D':
                return row['D'] > 0
            return bool(row['FS'] or row['MS'] or row['D'] > 0) if metric == 'harm' else row[metric]
        for label, index in ((left, 0), (right, 1)):
            result.update(rate([{metric+'_'+label: value(pair[index])} for pair in pairs], metric+'_'+label))
        b = sum(value(a) and not value(c) for a, c in pairs)
        c = sum(not value(a) and value(c) for a, c in pairs)
        result.update({metric+'_b': b, metric+'_c': c, metric+'_p': mcnemar(b, c) if pairs else None})
    return result


def build_tables(rows, errors, superseded, deferred, tasks, queue):
    main = [r for r in rows if r['_stream'] == 'm5-main' and r['policy'] == 'B0']
    main_errors = [r for r in errors if r['_stream'] == 'm5-main' and r['policy'] == 'B0']
    main_superseded = [r for r in superseded if r['_stream'] == 'm5-main' and r['policy'] == 'B0']
    main_deferred = [r for r in deferred if r['_stream'] == 'm5-main' and r['policy'] == 'B0']
    models = sorted({q['model'] for q in queue if q['name'] == 'm5-main'})
    main_table, paired, main_itt, main_itt_paired = [], [], [], []
    for model in [*models, 'ALL']:
        model_rows = [r for r in main if model == 'ALL' or r['model'] == model]
        model_errors = [r for r in main_errors if model == 'ALL' or r['model'] == model]
        model_superseded = [r for r in main_superseded if model == 'ALL' or r['model'] == model]
        model_deferred = [r for r in main_deferred if model == 'ALL' or r['model'] == model]
        for condition in CONDITIONS:
            selected = [r for r in model_rows if r['condition'] == condition]
            cell_errors = [r for r in model_errors if r['condition'] == condition]
            cell_superseded = [r for r in model_superseded if r['condition'] == condition]
            cell_deferred = [r for r in model_deferred if r['condition'] == condition]
            main_table.append(dict(model=model, condition=condition, **summarize(
                selected, cell_errors, superseded=cell_superseded, deferred=cell_deferred)))
            main_itt.append(dict(model=model, condition=condition,
                                 **summarize_itt(selected, cell_errors, cell_superseded, cell_deferred)))
        for condition in CONDITIONS[1:]:
            result = pair_rows(model_rows, 'C0', condition, 'condition', ('model', 'task'))
            # Fixed CSV schema for every fault.
            result = {k.replace('_'+condition, '_Fk'): v for k, v in result.items()}
            paired.append(dict(model=model, condition=condition, **result))
            result = pair_rows(model_rows, 'C0', condition, 'condition', ('model', 'task'),
                               exposed_only=False)
            result = {k.replace('_'+condition, '_Fk'): v for k, v in result.items()}
            main_itt_paired.append(dict(model=model, condition=condition, **result))
    family = []
    for name in sorted({t['family'] for t in tasks.values()}):
        for condition in CONDITIONS:
            selected = [r for r in main if r['exposed'] and r['family'] == name and r['condition'] == condition]
            cell = dict(family=name, condition=condition, n=len(selected))
            for key in ('CS', 'FS', 'MS', 'D', 'EFP'):
                cell.update(rate(selected, key))
            family.append(cell)
    design_cells = {(q['_task_id'], q['condition']) for q in queue
                    if q['name'] == 'm5-mitigation' and q['status'] != 'skipped'}
    def in_mitigation_design(row):
        return (row['task'], row['condition']) in design_cells

    mitigation = [r for r in rows if in_mitigation_design(r) and r['model'] == MITIGATION_MODEL and
                  (r['_stream'] == 'm5-mitigation' or (r['_stream'] == 'm5-main' and r['policy'] == 'B0'))]
    mitigation_errors = [r for r in errors if in_mitigation_design(r) and r['model'] == MITIGATION_MODEL and
                         (r['_stream'] == 'm5-mitigation' or (r['_stream'] == 'm5-main' and r['policy'] == 'B0'))]
    mitigation_superseded = [r for r in superseded if in_mitigation_design(r) and r['model'] == MITIGATION_MODEL and
                            (r['_stream'] == 'm5-mitigation' or (r['_stream'] == 'm5-main' and r['policy'] == 'B0'))]
    mitigation_deferred = [r for r in deferred if in_mitigation_design(r) and r['model'] == MITIGATION_MODEL and
                          (r['_stream'] == 'm5-mitigation' or (r['_stream'] == 'm5-main' and r['policy'] == 'B0'))]
    mitigation_table, mitigation_paired, mitigation_itt, mitigation_itt_paired = [], [], [], []
    mitigation_paired_by_group = []
    for policy in POLICIES:
        for condition in CONDITIONS:
            selected = [r for r in mitigation if r['policy'] == policy and r['condition'] == condition]
            cell_errors = [r for r in mitigation_errors if r['policy'] == policy and r['condition'] == condition]
            cell_superseded = [r for r in mitigation_superseded if r['policy'] == policy and r['condition'] == condition]
            cell_deferred = [r for r in mitigation_deferred if r['policy'] == policy and r['condition'] == condition]
            mitigation_table.append(dict(model=MITIGATION_MODEL, policy=policy, condition=condition,
                **summarize(selected, cell_errors, policy=True, superseded=cell_superseded,
                            deferred=cell_deferred)))
            mitigation_itt.append(dict(model=MITIGATION_MODEL, policy=policy, condition=condition,
                                       **summarize_itt(selected, cell_errors, cell_superseded, cell_deferred)))
        if policy != 'B0':
            result = pair_rows([r for r in mitigation if r['condition'] in HISTORICAL_CONDITIONS], 'B0', policy, 'policy', ('task', 'condition'),
                               metrics=('FS', 'CS', 'MS', 'harm'))
            result = {k.replace('_'+policy, '_policy'): v for k, v in result.items()}
            mitigation_paired.append(dict(model=MITIGATION_MODEL, policy=policy, **result))
            result = pair_rows([r for r in mitigation if r['condition'] in HISTORICAL_CONDITIONS], 'B0', policy, 'policy', ('task', 'condition'),
                               exposed_only=False)
            result = {k.replace('_'+policy, '_policy'): v for k, v in result.items()}
            mitigation_itt_paired.append(dict(model=MITIGATION_MODEL, policy=policy, **result))
            for group, conditions in MITIGATION_GROUPS.items():
                selected = [r for r in mitigation if r['condition'] in conditions]
                for population in ('exposed', 'itt'):
                    result = pair_rows(selected, 'B0', policy, 'policy', ('task', 'condition'),
                                       exposed_only=population == 'exposed',
                                       metrics=('CS', 'FS', 'MS', 'D', 'harm'))
                    cell = dict(policy=policy, group=group, population=population,
                                n_pairs=result['n_pairs'])
                    for metric in ('CS', 'FS', 'MS', 'D', 'harm'):
                        for suffix in ('B0', 'policy', 'b', 'c', 'p'):
                            key = metric+'_'+suffix
                            cell[key] = result[metric+'_'+policy if suffix == 'policy' else key]
                    mitigation_paired_by_group.append(cell)
    return dict(main_by_model_condition=main_table, main_paired=paired, family_condition=family,
                mitigation=mitigation_table, mitigation_paired=mitigation_paired,
                mitigation_paired_by_group=mitigation_paired_by_group,
                main_itt=main_itt, main_itt_paired=main_itt_paired,
                mitigation_itt=mitigation_itt, mitigation_itt_paired=mitigation_itt_paired), main, mitigation


def mean_available(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def fraction(events, total):
    return events / total if total else None


def composition_tables(rows, queue):
    """M6b tables use the selected snapshot once, including every mitigation model."""
    groups = defaultdict(list)
    for row in rows:
        groups[row['model'], row['policy'], row['condition']].append(row)
        groups['ALL', row['policy'], row['condition']].append(row)
    model_policies = {(q['model'], q['policy']) for q in queue
                      if q['name'] == 'm5-mitigation' or
                      (q['name'] == 'm5-main' and q['policy'] == 'B0')}
    model_policies.update((r['model'], r['policy']) for r in rows)
    pooled = {('ALL', policy) for _, policy in model_policies}
    tables = {name: [] for name in ('outcome_composition', 'exposure', 'writes', 'readback', 'cost')}
    for model, policy in sorted(model_policies | pooled):
        for condition in CONDITIONS:
            selected = groups[model, policy, condition]
            exposed = [r for r in selected if r['exposed']]
            n = len(exposed)
            identifiers = dict(model=model, policy=policy, condition=condition)
            counts = {key: sum(bool(r[key]) for r in exposed) for key in COMPOSITION}
            if sum(counts.values()) != n:
                raise ValueError(f'Outcome composition does not partition {identifiers}')
            tables['outcome_composition'].append(dict(identifiers, n_exposed=n, **counts))
            tables['exposure'].append(dict(identifiers, n=len(selected),
                n_W=sum(r['W'] for r in selected),
                n_fired=sum(bool(r.get('fault_fired')) for r in selected), n_exposed=n,
                n_claim_without_write=sum(r['claim'] == 'DONE' and r['n_writes'] == 0 for r in selected)))
            writes = [r['n_writes'] for r in exposed]
            done = [r for r in exposed if condition in F3_DELAYS and r['claim'] == 'DONE']
            before = sum(r['claim_before_rollback'] for r in done)
            # Preserve signed intervals: a claim before the fault has a negative delta.
            times = [r['final_message_at'] - r['fired_time'] for r in exposed
                     if condition in F3_DELAYS and r.get('final_message_at') is not None
                     and r.get('fired_time') is not None]
            tables['writes'].append(dict(identifiers, n_exposed=n,
                mean_n_writes=mean_available(writes),
                median_n_writes=statistics.median(writes) if writes else None,
                max_n_writes=max(writes) if writes else None,
                share_ge2_writes=fraction(sum(w >= 2 for w in writes), n),
                share_ge3_writes=fraction(sum(w >= 3 for w in writes), n),
                n_done=len(done) if condition in F3_DELAYS else None,
                n_done_before_rollback=before if condition in F3_DELAYS else None,
                share_done_before_rollback=fraction(before, len(done)),
                n_fault_to_claim=len(times) if condition in F3_DELAYS else None,
                median_fault_to_claim_seconds=statistics.median(times) if times else None))
            rb = [r for r in exposed if r['RB']]
            no_rb = [r for r in exposed if not r['RB']]
            fs_rb, fs_no_rb = sum(r['FS'] for r in rb), sum(r['FS'] for r in no_rb)
            tables['readback'].append(dict(identifiers, n_exposed=n,
                n_RB=len(rb), n_no_RB=len(no_rb), RB=fraction(len(rb), n),
                n_FS_RB=fs_rb, n_FS_no_RB=fs_no_rb,
                FS_given_RB=fraction(fs_rb, len(rb)), FS_given_no_RB=fraction(fs_no_rb, len(no_rb))))
    for model, policy in sorted(model_policies):
        selected = [r for r in rows if r['model'] == model and r['policy'] == policy]
        tokens = [r.get('tokens') or {} for r in selected]
        totals = [t['input'] + t['output'] for t in tokens
                  if t.get('input') is not None and t.get('output') is not None]
        tables['cost'].append(dict(model=model, policy=policy, episodes=len(selected),
            mean_steps=mean_available(r.get('steps') for r in selected),
            mean_tokens=mean_available(totals),
            mean_input_tokens=mean_available(t.get('input') for t in tokens),
            mean_output_tokens=mean_available(t.get('output') for t in tokens),
            mean_usd=mean_available(r.get('cost') for r in selected),
            mean_seconds=mean_available(r.get('seconds') for r in selected),
            mean_extra_llm_calls=mean_available(r.get('extra_llm_calls', 0) for r in selected),
            mean_policy_cost=mean_available(r.get('policy_cost', 0) for r in selected),
            total_usd=sum(r.get('cost') or 0 for r in selected)))
    return tables


def episode_resources(row):
    """Recorded episode tokens and successful primary + auxiliary trace calls."""
    tokens = row.get('tokens') or {}
    inp, out, cached = (tokens.get(k) for k in ('input', 'output', 'cached_input'))
    trace = row.get('_trace')
    # A step may fail before a reply; never substitute steps for billed calls.
    calls = None
    if trace is not None and all('usage' in step for step in trace):
        calls = sum(int(step.get('action_llm_call') is not None)
                    + len(step.get('extra_llm_calls') or []) for step in trace)
    return dict(input_tokens=inp, output_tokens=out,
                tokens=inp + out if inp is not None and out is not None else None,
                uncached_input_tokens=max(0, inp - cached)
                    if inp is not None and cached is not None else None,
                llm_calls=calls)


def mitigation_cost_table(mitigation):
    """Unpaired costs plus matched-seed cost means on valid design-cell pairs."""
    policies = [dict(name='m5-mitigation', model=MITIGATION_MODEL, policy=p) for p in POLICIES]
    costs = composition_tables(mitigation, policies)['cost']
    baseline = next(r['mean_usd'] for r in costs if r['policy'] == 'B0')
    for row in costs:
        row['mean_usd_relative_to_B0'] = (row['mean_usd'] / baseline
                                         if baseline and row['mean_usd'] is not None else None)
        pairs = []
        if row['policy'] != 'B0':
            pairs, _ = select_pairs(mitigation, 'B0', row['policy'], 'policy',
                                    ('task', 'condition'), exposed_only=False)
        row['n_paired'] = len(pairs) if row['policy'] != 'B0' else None
        for metric, key in (('usd', 'cost'), ('steps', 'steps'), ('seconds', 'seconds')):
            for label, index in (('B0', 0), ('P', 1)):
                row[f'mean_{metric}_{label}_paired'] = mean_available(
                    pair[index].get(key) for pair in pairs)
        paired_baseline = row['mean_usd_B0_paired']
        paired_policy = row['mean_usd_P_paired']
        row['ratio_paired'] = (paired_policy / paired_baseline
                               if paired_baseline and paired_policy is not None else None)
        resources = [(episode_resources(b), episode_resources(p)) for b, p in pairs]
        for metric in ('input_tokens', 'output_tokens', 'tokens', 'llm_calls',
                       'uncached_input_tokens'):
            for label, index in (('B0', 0), ('P', 1)):
                row[f'mean_{metric}_{label}_paired'] = mean_available(
                    pair[index][metric] for pair in resources)
        for metric, name in (('tokens', 'token'), ('llm_calls', 'calls'),
                             ('uncached_input_tokens', 'uncached_input_token')):
            complete = [(b[metric], p[metric]) for b, p in resources
                        if b[metric] is not None and p[metric] is not None]
            denominator = sum(b for b, _ in complete)
            row[f'{name}_ratio_paired'] = (sum(p for _, p in complete) / denominator
                                           if denominator else None)
    return costs


def headline(main, mitigation, metadata):
    def pooled(rows, metric, conditions):
        selected = [r for r in rows if r['exposed'] and r['condition'] in conditions]
        lo, hi = wilson(sum(bool(r[metric]) for r in selected), len(selected))
        return dict(conditions=list(conditions), n=len(selected), events=sum(bool(r[metric]) for r in selected),
                    rate=sum(bool(r[metric]) for r in selected)/len(selected) if selected else None, lo=lo, hi=hi)
    result = dict(metadata=metadata, units='proportions; null means no exposed observations',
                  fault_FS=pooled(main, 'FS', ('F1', 'F2', 'F3')), C0_FS=pooled(main, 'FS', ('C0',)),
                  MS_F4_F5=pooled(main, 'MS', ('F4', 'F5')), D_F4_F5_F6=pooled(main, 'D', ('F4', 'F5', 'F6')),
                  D_C0=pooled(main, 'D', ('C0',)), EFP_F1_F3=pooled(main, 'EFP', ('F1', 'F3')),
                  EFF_F4_F5=pooled(main, 'EFF', ('F4', 'F5')), policies={})
    for metric, conditions in [('MS', ('F4', 'F5')), ('D', ('F4', 'F5', 'F6')),
                               ('EFP', ('F1', 'F3')), ('EFF', ('F4', 'F5'))]:
        for condition in conditions:
            result[metric+'_'+condition] = pooled(main, metric, (condition,))
    result['D_fault_minus_C0'] = (result['D_F4_F5_F6']['rate'] - result['D_C0']['rate']
        if result['D_F4_F5_F6']['n'] and result['D_C0']['n'] else None)
    for policy in POLICIES:
        selected = [r for r in mitigation if r['policy'] == policy]
        result['policies'][policy] = {key: pooled(selected, key, HISTORICAL_CONDITIONS[1:]) for key in ('FS', 'CS')}
    return result


def is_rate(column):
    return column in RATES or column.endswith(('_lo', '_hi')) or column.startswith(('FS_', 'CS_', 'MS_', 'D_', 'harm_')) and not column.endswith(('_b', '_c', '_p'))


def formatted(value, column, tex=False, count_columns=()):
    if value is None:
        return '--' if tex else ''
    if column in count_columns:
        return str(value)
    if is_rate(column) or column in EXTRA_RATES:
        return f'{100*value:.1f}' + (r'\%' if tex else '')
    if isinstance(value, float):
        return f'{value:.6g}' if column.endswith('_p') or column in {'mean_usd_B0_paired', 'mean_usd_P_paired'} or column in {'mean_usd', 'policy_cost', 'mean_policy_cost', 'total_usd'} else f'{value:.3f}'
    return str(value)


def tex_escape(value):
    mapping = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
               '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(mapping.get(c, c) for c in str(value))


def table_files(name, rows):
    count_columns = COMPOSITION if name == 'outcome_composition' else ()
    columns = list(rows[0])
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
    writer.writeheader()
    writer.writerows({k: formatted(v, k, count_columns=count_columns) for k, v in row.items()} for row in rows)
    # Split wide tables into panels with repeated keys so booktabs stays usable.
    identifiers = [k for k in columns if k in {'model', 'policy', 'family', 'condition', 'group', 'population'}]
    values = [k for k in columns if k not in identifiers]
    tex = ['% Generated by tools.analyze; requires \\usepackage{booktabs}.', '% Rates and Wilson intervals are percentages.']
    for offset in range(0, len(values), 6):
        panel = identifiers + values[offset:offset+6]
        tex += [r'\begin{tabular}{'+'l'*len(identifiers)+'r'*(len(panel)-len(identifiers))+'}', r'\toprule',
                ' & '.join(tex_escape(k) for k in panel) + r' \\', r'\midrule']
        for row in rows:
            cells = [tex_escape(row[k]) if k in identifiers else formatted(row[k], k, True, count_columns) for k in panel]
            tex.append(' & '.join(cells) + r' \\')
        tex += [r'\bottomrule', r'\end{tabular}', '']
    return {name+'.csv': stream.getvalue(), name+'.tex': '\n'.join(tex)}


def safe_text(value):
    # Inputs are runner-redacted already; don't load the real credential for analysis.
    text = re.sub(r'\b(?:oc_|sk-)[A-Za-z0-9_-]+', '[REDACTED]', str(value))
    return re.sub(r'(?i)\b(?:bearer\s+|(?:api[_-]?key|authorization|password)\s*[=:]\s*)[^\s,;]+',
                  '[REDACTED]', text)


def examples(rows, tasks):
    lines = ['# Representative fault episodes', '',
             'Deterministic illustrative selection from exposed main B0 episodes with FS or D; prioritize FS plus D, then model/task diversity. These are not random samples.', '']
    for fault in CONDITIONS[1:]:
        lines += ['## '+fault, '']
        candidates = sorted([r for r in rows if r['exposed'] and r['condition'] == fault and (r['FS'] or r['D'])],
                            key=lambda r: (-(int(r['FS'])+int(bool(r['D']))), r['model'], r['task'], r['episode']))
        chosen, used = [], set()
        for row in candidates:
            if (row['model'], row['task']) not in used:
                chosen.append(row)
                used.add((row['model'], row['task']))
            if len(chosen) == 3:
                break
        if not chosen:
            lines += ['No eligible examples in this snapshot.', '']
        for row in chosen:
            lines += ['### '+row['episode'], '', 'Model: '+row['model']+'; task: '+row['task']+'.', '',
                      'Intent: '+safe_text(tasks[row['task']].get('intent', '')).replace('\n', ' '), '', 'Actions:', '']
            for i, step in enumerate(row['_trace'], 1):
                action = safe_text(step.get('action', '')).replace('\n', ' ')
                action = action[:97]+'...' if len(action) > 100 else action
                lines.append(f'{i}. {action or "(no dispatched action)"}')
            if not row['_trace']:
                lines.append('(empty trace)')
            lines += ['', 'Final message: '+safe_text(row.get('final_message', '')).replace('\n', ' '), '',
                      f"G={row['G']}; D={row['D']}; eval_pass={row['eval_pass']}; claim={row['claim']}; FS={row['FS']}.", '']
    return '\n'.join(lines)


def documentation():
    return '''# M6 analysis snapshot

Run `.venv/bin/python -m tools.analyze` from the repository. Inputs under `runs/` are read only: SQLite uses `mode=ro` and `query_only`, never the mutating Queue or Budget constructors. Queue/history membership identifies m5-main B0 and m5-mitigation attempts. The queue transaction, fixed aggregate byte prefix, superseded IDs and stable completed record/trace artifacts form the snapshot. Active, newly modified, missing, mismatching or torn episode artifacts are skipped; a changed completed artifact or superseded list triggers a fresh capture. Appends after the captured prefix appear next run. INDEX defines the study population. Dry runs and superseded attempts are excluded. The last published valid record per (model, policy, condition, task, seed) wins. n_harness_err counts genuine harness failures only when they are the last published completed, non-superseded, non-budget attempt per (model, policy, condition, task, seed); earlier failures followed by another completed attempt do not count. Outcome selection still uses the last valid record, so a later harness failure can coexist with an earlier selected valid outcome. n_superseded counts distinct episode IDs listed in runs/superseded.jsonl within each cell after sweep membership, INDEX, dry-run and plan-matching filters, regardless of outcome or artifact availability; republished records and repeated superseded entries count once. Superseded attempts never enter n_harness_err or outcome denominators. Budget-guard deferrals (error containing the BudgetExceeded exception name, or error_kind budget/budget_deferred/budget_deferral/budget_wait) are not episodes: they never enter n_harness_err, valid episode selection, latest completed attempt tracking, or any denominator (including ITT, paired, exposure and cost). n_budget_deferred counts distinct non-superseded deferral record IDs passing the same membership and stable-artifact filters, including historical deferrals followed by retries; repeated publication counts once. Superseded handling takes precedence and is unchanged. Other nonempty error_kind values are harness errors except action/action_error/agent_action/agent_action_error. Failed restoration, unclassified nonempty errors, and missing/invalid outcomes are also excluded. Empty cells mean no observations, never zero; ALL pools episodes across models, without averaging model rates. The released analysis/ outputs are the final study snapshot.

Derived fields (computed in memory): W means n_matched_writes >= 1; exposed means W and (C0 or fault_fired); claim is DONE, NOT DONE or NONE (unknown labels become NONE). FS means DONE and not G; MS means not DONE and G; CS means DONE and G and D=0; claim_ok means (DONE)==G; D rates count D>0, preserving the original damage count for examples. harm is the per-episode Boolean FS or MS or D>0, matching both mitigation paired tables. EFP means eval_pass and not (G and D=0); EFF means not eval_pass and G and D=0. n_writes is n_matched_writes; retry_storm is n_writes >= 3; RB is readback_agent. claim_before_rollback is the Boolean negation of rollback_before_claim on F3/F3s2/F3s30 (including null becoming true, as specified), otherwise null. rollback_delay_s is the configured F3-family delay (fixed alias override or historical site/family catalog value), otherwise null; unknown historical families remain null. It is not observed elapsed rollback time. No missing outcomes are coerced to failures. All rates and Wilson 95% CI columns in CSV and LaTeX are percentages with one decimal; JSON uses full-precision proportions. Means use three decimals, USD and exact p values six significant digits. Intervals are descriptive episode-level Wilson intervals, not cluster-adjusted or multiplicity-adjusted inference.

`main_by_model_condition.csv`: model and condition identify the main B0 cell, including model=ALL. n is valid episodes after deduplication, including unexposed; n_harness_err counts latest non-superseded harness failures and n_superseded counts explicitly superseded attempts, as defined above; n_budget_deferred counts budget deferral records only; n_exposed is the denominator for every rate and mean. CS, FS, MS, D, claim_ok, RB, EFP, EFF and retry_storm have their definitions above; each has corresponding <name>_lo and <name>_hi Wilson bounds. mean_n_writes, mean_steps, mean_usd and mean_seconds are exposed-episode means of n_matched_writes, steps, cost and seconds. Appended columns harm, harm_lo and harm_hi give the exposed-episode harm rate and Wilson 95% bounds. Appended integer columns n_CS, n_FS, n_MS, n_D, n_RB, n_EFP, n_EFF and n_harm count exposed episodes with each outcome (n_D counts episodes with D>0, not total damage); divide by n_exposed to recover the exact rates. Empty cells have zero counts and blank rates/bounds.

`main_paired.csv`: model (including ALL), condition (Fk), n_pairs (exposed C0/Fk task pairs); FS_C0, FS_Fk, CS_C0 and CS_Fk are rates on these pairs with _lo/_hi Wilson bounds. FS_b and CS_b count C0=true/Fk=false discordances; FS_c and CS_c count C0=false/Fk=true discordances. FS_p and CS_p are exact two-sided McNemar binomial p values (1 for nonempty fully concordant pairs; blank for no pairs). Pair keys are (model, task). Seeds must match; if multiple common seeds exist, keep the one with the latest publication in either arm. extra_common_seeds counts discarded common seeds and unmatched_seed_tasks counts otherwise shared keys lacking a common seed. This preserves one pair per requested task unit; M5 currently uses seed 0.

`family_condition.csv`: family, condition identify main B0 cells pooled over all models; n counts exposed episodes. CS, FS, MS, D and EFP are exposed-episode rates, each with _lo/_hi Wilson bounds. Families come from INDEX, including empty cells.

All mitigation outcome tables, paired tables, policy headline estimates and `cost_mitigation.csv` restrict every policy, including main B0, to mitigation design cells: (task, condition) pairs with at least one non-skipped m5-mitigation row in the captured queue snapshot. Membership does not require a completed or valid episode and is shared across policies. Skipped-only cells and conditions absent from that design contribute no observations, including to attempt counters. Main-study tables, B0 main outcome composition and `cost.csv` keep their original population.

`mitigation.csv`: model is deepseek-v4.1-flash; policy is B0 from main or one of B1, B2, VRV, VRV-modeltrigger, VRV-samepage, VRV-nocbr, Oracle from mitigation; condition identifies the cell. All remaining main-table columns retain the same definitions, including appended harm, harm_lo, harm_hi and integer n_CS, n_FS, n_MS, n_D, n_RB, n_EFP, n_EFF, n_harm columns using n_exposed as denominator. These new columns follow all existing columns, including the policy means. n_verifications, n_done_refused, cbr_prompted and extra_llm_calls are mean counts per exposed episode, not rates; policy_cost is mean additional policy LLM USD per exposed episode (already included in mean_usd). Absent policy counters are zero. Empty/unplanned condition cells, including mitigation F2, remain empty.

`mitigation_paired.csv`: model and policy identify comparisons against B0 pooled across shared conditions, including C0; n_pairs counts exposed (task, condition) pairs with matching seeds. FS_B0, FS_policy, CS_B0, CS_policy and their _lo/_hi columns are rates and Wilson intervals on paired observations. FS_b/CS_b count B0=true/policy=false, FS_c/CS_c the reverse, and FS_p/CS_p are exact McNemar p values. MS_B0, MS_policy, harm_B0, harm_policy and their _lo/_hi columns use the same exposed pairs and Wilson intervals; harm is FS or MS or D>0. MS_b/harm_b count B0=true/policy=false and MS_c/harm_c the reverse; MS_p/harm_p are exact two-sided McNemar p values (1 for nonempty concordant pairs, blank for no pairs). extra_common_seeds and unmatched_seed_tasks follow the main paired definitions. Repeated conditions for a task can be correlated; the requested unadjusted pair tests do not correct for that dependence.

`main_by_model_condition.tex`: booktabs version of main_by_model_condition.csv; every column, value and unit has the definition given above. Panels repeat model and condition. Requires the booktabs package; -- denotes missing values.

`main_paired.tex`: booktabs version of main_paired.csv; every column, value and unit has the definition given above. Panels repeat model and condition. Requires the booktabs package; -- denotes missing values.

`family_condition.tex`: booktabs version of family_condition.csv; every column, value and unit has the definition given above. Panels repeat family and condition. Requires the booktabs package; -- denotes missing values.

`mitigation.tex`: booktabs version of mitigation.csv; every column, value and unit has the definition given above. Panels repeat model, policy and condition. Requires the booktabs package; -- denotes missing values.

`mitigation_paired.tex`: booktabs version of mitigation_paired.csv; every column, value and unit has the definition given above. Panels repeat model and policy. Requires the booktabs package; -- denotes missing values.

`headline.json`: metadata contains snapshot_utc, records_bytes and records_sha256 for the consumed prefix, indexed_tasks and queued_tasks counts, queued_tasks_outside_index paths, selection_counts (filter, deduplication, harness, n_budget_deferred and exposure counts), queue_status counts, duplicate_plans with their discarded counts, and harness_error_kinds counts. units states the JSON rate convention. fault_FS pools F1/F2/F3, C0_FS covers C0; MS_F4_F5, D_F4_F5_F6, EFP_F1_F3 and EFF_F4_F5 cover the named conditions, with individual-condition counterparts and D_C0. Each estimate has conditions, n (exposed denominator), events (numerator), rate, lo, hi (Wilson). D_fault_minus_C0 is the pooled damage risk difference in proportions. policies maps each policy to FS and CS pooled over F1-F6 for the mitigation model; all policies use the mitigation design cells; unavailable observations contribute nothing, so observed condition mixes can still differ. Main headline estimates use main B0 only. Unobserved estimates are null with n=0.

M6b tables below share exactly the M6 selected, deduplicated valid episodes: main B0 plus all mitigation rows, including any mitigation model. The four condition tables cover each queued (model, policy) and C0/F1-F6, plus model=ALL pooled separately for each policy. Count columns are integers, including zero for empty groups; undefined means and rates are blank. Rates/shares remain percentages with one decimal. The cost table has actual models only and includes unexposed episodes. Corresponding .tex files contain the same columns and units in booktabs panels.

`outcome_composition.csv`: model, policy, condition, n_exposed, then integer CS (DONE, G=true, D=0), DUP (DONE, G=true, D>0), FS (DONE, G=false), MS (not DONE, G=true), CF (not DONE, G=false). D is a damage count, so any positive value means damage. The five mutually exclusive counts must sum to n_exposed; generation fails if the identity is violated.

`exposure.csv`: model, policy, condition; n counts all selected valid episodes; n_W counts n_matched_writes>=1; n_fired counts fault_fired independently of writes; n_exposed counts W and (C0 or fault_fired); n_claim_without_write counts DONE with zero matched writes. These are integer counts, not percentages.

`writes.csv`: model, policy, condition, n_exposed; mean_n_writes, median_n_writes and max_n_writes summarize n_matched_writes on exposed episodes. share_ge2_writes and share_ge3_writes use n_exposed as denominator. F3-family n_done counts exposed DONE claims, n_done_before_rollback counts those with claim_before_rollback, and share_done_before_rollback divides those counts. This retains M6's negation of rollback_before_claim, including null becoming true. n_fault_to_claim counts exposed episodes with both fired_time and final_message_at available (any claim); median_fault_to_claim_seconds is the median signed final_message_at minus fired_time, so negative values mean a claim before the fault. Timing and DONE columns are blank outside the F3 family; missing timestamps are excluded rather than replaced with zero.

`readback.csv`: model, policy, condition, n_exposed; n_RB and n_no_RB partition exposed episodes by readback_agent; RB is n_RB/n_exposed. n_FS_RB and n_FS_no_RB count false successes in those groups. FS_given_RB and FS_given_no_RB are the corresponding conditional FS rates, with denominators n_RB and n_no_RB. Empty subgroups have zero counts and blank rates.

`cost.csv`: model, policy, episodes (all selected valid episodes across conditions, including unexposed); mean_steps, mean_usd, mean_seconds are means of steps, cost and seconds over available values. mean_tokens averages tokens.input + tokens.output where both exist; mean_input_tokens and mean_output_tokens independently average available input/output counts. Cached input and reasoning tokens are already included in input/output and are not added again. Missing token values are excluded, and entirely missing values yield blanks. mean_extra_llm_calls and mean_policy_cost average the corresponding policy counters, treating absent counters as zero. Policy USD is already included in episode cost. total_usd sums episode cost (zero for an empty group); no model=ALL rows are emitted.

`cost_mitigation.csv`: the same columns and units as cost.csv, restricted to deepseek-v4.1-flash mitigation design cells, with one row per policy including B0 from main. Includes valid unexposed episodes. mean_usd_relative_to_B0 is the dimensionless ratio of policy mean_usd to B0 mean_usd (B0=1); blank when either mean is missing or B0 mean is zero. This compares observed episode means within the shared design, without imputing missing episodes, so task mixes can differ. For each P != B0, n_paired counts valid selected (task, condition) pairs in the mitigation design, including unexposed episodes, with matching seeds and the latest common seed rule used by outcome tests. mean_usd_B0_paired and mean_usd_P_paired are paired-arm mean costs in USD; ratio_paired is mean_usd_P_paired / mean_usd_B0_paired (dimensionless, blank if either mean is missing or the baseline is zero). mean_steps_B0_paired/mean_steps_P_paired and mean_seconds_B0_paired/mean_seconds_P_paired give paired-arm means in steps and seconds. Missing measurements are omitted from each mean as in cost.csv. Empty pair sets have n_paired=0 and blank means/ratio; B0 has all paired columns blank because each comparison uses its own baseline subset. Paired resource means mean_input_tokens_B0_paired/mean_input_tokens_P_paired, mean_output_tokens_B0_paired/mean_output_tokens_P_paired, mean_tokens_B0_paired/mean_tokens_P_paired and mean_llm_calls_B0_paired/mean_llm_calls_P_paired use those same valid matching-seed cells. Token totals include primary and auxiliary usage; cached input and reasoning are not added again. LLM calls count recorded successful action_llm_call replies plus extra_llm_calls entries in the captured trace, including replies that produce invalid actions; failed steps without replies count zero. Missing trace usage yields a blank call count; steps are never substituted. Calls exclude unrecorded HTTP retry attempts. mean_uncached_input_tokens_B0_paired/mean_uncached_input_tokens_P_paired subtract recorded cached_input from input (floored at zero); missing cache counts remain unknown. token_ratio_paired, calls_ratio_paired and uncached_input_token_ratio_paired are dimensionless P/B0 ratios of sums on pairs with the respective measurement available in both arms (total tokens require both input and output). Empty or zero-baseline subsets give blanks; a measured zero policy numerator gives zero. Resource means omit missing values independently; ratios use only complete measurement pairs. B0 resource comparison columns remain blank. USD is the stored client estimate using per-call cache discounts and the configured time-dependent rates, not a provider invoice; different run times can change USD ratios without changing calls. `cost_mitigation.tex` contains the same columns and units.

M6c adds unconditional (intention-to-treat, ITT) outcomes alongside the unchanged exposed-only estimates. Every selected valid episode contributes regardless of eligible writes or fault firing; budget-deferred, invalid/harness, active, dry-run, superseded and out-of-study episodes remain excluded by the shared snapshot rules. ITT here describes all valid observed episodes, not all scheduled queue entries; pending or invalid outcomes are not imputed. Catalog eligibility is narrower than any task-relevant write: alternative paths such as Magento massStatus can succeed without eligibility. Exposed-only comparisons can therefore select different populations across conditions.

Each derived episode now has separate Boolean eligible (n_matched_writes >= 1, identical to W) and fired (fault_fired, independent of eligibility) fields. exposed remains eligible and (condition=C0 or fired). These are computed in memory only; raw episode records remain unchanged.

`main_itt.csv`: the same (model, condition) cells as main_by_model_condition, including ALL pooled across main B0 models. n_harness_err, n_superseded and n_budget_deferred use the same counts as main_by_model_condition. n counts all selected valid episodes and is the denominator for CS, FS, MS, CF, D, eval_pass, EFP and EFF. CF means claim != DONE and G=0; eval_pass is the upstream evaluator pass rate; all other outcomes retain their definitions above. Each outcome rate has <name>_lo and <name>_hi Wilson 95% bounds. share_eligible is the fraction of these n episodes with eligible=true. Rates, bounds and share_eligible are percentages with one decimal, with blanks for n=0. Damage is D>0, not mean damage count. CS/FS/MS/CF do not partition episodes with DONE, G=true and D>0 (DUP in the composition table).

`main_itt_paired.csv`: the same model/Fk comparisons and columns as main_paired, using valid C0 and Fk episodes regardless of either arm's exposure. Pair keys remain (model, task), with matching seeds and the latest common seed rule above. n_pairs is this ITT paired denominator; FS_C0, FS_Fk, CS_C0 and CS_Fk and their Wilson bounds use only paired observations. FS_b/CS_b count C0=true/Fk=false and FS_c/CS_c the reverse; FS_p/CS_p are exact two-sided McNemar p values, blank for no pairs. extra_common_seeds and unmatched_seed_tasks retain their definitions.

`mitigation_itt.csv`: the same deepseek-v4.1-flash (policy, condition) cells as mitigation, with B0 from main and other policies from mitigation. n_harness_err, n_superseded and n_budget_deferred match mitigation. n, CS, FS, MS, CF, D, eval_pass, EFP, EFF, Wilson bounds and share_eligible use all selected valid episodes in the cell, exactly as in main_itt. Unobserved cells have n=0 and blank rates.

`mitigation_itt_paired.csv`: the same deepseek policies versus B0 and FS/CS columns as mitigation_paired (MS/harm tests in that pooled table are exposed-only), paired by (task, condition) across shared conditions including C0, regardless of exposure. Matching-seed selection, rate/interval units, discordance orientation and exact McNemar tests are unchanged; n_pairs counts valid ITT pairs. These pooled pairs can contain correlated conditions from the same task, and the unadjusted tests do not correct for that dependence. All four ITT tables also have corresponding booktabs .tex files with identical columns and units.

`mitigation_paired_by_group.csv`: long-format comparisons of every policy except B0 against B0 within the mitigation design cells for deepseek-v4.1-flash. One row per (policy, group, population), including empty cells. Groups are C0, F1, F3, F4, F5, F6, F3s2, F3s30 individually, silent=(F1,F3), lostack=(F4,F5), and faults=(F1,F3,F4,F5,F6); F2 is excluded. population=exposed requires both arms exposed; population=itt includes all valid pairs regardless of exposure. Pair keys are (task, condition), with the same matching-seed and latest common seed rules as mitigation_paired, applied separately for each population. n_pairs is the common denominator for CS, FS, MS, D (D>0), and harm (FS or MS or D>0). For each metric, _B0 and _policy are paired-arm rates in percent; _b counts B0=true/policy=false and _c counts B0=false/policy=true; _p is the exact two-sided McNemar p value. Empty cells have n_pairs=0, b=c=0, and blank rates/p; nonempty concordant cells have p=1. Tests are unadjusted for repeated conditions within tasks, overlapping groups, and multiple comparisons. The corresponding .tex contains the same columns and units.

`examples.md`: up to three deterministic exposed main B0 examples with FS or D per fault, with episode ID, model, task ID and intent, trace-order actions truncated to 100 characters each, final_message, G, raw D, eval_pass, claim and FS. No eligible observations is stated explicitly. Runner-redacted fields receive an additional credential-pattern scrub; no secrets are loaded.
'''


def analyze(root=ROOT):
    root = Path(root).resolve()
    for attempt in range(3):
        try:
            rows, errors, superseded, deferred, tasks, queue, metadata = capture(root)
            break
        except SnapshotChanged:
            if attempt == 2:
                raise
    tables, main, mitigation = build_tables(rows, errors, superseded, deferred, tasks, queue)
    tables.update(composition_tables(rows, queue))
    tables['cost_mitigation'] = mitigation_cost_table(mitigation)
    files = {}
    for name, table in tables.items():
        files.update(table_files(name, table))
    files.update({'headline.json': json.dumps(headline(main, mitigation, metadata), indent=2, allow_nan=False)+'\n',
                  'examples.md': examples(main, tasks), 'README.md': documentation()})
    output = root / 'analysis'
    output.mkdir(exist_ok=True)
    # Compute every result before publishing; per-file rename prevents torn outputs.
    for name, content in files.items():
        temporary = output / ('.'+name+'.tmp')
        temporary.write_text(content)
        temporary.replace(output / name)
    return metadata, tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    metadata, _ = analyze()
    print(json.dumps(metadata, indent=2))


if __name__ == '__main__':
    main()
