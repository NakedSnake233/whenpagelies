"""Intention-to-treat check for the mitigation study (review round 1).

Policies change the agent's prompt from the first step, so whether the agent reaches the
faulted write (exposure) can depend on the policy.  This script (1) compares exposure of
each policy with B0 on the same (task, condition) and (2) repeats the 21 policy-vs-B0
comparisons over all episodes, exposed or not.  Output: data/mitigation_itt.json.
"""
import gzip
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import robustness as R  # noqa: E402

R.pct = lambda x: None if x is None else 100.0 * x  # keep full precision
DATA = Path(os.environ.get('WPL_DATA', ROOT / 'data'))
OUT = Path(os.environ.get('WPL_OUT', ROOT / 'analysis' / 'review'))
POLICIES = ['B1', 'B2', 'VRV', 'VRV-modeltrigger', 'VRV-samepage', 'VRV-nocbr', 'Oracle']
GROUPS = {'silent': ('F1', 'F3'), 'lostack': ('F4', 'F5'), 'C0': ('C0',)}
METRIC = {'silent': ('FS', lambda r: r['FS']), 'lostack': ('harm', lambda r: r['harm']), 'C0': ('CS', lambda r: r['CS'])}


def main():
    rows = [json.loads(l) for l in gzip.open(DATA / 'episodes_mitigation.jsonl.gz', 'rt')]
    cell = {(r['policy'], r['task'], r['condition']): r for r in rows}
    out = {'exposure': {}, 'itt': {}}
    for pol in ['B0'] + POLICIES:
        rs = [r for r in rows if r['policy'] == pol]
        out['exposure'][pol] = {'n': len(rs), 'exposed': R.pct(sum(r['exposed'] for r in rs) / len(rs))}
    for pol in POLICIES:
        pairs = [(t, bool(cell[('B0', t, c)]['exposed']), bool(r['exposed']))
                 for (p, t, c), r in cell.items() if p == pol and ('B0', t, c) in cell]
        out['exposure'][pol]['paired_vs_B0'] = R.paired_summary(pairs)
    keys = []
    for pol in POLICIES:
        for g, cs in GROUPS.items():
            name, fn = METRIC[g]
            pairs = [(t, bool(fn(cell[('B0', t, c)])), bool(fn(r)))
                     for (p, t, c), r in cell.items() if p == pol and c in cs and ('B0', t, c) in cell]
            key = f'{pol}:{g}:{name}'
            out['itt'][key] = R.paired_summary(pairs)
            keys.append(key)
    adj = R.holm([out['itt'][k]['p_exact'] for k in keys])
    for k, a in zip(keys, adj):
        out['itt'][k]['p_holm_exact'] = a
    (OUT / 'mitigation_itt.json').write_text(json.dumps(out, indent=1, default=float))
    for pol, s in out['exposure'].items():
        extra = '' if pol == 'B0' else f" vs B0: {s['paired_vs_B0']['ref']:.1f} -> {s['paired_vs_B0']['new']:.1f}, b/c={s['paired_vs_B0']['b_ref_only']}/{s['paired_vs_B0']['c_new_only']}, p={s['paired_vs_B0']['p_exact']:.3g}"
        print(f"exposure {pol}: {s['exposed']:.1f}%{extra}")
    for k in keys:
        s = out['itt'][k]
        print(f"ITT {k}: n={s['n_pairs']} {s['ref']:.1f} -> {s['new']:.1f} diff {s['diff']:.1f} [{s['ci_lo']:.1f}, {s['ci_hi']:.1f}] p={s['p_exact']:.3g} holm={s['p_holm_exact']:.3g}")


if __name__ == '__main__':
    main()
