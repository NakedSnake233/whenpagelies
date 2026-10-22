"""Analyse the proprietary-model run (gpt-5.6-luna, baseline agent B0, C0/F1/F3/F4).

Inputs (same per-episode schema as the main study):
  data/episodes_proprietary.jsonl.gz   (override with WPL_PROP; comma-separated list allowed)
  data/episodes_main.jsonl.gz          (four open-weight models, for comparison; WPL_DATA sets the directory)
Output: analysis/review/proprietary/proprietary_raw.json (unrounded rates in %), and a short markdown summary on stdout.

Metrics follow the main study: fault-condition rates are over exposed episodes; comparisons
between conditions are exact McNemar tests on episodes paired by task (one episode per task and
condition, so tasks are the clusters and the pairs at once); the difference has a task-level
bootstrap interval.  The comparison with the open-weight models uses Fisher's exact test on
exposed episodes; it is descriptive, since the open-weight episodes share tasks.
"""
import gzip
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import robustness as R  # noqa: E402  (exact_mcnemar, cluster_boot, cochran_q)

DATA = Path(os.environ.get('WPL_DATA', 'data'))
# One or more exports, separated by commas (e.g. the main sweep and a retry of a harness failure).
PROP = [Path(x) for x in os.environ.get('WPL_PROP', str(DATA / 'episodes_proprietary.jsonl.gz')).split(',')]
OUT = Path(os.environ.get('WPL_OUT', 'analysis/review/proprietary'))
CONDS = ('C0', 'F1', 'F3', 'F4')
METRICS = ('CS', 'FS', 'MS', 'D', 'RB', 'EFP', 'EFF')


def load(p):
    with gzip.open(p, 'rt') as fh:
        return [json.loads(line) for line in fh]


def flag(e, m):
    if m == 'D':
        return bool(e['D'])
    if m == 'RB':
        return bool(e['readback_agent'])
    return bool(e[m])


def rate(eps, m):
    return None if not eps else 100.0 * sum(flag(e, m) for e in eps) / len(eps)


def wilson(k, n, z=1.959964):
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / d
    return 100 * (c - h), 100 * (c + h)


def summary(eps):
    out = {}
    for c in CONDS:
        allc = [e for e in eps if e['condition'] == c]
        ex = [e for e in allc if e['exposed']]
        row = {'n': len(allc), 'n_exposed': len(ex),
               'exposure': rate(allc, 'exposed') if allc else None}
        for m in METRICS:
            k = sum(flag(e, m) for e in ex)
            row[m] = rate(ex, m)
            row['n_' + m] = k
            row[m + '_ci'] = wilson(k, len(ex))
            row[m + '_all'] = rate(allc, m)  # over all episodes, exposed or not
        row['claims_done'] = sum(e['claim'] == 'DONE' for e in ex)
        row['eval_pass'] = rate(ex, 'eval_pass')
        row['mean_steps'] = float(np.mean([e['steps'] for e in allc])) if allc else None
        row['mean_writes_exposed'] = float(np.mean([e['n_matched_writes'] for e in ex])) if ex else None
        row['usd'] = float(sum(e['cost'] for e in allc))
        out[c] = row
    return out


def by_task(eps, cond):
    return {e['task']: e for e in eps if e['condition'] == cond}


def paired(eps, cond, m):
    ref, new = by_task(eps, 'C0'), by_task(eps, cond)
    pairs = [(t, flag(ref[t], m), flag(new[t], m)) for t in sorted(set(ref) & set(new))
             if ref[t]['exposed'] and new[t]['exposed']]
    n = len(pairs)
    if n == 0:
        return None
    b = sum(1 for _, x, y in pairs if x and not y)
    c = sum(1 for _, x, y in pairs if y and not x)
    lo, hi = R.cluster_boot(pairs)
    return {'n_pairs': n,
            'C0': 100.0 * sum(x for _, x, _ in pairs) / n,
            cond: 100.0 * sum(y for _, _, y in pairs) / n,
            'b_C0_only': b, 'c_new_only': c,
            'p_exact': R.exact_mcnemar(b, c),
            'diff_ci': (100 * lo, 100 * hi)}


def exposure_balance(eps):
    tasks = sorted({e['task'] for e in eps})
    cell = {(e['task'], e['condition']): int(bool(e['exposed'])) for e in eps}
    blocks = [[cell[(t, c)] for c in CONDS] for t in tasks if all((t, c) in cell for c in CONDS)]
    q, p = R.cochran_q(blocks)
    return {'n_tasks': len(blocks), 'Q': q, 'p': p}


def compare(prop, main):
    """Proprietary model against the four open-weight models (B0, same conditions)."""
    open_ = [e for e in main if e['policy'] == 'B0' and e['condition'] in CONDS]
    models = sorted({e['model'] for e in open_})
    rows = {}
    spec = [('CS', ('C0',)), ('FS', ('C0',)), ('FS', ('F1',)), ('FS', ('F3',)), ('FS', ('F1', 'F3')),
            ('RB', ('F1', 'F3')), ('EFP', ('F1', 'F3')), ('CS', ('F4',)), ('MS', ('F4',)),
            ('D', ('F4',)), ('RB', ('F4',)), ('EFF', ('F4',))]
    for m, cs in spec:
        key = f"{m}@{'+'.join(cs)}"
        p_ex = [e for e in prop if e['condition'] in cs and e['exposed']]
        o_ex = [e for e in open_ if e['condition'] in cs and e['exposed']]
        kp, ko = sum(flag(e, m) for e in p_ex), sum(flag(e, m) for e in o_ex)
        per = {}
        for mod in models:
            ex = [e for e in o_ex if e['model'] == mod]
            per[mod] = rate(ex, m)
        _, p = stats.fisher_exact([[kp, len(p_ex) - kp], [ko, len(o_ex) - ko]])
        rows[key] = {'prop': rate(p_ex, m), 'prop_k': kp, 'prop_n': len(p_ex),
                     'open': rate(o_ex, m), 'open_k': ko, 'open_n': len(o_ex),
                     'open_min': min(v for v in per.values() if v is not None),
                     'open_max': max(v for v in per.values() if v is not None),
                     'per_model': per, 'p_fisher': float(p)}
    return rows


def main():
    prop = [e for path in PROP for e in load(path)]
    prop = [e for e in prop if e['policy'] == 'B0']
    keys = [(e['task'], e['condition'], e['seed']) for e in prop]
    assert len(keys) == len(set(keys)), 'duplicate (task, condition, seed) across exports'
    models = {e['model'] for e in prop}
    assert len(models) == 1, models
    main_eps = load(DATA / 'episodes_main.jsonl.gz')
    res = {'model': models.pop(), 'n_episodes': len(prop),
           'n_tasks': len({e['task'] for e in prop}),
           'by_condition': summary(prop),
           'paired': {c: {m: paired(prop, c, m) for m in ('FS', 'CS', 'MS', 'D', 'RB')}
                      for c in ('F1', 'F3', 'F4')},
           'exposure_balance': exposure_balance(prop),
           'vs_open': compare(prop, main_eps),
           'usd_total': float(sum(e['cost'] for e in prop)),
           'silent_claims': None}
    sil = [e for e in prop if e['condition'] in ('F1', 'F3') and e['exposed']]
    res['silent_claims'] = {'n_exposed': len(sil),
                            'claimed_done': sum(e['claim'] == 'DONE' for e in sil),
                            'readback': sum(bool(e['readback_agent']) for e in sil),
                            'goal_met': sum(bool(e['G']) for e in sil)}
    (OUT / 'proprietary_raw.json').write_text(json.dumps(res, indent=1, sort_keys=True))

    f = lambda x: '-' if x is None else f'{x:.1f}'
    print(f"# {res['model']}: {res['n_episodes']} episodes, {res['n_tasks']} tasks, ${res['usd_total']:.2f}\n")
    print('| cond | n | exp | CS | FS | MS | D | RB | EFP | EFF | steps |')
    print('|---|---|---|---|---|---|---|---|---|---|---|')
    for c, r in res['by_condition'].items():
        print(f"| {c} | {r['n']} | {r['n_exposed']} | " + ' | '.join(f(r[m]) for m in METRICS)
              + f" | {f(r['mean_steps'])} |")
    eb = res['exposure_balance']
    print(f"\nExposure balance: Q={eb['Q']:.2f}, p={eb['p']:.3f} over {eb['n_tasks']} tasks")
    print('\nPaired vs C0 (exposed in both):')
    for c, d in res['paired'].items():
        for m, v in d.items():
            if v:
                print(f"  {c} {m}: n={v['n_pairs']} C0={f(v['C0'])} {c}={f(v[c])} "
                      f"b={v['b_C0_only']} c={v['c_new_only']} p={v['p_exact']:.2e} "
                      f"CI=({f(v['diff_ci'][0])},{f(v['diff_ci'][1])})")
    print('\nVs open-weight (exposed, pooled):')
    for k, v in res['vs_open'].items():
        print(f"  {k}: prop {f(v['prop'])} ({v['prop_k']}/{v['prop_n']}) open {f(v['open'])} "
              f"[{f(v['open_min'])}-{f(v['open_max'])}] p={v['p_fisher']:.3g}")
    print('\nSilent faults:', res['silent_claims'])


if __name__ == '__main__':
    main()
