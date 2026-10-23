"""Round-3 analyses: VRV with a second model, rollback-delay sweep, and repeat runs.

Inputs (per-episode tables in the released schema):
  data/episodes_r3.jsonl.gz          round-3 episodes (WPL_R3 overrides; comma-separated list allowed)
  data/episodes_main.jsonl.gz        main study (seed 0), for repeat-run comparisons
  data/episodes_mitigation.jsonl.gz  mitigation study (DeepSeek), for the 8 s VRV reference
Output: analysis/review/round3/round3_raw.json (unrounded, rates in %) and a markdown summary on stdout.

Conventions follow the paper: rates over exposed episodes; comparisons paired by task (and
condition) over episodes exposed in both arms; exact McNemar tests, task-clustered bootstrap
intervals and the cluster-adjusted McNemar test (robustness.py).  'Harm' is false success,
missed success or any excess effect.
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
import robustness as R  # noqa: E402

DATA = Path(os.environ.get('WPL_DATA', HERE.parents[1] / 'data'))
R3 = [Path(x) for x in os.environ.get('WPL_R3', str(DATA / 'episodes_r3.jsonl.gz')).split(',')]
OUT = Path(os.environ.get('WPL_OUT', HERE.parents[1] / 'analysis/review/round3'))
MIMO, DS = 'mimo-v2.6-flash', 'deepseek-v4.1-flash'
SILENT, LOST = ('F1', 'F3'), ('F4',)
F3_DELAY = {'F3s2': 2, 'F3': 8, 'F3s30': 30}


def load(path):
    with gzip.open(path, 'rt') as fh:
        return [json.loads(line) for line in fh]


METRIC = {
    'CS': lambda r: bool(r['CS']), 'FS': lambda r: bool(r['FS']), 'MS': lambda r: bool(r['MS']),
    'D': lambda r: bool(r['D']), 'RB': lambda r: bool(r['readback_agent']), 'harm': lambda r: bool(r['harm']),
    'noclaim': lambda r: r['claim'] != 'DONE', 'EFP': lambda r: bool(r['EFP']),
}


def pct(k, n):
    return None if n == 0 else 100.0 * k / n


def rates(rows):
    ex = [r for r in rows if r['exposed']]
    out = {'n': len(rows), 'n_exposed': len(ex), 'exposure': pct(len(ex), len(rows))}
    for m, fn in METRIC.items():
        out[m] = pct(sum(fn(r) for r in ex), len(ex))
        out['n_' + m] = sum(fn(r) for r in ex)
    out['mean_steps'] = float(np.mean([r['steps'] for r in rows])) if rows else None
    out['usd'] = float(sum(r['cost'] for r in rows))
    return out


def tokens(r):
    t = r.get('tokens') or {}
    return (t.get('input') or 0) + (t.get('output') or 0)


def paired(ref, new, conds, metric, exposed_only=True):
    """ref/new: dict (task, condition) -> row. Returns paired summary or None."""
    pairs = []
    for key, b in new.items():
        if key[1] not in conds or key not in ref:
            continue
        a = ref[key]
        if exposed_only and not (a['exposed'] and b['exposed']):
            continue
        pairs.append((key[0], METRIC[metric](a), METRIC[metric](b)))
    if not pairs:
        return None
    s = R.paired_summary(pairs)
    return s


def index(rows, **f):
    return {(r['task'], r['condition']): r for r in rows if all(r[k] == v for k, v in f.items())}


def agreement(ref, new):
    """Test-retest agreement of exposure and, on cells exposed twice, of outcomes."""
    keys = sorted(set(ref) & set(new))
    out = {'n_cells': len(keys)}
    ex_a = [bool(ref[k]['exposed']) for k in keys]
    ex_b = [bool(new[k]['exposed']) for k in keys]
    out['exposure_agree'] = pct(sum(a == b for a, b in zip(ex_a, ex_b)), len(keys))
    both = [k for k in keys if ref[k]['exposed'] and new[k]['exposed']]
    out['n_both_exposed'] = len(both)

    def outcome(r):
        return 'CS' if r['CS'] else 'FS' if r['FS'] else 'MS' if r['MS'] else 'DUP' if (r['claim'] == 'DONE' and r['G']) else 'CF'
    out['outcome_agree'] = pct(sum(outcome(ref[k]) == outcome(new[k]) for k in both), len(both))
    for m in ('CS', 'FS'):
        a = [METRIC[m](ref[k]) for k in both]
        b = [METRIC[m](new[k]) for k in both]
        po = pct(sum(x == y for x, y in zip(a, b)), len(both))
        pa, pb = np.mean(a) if a else 0, np.mean(b) if b else 0
        pe = pa * pb + (1 - pa) * (1 - pb)
        out[f'{m}_agree'] = po
        out[f'{m}_kappa'] = None if not both or pe == 1 else float((po / 100 - pe) / (1 - pe))
    return out


def main():
    r3 = [r for p in R3 for r in load(p)]
    keys = [(r['model'], r['policy'], r['task'], r['condition'], r['seed']) for r in r3]
    assert len(keys) == len(set(keys)), 'duplicate cells in round-3 exports'
    main_rows = [r for r in load(DATA / 'episodes_main.jsonl.gz') if r['policy'] == 'B0']
    # Optional: claim-before-rollback flags for main-study episodes (not in the released schema).
    timing = HERE.parents[1] / 'analysis/review/round3/main_f3_claim_timing.json'
    if timing.exists():
        flags = json.loads(timing.read_text())
        for r in main_rows:
            if r['episode'] in flags:
                r['claim_before_rollback'] = flags[r['episode']]
    mit_rows = load(DATA / 'episodes_mitigation.jsonl.gz')
    res = {'n_episodes': len(r3), 'cells': {}}

    # ---- summaries by model, policy, condition ----
    groups = defaultdict(list)
    for r in r3:
        groups[(r['model'], r['policy'], r['condition'])].append(r)
    for (m, p, c), rows in sorted(groups.items()):
        res['cells'][f'{m}|{p}|{c}'] = rates(rows)
    res['usd_total'] = float(sum(r['cost'] for r in r3))

    # ---- A. VRV with a second model (MiMo): VRV vs fresh B0 ----
    mb0, mvrv = index(r3, model=MIMO, policy='B0'), index(r3, model=MIMO, policy='VRV')
    mimo = {}
    if mb0 and mvrv:
        for name, conds, met in [('silent:FS', SILENT, 'FS'), ('silent:CS', SILENT, 'CS'), ('lostack:harm', LOST, 'harm'),
                                 ('lostack:D', LOST, 'D'), ('C0:CS', ('C0',), 'CS'), ('C0:MS', ('C0',), 'MS'),
                                 ('F1:FS', ('F1',), 'FS'), ('F3:FS', ('F3',), 'FS')]:
            mimo[name] = paired(mb0, mvrv, conds, met)
            mimo[name + ':ITT'] = paired(mb0, mvrv, conds, met, exposed_only=False)
        # exposure under VRV vs B0
        ex_pairs = [(k[0], bool(mb0[k]['exposed']), bool(v['exposed'])) for k, v in mvrv.items() if k in mb0]
        mimo['exposure'] = R.paired_summary(ex_pairs) if ex_pairs else None
        # token and step ratios on paired cells
        common = [k for k in mvrv if k in mb0]
        tb = np.mean([tokens(mb0[k]) for k in common]) if common else None
        tv = np.mean([tokens(mvrv[k]) for k in common]) if common else None
        mimo['token_ratio'] = float(tv / tb) if tb else None
        mimo['steps_B0'] = float(np.mean([mb0[k]['steps'] for k in common])) if common else None
        mimo['steps_VRV'] = float(np.mean([mvrv[k]['steps'] for k in common])) if common else None
        # VRV vs the main-study MiMo B0 (seed 0), as a secondary reference
        mb0_main = index(main_rows, model=MIMO)
        mimo['silent:FS:vs_main_B0'] = paired(mb0_main, mvrv, SILENT, 'FS')
        mimo['lostack:harm:vs_main_B0'] = paired(mb0_main, mvrv, LOST, 'harm')
    res['mimo_vrv'] = mimo

    # ---- B. repeat runs: fresh B0 vs main-study B0 (same model, task, condition) ----
    rep = {}
    for model in (MIMO, DS):
        fresh = {k: v for k, v in index(r3, model=model, policy='B0').items() if k[1] in ('C0', 'F1', 'F3', 'F4')}
        old = index(main_rows, model=model)
        if not fresh:
            continue
        d = {'agreement': agreement(old, fresh)}
        for name, conds, met in [('C0:CS', ('C0',), 'CS'), ('C0:FS', ('C0',), 'FS'), ('silent:FS', SILENT, 'FS'),
                                 ('silent:RB', SILENT, 'RB'), ('F4:MS', ('F4',), 'MS'), ('F4:D', ('F4',), 'D'), ('F4:RB', ('F4',), 'RB')]:
            d[name] = paired(old, fresh, conds, met)
        d['old'] = {c: rates([r for r in main_rows if r['model'] == model and r['condition'] == c and r['task'] in {k[0] for k in fresh}]) for c in ('C0', 'F1', 'F3', 'F4')}
        d['new'] = {c: rates([v for k, v in fresh.items() if k[1] == c]) for c in ('C0', 'F1', 'F3', 'F4')}
        # effect of the faults within the fresh run (does the headline replicate?)
        within = {}
        for c in ('F1', 'F3'):
            ref = {(k[0], c): v for k, v in fresh.items() if k[1] == 'C0'}
            new = {k: v for k, v in fresh.items() if k[1] == c}
            within[f'{c}:FS'] = paired(ref, new, (c,), 'FS')
        d['within_fresh'] = within
        rep[model] = d
    res['repeat'] = rep

    # ---- C. rollback-delay sweep (DeepSeek) ----
    delay = {}
    for pol in ('B0', 'VRV'):
        cells = {}
        for c, sec in F3_DELAY.items():
            rows = [r for r in r3 if r['model'] == DS and r['policy'] == pol and r['condition'] == c]
            if rows:
                s = rates(rows)
                ex = [r for r in rows if r['exposed'] and r['claim'] == 'DONE']
                if ex and 'claim_before_rollback' in ex[0]:
                    s['claims_before_rollback'] = pct(sum(bool(r['claim_before_rollback']) for r in ex), len(ex))
                cells[f'{sec}s'] = s
        # 8 s references.  B0: the third round repeats F3 only on part of the tasks, so the
        # 8 s column uses the main study (all 64 tasks; the partial repeat is kept as 8s_repeat).
        # VRV: the third round reran F3 on all 59 tasks; the mitigation study is kept for reference.
        if pol == 'B0':
            if '8s' in cells:
                cells['8s_repeat'] = cells.pop('8s')
            main_f3 = [r for r in main_rows if r['model'] == DS and r['condition'] == 'F3']
            s8 = rates(main_f3)
            ex = [r for r in main_f3 if r['exposed'] and r['claim'] == 'DONE']
            if ex and 'claim_before_rollback' in ex[0]:
                s8['claims_before_rollback'] = pct(sum(bool(r['claim_before_rollback']) for r in ex), len(ex))
            cells['8s'] = s8
        else:
            cells['8s_mitigation'] = rates([r for r in mit_rows if r['policy'] == 'VRV' and r['condition'] == 'F3'])
        idx = {c: index([r for r in r3 if r['model'] == DS and r['policy'] == pol and r['condition'] == c]) for c in F3_DELAY}
        if pol == 'B0':
            idx['F3'] = index([r for r in main_rows if r['model'] == DS and r['condition'] == 'F3'])
        tests = {}
        for a, b in [('F3s2', 'F3s30'), ('F3s2', 'F3'), ('F3', 'F3s30')]:
            ra = {(k[0], 'X'): v for k, v in idx[a].items()}
            rb = {(k[0], 'X'): v for k, v in idx[b].items()}
            for met in ('FS', 'CS'):
                tests[f'{a}->{b}:{met}'] = paired(ra, rb, ('X',), met)
        cells['tests'] = tests
        delay[pol] = cells
    res['delay'] = delay

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'round3_raw.json').write_text(json.dumps(res, indent=1, sort_keys=True, default=float))

    f = lambda x: '-' if x is None else f'{x:.1f}'
    print(f"# Round 3: {res['n_episodes']} episodes, ${res['usd_total']:.2f}\n")
    print('| model | policy | cond | n | exp | CS | FS | MS | D | RB | harm | no claim | steps |')
    print('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for k, s in res['cells'].items():
        m, p, c = k.split('|')
        print(f"| {m} | {p} | {c} | {s['n']} | {s['n_exposed']} | " + ' | '.join(f(s[x]) for x in ('CS', 'FS', 'MS', 'D', 'RB', 'harm', 'noclaim')) + f" | {f(s['mean_steps'])} |")

    def show(name, s):
        if s:
            print(f"  {name}: n={s['n_pairs']} {f(s['ref'])} -> {f(s['new'])} diff {f(s['diff'])} [{f(s['ci_lo'])}, {f(s['ci_hi'])}] "
                  f"b/c={s['b_ref_only']}/{s['c_new_only']} p={s['p_exact']:.3g} p_cl={'NA' if s['p_clustered'] is None else format(s['p_clustered'], '.3g')}")
    print('\n## A. MiMo: VRV vs fresh B0 (exposed in both unless ITT)')
    for k, s in mimo.items():
        if isinstance(s, dict) and 'n_pairs' in s:
            show(k, s)
    for k in ('token_ratio', 'steps_B0', 'steps_VRV'):
        if k in mimo:
            print(f"  {k}: {f(mimo[k]) if k != 'token_ratio' else format(mimo[k], '.2f')}")
    print('\n## B. Repeat runs: fresh B0 vs main-study B0')
    for model, d in rep.items():
        print(f"### {model}: {json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in d['agreement'].items()})}")
        for k, s in d.items():
            if isinstance(s, dict) and 'n_pairs' in s:
                show(k, s)
        for k, s in d['within_fresh'].items():
            show('within fresh ' + k, s)
    print('\n## C. Rollback delay (DeepSeek)')
    for pol, cells in delay.items():
        for k, s in cells.items():
            if k == 'tests':
                for t, v in s.items():
                    show(f'{pol} {t}', v)
            else:
                extra = f" claims_before_rollback={f(s.get('claims_before_rollback'))}" if 'claims_before_rollback' in s else ''
                print(f"  {pol} {k}: n={s['n']} exp={s['n_exposed']} CS={f(s['CS'])} FS={f(s['FS'])} RB={f(s['RB'])} noclaim={f(s['noclaim'])}{extra}")


if __name__ == '__main__':
    main()
