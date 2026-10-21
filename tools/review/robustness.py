"""Robustness analyses for 'When the Page Lies' (review round 1).

Reads the released per-episode tables (data/episodes_main.jsonl.gz,
data/episodes_mitigation.jsonl.gz) and writes data/robustness.json and
data/robustness.md.  Everything is recomputed from per-episode rows:

* exposure balance across conditions (Cochran's Q over model x task blocks);
* paired differences with task-clustered bootstrap intervals and the
  cluster-adjusted McNemar test of Durkalski et al. (2003);
* Holm adjustment over the 21 policy-vs-B0 tests of the mitigation study;
* GEE logistic models (task clusters) as a model-based check;
* leave-one-family-out ranges of headline rates;
* step-budget exhaustion by policy;
* per-model rates.
"""
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats

import os

ROOT = Path(__file__).resolve().parents[2]
# DATA holds episodes_*.jsonl.gz; FLAGS the validity flags; OUT receives robustness.{json,md}.
DATA = Path(os.environ.get('WPL_DATA', ROOT / 'data'))
FLAGS = Path(os.environ.get('WPL_FLAGS', ROOT / 'analysis' / 'review' / 'episode_flags.csv'))
OUT = Path(os.environ.get('WPL_OUT', ROOT / 'analysis' / 'review'))
RNG_SEED = 20261021
N_BOOT = 10000
SILENT = ('F1', 'F3')
LOSTACK = ('F4', 'F5')


def load(name):
    with gzip.open(DATA / name, 'rt') as fh:
        return [json.loads(line) for line in fh]


def pct(x):
    return None if x is None else round(100.0 * x, 1)


def exact_mcnemar(b, c):
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2.0 * stats.binom.cdf(min(b, c), n, 0.5))


def clustered_mcnemar(pairs):
    """Durkalski et al. (2003) cluster-adjusted McNemar statistic.

    pairs: iterable of (cluster, x_ref, x_new) with Boolean outcomes.
    Returns (statistic, p) or (None, None) without discordant pairs.
    """
    diff = defaultdict(int)
    for k, a, b in pairs:
        if a and not b:
            diff[k] += 1
        elif b and not a:
            diff[k] -= 1
    num = sum(diff.values()) ** 2
    den = sum(v * v for v in diff.values())
    if den == 0:
        return None, None
    x2 = num / den
    return x2, float(stats.chi2.sf(x2, 1))


def cluster_boot(pairs, n_boot=N_BOOT, seed=RNG_SEED):
    """Task-clustered percentile bootstrap of mean(x_new) - mean(x_ref)."""
    by = defaultdict(list)
    for k, a, b in pairs:
        by[k].append((float(a), float(b)))
    keys = sorted(by)
    sums = np.array([[sum(p[0] for p in by[k]), sum(p[1] for p in by[k]), len(by[k])] for k in keys])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    s = sums[idx].sum(axis=1)
    d = (s[:, 1] - s[:, 0]) / s[:, 2]
    lo, hi = np.percentile(d, [2.5, 97.5])
    return float(lo), float(hi)


def paired_summary(pairs, cluster_label='task'):
    pairs = list(pairs)
    n = len(pairs)
    a = sum(1 for _, x, _ in pairs if x)
    b_ = sum(1 for _, _, y in pairs if y)
    disc_ref = sum(1 for _, x, y in pairs if x and not y)
    disc_new = sum(1 for _, x, y in pairs if y and not x)
    x2, p_cl = clustered_mcnemar(pairs)
    lo, hi = cluster_boot(pairs)
    return {
        'n_pairs': n,
        'n_clusters': len({k for k, _, _ in pairs}),
        'ref': pct(a / n), 'new': pct(b_ / n), 'diff': pct((b_ - a) / n),
        'ci_lo': pct(lo), 'ci_hi': pct(hi),
        'b_ref_only': disc_ref, 'c_new_only': disc_new,
        'p_exact': exact_mcnemar(disc_ref, disc_new),
        'p_clustered': p_cl,
        'cluster': cluster_label,
    }


def holm(pvals):
    """Holm step-down adjusted p values (same order as input)."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        val = min(1.0, (m - rank) * pvals[i])
        running = max(running, val)
        adj[i] = running
    return adj


def cochran_q(blocks):
    """Cochran's Q for k binary treatments over complete blocks (lists of 0/1)."""
    x = np.array(blocks, dtype=float)
    k = x.shape[1]
    col = x.sum(axis=0)
    row = x.sum(axis=1)
    total = x.sum()
    den = k * total - (row ** 2).sum()
    if den == 0:
        return 0.0, 1.0
    q = (k - 1) * (k * (col ** 2).sum() - total ** 2) / den
    return float(q), float(stats.chi2.sf(q, k - 1))


def main():
    main_rows = [r for r in load('episodes_main.jsonl.gz') if r['policy'] == 'B0']
    mit_rows = load('episodes_mitigation.jsonl.gz')
    out = {}

    # ---------------- exposure balance (main, B0) ----------------
    conds = ['C0', 'F1', 'F3', 'F4', 'F5', 'F6']
    cell = {(r['model'], r['task'], r['condition']): r for r in main_rows}
    blocks, uniform, n_blocks = [], 0, 0
    for (m, t) in sorted({(r['model'], r['task']) for r in main_rows}):
        if all((m, t, c) in cell for c in conds):
            v = [int(cell[(m, t, c)]['exposed']) for c in conds]
            blocks.append(v)
            n_blocks += 1
            uniform += int(len(set(v)) == 1)
    q, p_q = cochran_q(blocks)
    exp_rate = {c: pct(np.mean([b[i] for b in blocks])) for i, c in enumerate(conds)}
    out['exposure_balance'] = {
        'n_blocks': n_blocks, 'uniform_blocks': uniform, 'share_uniform': pct(uniform / n_blocks),
        'exposure_rate': exp_rate, 'cochran_q': round(q, 2), 'df': len(conds) - 1, 'p': p_q,
    }

    # ---------------- main paired comparisons (Fk vs C0) ----------------
    def main_pairs(cond, metric, exposed_only=True):
        res = []
        for (m, t, c), r in cell.items():
            if c != cond:
                continue
            r0 = cell.get((m, t, 'C0'))
            if r0 is None:
                continue
            if exposed_only and not (r['exposed'] and r0['exposed']):
                continue
            res.append((t, bool(metric(r0)), bool(metric(r))))
        return res

    metrics = {
        'FS': lambda r: r['FS'],
        'CS': lambda r: r['CS'],
        'MS': lambda r: r['MS'],
        'Dpos': lambda r: r['D'] > 0,
        'EFP': lambda r: r['EFP'],
        'EFF': lambda r: r['EFF'],
        'ge2_writes': lambda r: r['n_matched_writes'] >= 2,
        'RB': lambda r: r['readback_agent'],
    }
    main_tests = {}
    for cond, mets in [('F1', ['FS', 'CS', 'EFP', 'RB']), ('F3', ['FS', 'CS', 'EFP', 'RB']),
                       ('F2', ['FS', 'CS']), ('F4', ['CS', 'FS', 'MS', 'Dpos', 'EFF', 'ge2_writes', 'RB']),
                       ('F5', ['CS', 'FS', 'MS', 'Dpos', 'EFF', 'ge2_writes', 'RB']), ('F6', ['CS', 'FS', 'ge2_writes'])]:
        for met in mets:
            main_tests[f'{cond}:{met}'] = paired_summary(main_pairs(cond, metrics[met]))
    for cond in ['F1', 'F3']:
        for met in ['FS', 'CS']:
            main_tests[f'{cond}:{met}:ITT'] = paired_summary(main_pairs(cond, metrics[met], exposed_only=False))
    out['main_paired'] = main_tests

    # family-clustered variant for the two headline comparisons
    fam_of = {r['task']: r['family'] for r in main_rows}
    fam_tests = {}
    for cond in ['F1', 'F3']:
        pr = [(fam_of[t], a, b) for t, a, b in main_pairs(cond, metrics['FS'])]
        fam_tests[f'{cond}:FS'] = paired_summary(pr, cluster_label='family')
    pr = [(fam_of[t], a, b) for t, a, b in main_pairs('F4', metrics['Dpos'])]
    fam_tests['F4:Dpos'] = paired_summary(pr, cluster_label='family')
    out['main_paired_family_clusters'] = fam_tests

    # ---------------- mitigation: policy vs B0 ----------------
    mcell = {(r['policy'], r['task'], r['condition']): r for r in mit_rows}
    policies = ['B1', 'B2', 'VRV', 'VRV-modeltrigger', 'VRV-samepage', 'VRV-nocbr', 'Oracle']

    def mit_pairs(pol, group, metric):
        cs = {'silent': SILENT, 'lostack': LOSTACK, 'C0': ('C0',), 'F6': ('F6',),
              'F1': ('F1',), 'F3': ('F3',), 'F4': ('F4',), 'F5': ('F5',)}[group]
        res = []
        for (p, t, c), r in mcell.items():
            if p != pol or c not in cs:
                continue
            r0 = mcell.get(('B0', t, c))
            if r0 is None or not (r['exposed'] and r0['exposed']):
                continue
            res.append((t, bool(metric(r0)), bool(metric(r))))
        return res

    harm = lambda r: r['harm']
    mit_tests = {}
    family = []  # the 21 confirmatory tests
    for pol in policies:
        for group, met, fn in [('silent', 'FS', metrics['FS']), ('lostack', 'harm', harm), ('C0', 'CS', metrics['CS'])]:
            s = paired_summary(mit_pairs(pol, group, fn))
            key = f'{pol}:{group}:{met}'
            mit_tests[key] = s
            family.append(key)
        for group, met, fn in [('silent', 'CS', metrics['CS']), ('F6', 'harm', harm), ('lostack', 'Dpos', metrics['Dpos'])]:
            mit_tests[f'{pol}:{group}:{met}'] = paired_summary(mit_pairs(pol, group, fn))
    adj_exact = holm([mit_tests[k]['p_exact'] for k in family])
    adj_cl = holm([mit_tests[k]['p_clustered'] if mit_tests[k]['p_clustered'] is not None else 1.0 for k in family])
    for k, a1, a2 in zip(family, adj_exact, adj_cl):
        mit_tests[k]['p_holm_exact'] = a1
        mit_tests[k]['p_holm_clustered'] = a2
    out['mitigation_paired'] = mit_tests
    out['mitigation_holm_family'] = family

    # ---------------- GEE (model-based check) ----------------
    try:
        import pandas as pd
        import statsmodels.api as sm
        import statsmodels.formula.api as smf
        df = pd.DataFrame([{**{k: r[k] for k in ('model', 'task', 'condition', 'family')},
                            'FS': int(r['FS']), 'CS': int(r['CS'])}
                           for r in main_rows if r['exposed'] and r['condition'] in ('C0', 'F1', 'F3')])
        gee = {}
        for y in ('FS', 'CS'):
            mod = smf.gee(f'{y} ~ C(condition, Treatment("C0")) + C(model)', 'task', df,
                          family=sm.families.Binomial(), cov_struct=sm.cov_struct.Exchangeable())
            fit = mod.fit()
            ci = fit.conf_int()
            for c in ('F1', 'F3'):
                name = f'C(condition, Treatment("C0"))[T.{c}]'
                gee[f'{y}:{c}'] = {'OR': float(math.exp(fit.params[name])),
                                   'lo': float(math.exp(ci.loc[name, 0])), 'hi': float(math.exp(ci.loc[name, 1])),
                                   'p': float(fit.pvalues[name])}
        out['gee_main'] = gee
    except Exception as exc:  # pragma: no cover
        out['gee_main'] = {'error': repr(exc)}

    # ---------------- leave-one-family-out ----------------
    def rate(rows, cond, fn):
        xs = [fn(r) for r in rows if r['condition'] == cond and r['exposed']]
        return (sum(xs) / len(xs)) if xs else None

    fams = sorted({r['family'] for r in main_rows})
    lofo = {}
    for label, cond, fn in [('FS_C0', 'C0', metrics['FS']), ('FS_F1', 'F1', metrics['FS']), ('FS_F3', 'F3', metrics['FS']),
                            ('CS_C0', 'C0', metrics['CS']), ('MS_F4', 'F4', metrics['MS']), ('D_F4', 'F4', metrics['Dpos']),
                            ('EFP_F1', 'F1', metrics['EFP']), ('EFP_F3', 'F3', metrics['EFP']), ('RB_F1F3', None, None)]:
        if label == 'RB_F1F3':
            full = np.mean([r['readback_agent'] for r in main_rows if r['condition'] in SILENT and r['exposed']])
            vals = {f: np.mean([r['readback_agent'] for r in main_rows if r['condition'] in SILENT and r['exposed'] and r['family'] != f]) for f in fams}
        else:
            full = rate(main_rows, cond, fn)
            vals = {f: rate([r for r in main_rows if r['family'] != f], cond, fn) for f in fams}
        lo_f = min(vals, key=lambda f: vals[f])
        hi_f = max(vals, key=lambda f: vals[f])
        lofo[label] = {'full': pct(full), 'min': pct(vals[lo_f]), 'min_drop': lo_f, 'max': pct(vals[hi_f]), 'max_drop': hi_f}
    lofo['FS_C0_without_fam-247'] = pct(rate([r for r in main_rows if r['family'] != 'fam-247'], 'C0', metrics['FS']))
    lofo['D_C0_without_fam-247'] = pct(rate([r for r in main_rows if r['family'] != 'fam-247'], 'C0', metrics['Dpos']))
    out['leave_one_family_out'] = lofo

    # ---------------- step budget ----------------
    budget = {}
    for pol in ['B0'] + policies:
        for grp, cs in [('C0', ('C0',)), ('silent', SILENT), ('lostack', LOSTACK), ('F6', ('F6',))]:
            rows = [r for r in mit_rows if r['policy'] == pol and r['condition'] in cs and r['exposed']]
            none = [r for r in rows if r['claim'] == 'NONE']
            budget[f'{pol}:{grp}'] = {'n': len(rows), 'no_claim': pct(len(none) / len(rows)) if rows else None,
                                      'no_claim_goal_met': pct(sum(r['G'] for r in none) / len(rows)) if rows else None,
                                      'mean_steps': round(float(np.mean([r['steps'] for r in rows])), 1) if rows else None}
    out['step_budget'] = budget

    # ---------------- per-model rates (main, exposed) ----------------
    per_model = {}
    for m in sorted({r['model'] for r in main_rows}):
        rows = [r for r in main_rows if r['model'] == m]
        per_model[m] = {
            'CS_C0': pct(rate(rows, 'C0', metrics['CS'])),
            'FS_F1': pct(rate(rows, 'F1', metrics['FS'])), 'FS_F3': pct(rate(rows, 'F3', metrics['FS'])),
            'FS_F2': pct(rate(rows, 'F2', metrics['FS'])),
            'RB_silent': pct(np.mean([r['readback_agent'] for r in rows if r['condition'] in SILENT and r['exposed']])),
            'MS_F4': pct(rate(rows, 'F4', metrics['MS'])), 'D_F4': pct(rate(rows, 'F4', metrics['Dpos'])),
            'RB_F4': pct(rate(rows, 'F4', metrics['RB'])),
            'EFP_silent': pct(np.mean([r['EFP'] for r in rows if r['condition'] in SILENT and r['exposed']])),
            'n_exposed': {c: sum(1 for r in rows if r['condition'] == c and r['exposed']) for c in ['C0', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6']},
        }
    out['per_model'] = per_model

    # ---------------- start-state contamination (pre-reset forum window) ----------------
    flags_path = FLAGS
    if flags_path.exists():
        import csv
        flags = {r['episode']: r for r in csv.DictReader(flags_path.open())}
        inw = lambda ep: flags[ep]['in_window'] in ('1', 'True', 'true')
        cont = {'n_in_window_main': sum(inw(r['episode']) for r in main_rows),
                'n_in_window_mitigation_B0': sum(inw(r['episode']) for r in mit_rows if r['policy'] == 'B0'),
                'n_in_window_mitigation_other': sum(inw(r['episode']) for r in mit_rows if r['policy'] != 'B0'),
                'by_condition_main': {c: sum(inw(r['episode']) for r in main_rows if r['condition'] == c)
                                      for c in ['C0', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6']},
                'by_family_main': {f: sum(inw(r['episode']) for r in main_rows if r['family'] == f)
                                   for f in sorted({r['family'] for r in main_rows})},
                'exposed_share_in_window': pct(np.mean([r['exposed'] for r in main_rows if inw(r['episode'])])),
                'exposed_share_same_families_outside': pct(np.mean([r['exposed'] for r in main_rows if not inw(r['episode']) and r['family'] in {'fam-4', 'fam-22', 'fam-13', 'fam-12'}])),
                }
        # mixed pairs: exactly one member of a (model, task) C0/Fk pair in the window
        mixed = 0
        tot = 0
        for (m, t, c), r in cell.items():
            if c == 'C0' or (m, t, 'C0') not in cell:
                continue
            tot += 1
            mixed += int(inw(r['episode']) != inw(cell[(m, t, 'C0')]['episode']))
        cont['mixed_pairs'] = mixed
        cont['all_pairs'] = tot
        # headline exposed rates without in-window episodes
        clean_rows = [r for r in main_rows if not inw(r['episode'])]
        head = {}
        for c in ['C0', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6']:
            full = [r for r in main_rows if r['condition'] == c and r['exposed']]
            cl = [r for r in clean_rows if r['condition'] == c and r['exposed']]
            head[c] = {'n_full': len(full), 'n_clean': len(cl)}
            for k, fn in [('CS', metrics['CS']), ('FS', metrics['FS']), ('MS', metrics['MS']), ('D', metrics['Dpos']),
                          ('RB', metrics['RB']), ('EFP', metrics['EFP']), ('EFF', metrics['EFF'])]:
                head[c][k] = [pct(np.mean([bool(fn(r)) for r in full])), pct(np.mean([bool(fn(r)) for r in cl]))]
        cont['main_rates_full_vs_clean'] = head
        clean_tests = {}
        for key, cond, fn in [('F1:FS', 'F1', metrics['FS']), ('F3:FS', 'F3', metrics['FS']), ('F2:FS', 'F2', metrics['FS']),
                              ('F4:Dpos', 'F4', metrics['Dpos']), ('F4:MS', 'F4', metrics['MS']), ('F5:MS', 'F5', metrics['MS']),
                              ('F1:EFP', 'F1', metrics['EFP']), ('F4:EFF', 'F4', metrics['EFF'])]:
            pr = []
            for (m, t, c), r in cell.items():
                if c != cond or (m, t, 'C0') not in cell:
                    continue
                r0 = cell[(m, t, 'C0')]
                if not (r['exposed'] and r0['exposed']) or inw(r['episode']) or inw(r0['episode']):
                    continue
                pr.append((t, bool(fn(r0)), bool(fn(r))))
            clean_tests[key] = paired_summary(pr)
        cont['main_paired_clean'] = clean_tests
        mit_clean = {}
        for pol in policies:
            for group, met, fn in [('silent', 'FS', metrics['FS']), ('lostack', 'harm', harm), ('C0', 'CS', metrics['CS'])]:
                pr = [(k, a, b) for k, a, b in mit_pairs(pol, group, fn)]
                # drop pairs whose B0 episode started in the window
                keep = []
                cs = {'silent': SILENT, 'lostack': LOSTACK, 'C0': ('C0',)}[group]
                for (p, t, c), r in mcell.items():
                    if p != pol or c not in cs:
                        continue
                    r0 = mcell.get(('B0', t, c))
                    if r0 is None or not (r['exposed'] and r0['exposed']) or inw(r0['episode']):
                        continue
                    keep.append((t, bool(fn(r0)), bool(fn(r))))
                mit_clean[f'{pol}:{group}:{met}'] = paired_summary(keep)
        cont['mitigation_paired_clean'] = mit_clean
        # determinism of the pre-write prefix: equal prefix length and equal exposure across conditions
        groups = defaultdict(list)
        for r in main_rows:
            if r['condition'] in conds:
                groups[(r['model'], r['task'])].append((int(flags[r['episode']]['prefix_len'] or 0), r['exposed'], flags[r['episode']]['prefix_sha1']))
        full_groups = [g for g in groups.values() if len(g) == len(conds)]
        cont['prefix_groups'] = len(full_groups)
        cont['prefix_identical_literal'] = sum(len({x[2] for x in g}) == 1 for g in full_groups)
        cont['prefix_same_length_all_exposed'] = sum(all(x[1] for x in g) and len({x[0] for x in g}) == 1 for g in full_groups)
        cont['all_exposed_groups'] = sum(all(x[1] for x in g) for g in full_groups)
        rb = [r for r in main_rows if r['site'] == 'reddit']
        cont['reddit_episodes_main'] = len(rb)
        cont['delete_requests'] = sum(int(flags[e]['n_delete_req'] or 0) for e in flags)
        cont['mentions_delete'] = sum(int(flags[e]['mentions_delete'] or 0) > 0 for e in flags)
        out['contamination'] = cont

    (OUT / 'robustness.json').write_text(json.dumps(out, indent=1, default=float))

    # ---------------- markdown summary ----------------
    L = ['# Robustness analyses (generated by scripts/robustness.py)', '']
    e = out['exposure_balance']
    L.append(f"Exposure balance (main B0, {e['n_blocks']} complete model x task blocks over C0,F1,F3,F4,F5,F6): "
             f"uniform in {e['uniform_blocks']} ({e['share_uniform']}%); rates {e['exposure_rate']}; Cochran Q={e['cochran_q']}, df={e['df']}, p={e['p']:.3g}")
    L.append('')
    L.append('## Main paired (exposed in both; task clusters)')
    for k, s in main_tests.items():
        L.append(f"{k}: n={s['n_pairs']} ({s['n_clusters']} tasks) {s['ref']} -> {s['new']} diff {s['diff']} [{s['ci_lo']}, {s['ci_hi']}] "
                 f"b/c={s['b_ref_only']}/{s['c_new_only']} p_exact={s['p_exact']:.3g} p_clustered={s['p_clustered'] if s['p_clustered'] is None else format(s['p_clustered'], '.3g')}")
    L.append('')
    L.append('## Main paired, family clusters')
    for k, s in fam_tests.items():
        L.append(f"{k}: diff {s['diff']} [{s['ci_lo']}, {s['ci_hi']}] ({s['n_clusters']} families) p_clustered={s['p_clustered']:.3g}")
    L.append('')
    L.append('## Mitigation vs B0 (DeepSeek; exposed in both; task clusters); Holm over the 21 starred tests')
    for k, s in mit_tests.items():
        star = '*' if k in family else ' '
        extra = f" holm_exact={s['p_holm_exact']:.3g} holm_cl={s['p_holm_clustered']:.3g}" if k in family else ''
        pcl = 'NA' if s['p_clustered'] is None else format(s['p_clustered'], '.3g')
        L.append(f"{star}{k}: n={s['n_pairs']} {s['ref']} -> {s['new']} diff {s['diff']} [{s['ci_lo']}, {s['ci_hi']}] b/c={s['b_ref_only']}/{s['c_new_only']} p={s['p_exact']:.3g} p_cl={pcl}{extra}")
    L.append('')
    L.append(f"GEE (FS/CS ~ condition + model, task clusters, exchangeable): {json.dumps(out['gee_main'])}")
    L.append('')
    L.append(f"Leave-one-family-out: {json.dumps(lofo)}")
    L.append('')
    L.append('## Step budget (exposed; % without a claim, % without a claim although G holds, mean steps)')
    for k, s in budget.items():
        L.append(f"{k}: n={s['n']} no_claim={s['no_claim']} no_claim_G={s['no_claim_goal_met']} steps={s['mean_steps']}")
    L.append('')
    L.append('## Per model')
    for m, s in per_model.items():
        L.append(f"{m}: {json.dumps(s)}")
    if 'contamination' in out:
        c = out['contamination']
        L.append('')
        L.append('## Start-state contamination (forum episodes started before the global reset)')
        for k in ['n_in_window_main', 'n_in_window_mitigation_B0', 'n_in_window_mitigation_other', 'by_condition_main',
                  'by_family_main', 'exposed_share_in_window', 'exposed_share_same_families_outside', 'mixed_pairs', 'all_pairs',
                  'prefix_groups', 'prefix_identical_literal', 'all_exposed_groups', 'prefix_same_length_all_exposed',
                  'delete_requests', 'mentions_delete']:
            L.append(f"{k}: {c[k]}")
        L.append('Exposed rates, full vs without in-window episodes [full, clean]:')
        for cond, s in c['main_rates_full_vs_clean'].items():
            L.append(f"  {cond}: {json.dumps(s)}")
        L.append('Main paired tests without in-window pairs:')
        for k, s in c['main_paired_clean'].items():
            L.append(f"  {k}: n={s['n_pairs']} {s['ref']} -> {s['new']} diff {s['diff']} [{s['ci_lo']}, {s['ci_hi']}] p={s['p_exact']:.3g} p_cl={s['p_clustered']}")
        L.append('Mitigation tests without in-window B0 pairs:')
        for k, s in c['mitigation_paired_clean'].items():
            L.append(f"  {k}: n={s['n_pairs']} {s['ref']} -> {s['new']} diff {s['diff']} [{s['ci_lo']}, {s['ci_hi']}] p={s['p_exact']:.3g}")
    (OUT / 'robustness.md').write_text('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    sys.exit(main())
