"""Repeat of DeepSeek-V4.1-Flash's main-study B0 cells (C0, F1, F3, F4 on all 64 tasks).

The repeat ran in two parts: 49 cells through the original gateway (round 3, data/episodes_r3.jsonl.gz,
sweep r3-repeat) and the remaining cells through a second provider after the first gateway's quota
ran out (data/episodes_r3b.jsonl.gz; rows carry provider='lmuai').  Each part is compared with the
main study on the same (task, condition) cells, and both parts are pooled.

Inputs: WPL_R3 (round-3 export), WPL_R3B (repeat export via the second provider), WPL_DATA (main study),
WPL_OUT (output directory).  Output: repeat_ds_raw.json and a markdown summary on stdout.
"""
import gzip
import json
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import robustness as R  # noqa: E402
from round3 import METRIC, agreement, index, paired, pct, rates  # noqa: E402

DATA = Path(os.environ.get('WPL_DATA', HERE.parents[1] / 'data'))
R3 = Path(os.environ.get('WPL_R3', str(DATA / 'episodes_r3.jsonl.gz')))
R3B = [Path(x) for x in os.environ.get('WPL_R3B', str(DATA / 'episodes_r3b.jsonl.gz')).split(',')]
OUT = Path(os.environ.get('WPL_OUT', HERE.parents[1] / 'analysis/review/round3b'))
DS = 'deepseek-v4.1-flash'
CONDS = ('C0', 'F1', 'F3', 'F4')


def load(path):
    with gzip.open(path, 'rt') as fh:
        return [json.loads(line) for line in fh]


def part(rows):
    return {k: v for k, v in index(rows, model=DS, policy='B0').items() if k[1] in CONDS}


def compare(old, fresh):
    d = {'agreement': agreement(old, fresh), 'n_cells': len(fresh)}
    for name, conds, met in [('C0:CS', ('C0',), 'CS'), ('C0:FS', ('C0',), 'FS'), ('silent:FS', ('F1', 'F3'), 'FS'),
                             ('silent:CS', ('F1', 'F3'), 'CS'), ('silent:RB', ('F1', 'F3'), 'RB'),
                             ('F4:CS', ('F4',), 'CS'), ('F4:MS', ('F4',), 'MS'), ('F4:D', ('F4',), 'D'),
                             ('F4:RB', ('F4',), 'RB'), ('exposure', CONDS, None)]:
        if met is None:
            pairs = [(k[0], bool(old[k]['exposed']), bool(v['exposed'])) for k, v in fresh.items() if k in old]
            d[name] = R.paired_summary(pairs) if pairs else None
        else:
            d[name] = paired(old, fresh, conds, met)
    d['old'] = {c: rates([old[k] for k in fresh if k in old and k[1] == c]) for c in CONDS}
    d['new'] = {c: rates([v for k, v in fresh.items() if k[1] == c]) for c in CONDS}
    within = {}
    for c in ('F1', 'F3'):
        ref = {(k[0], c): v for k, v in fresh.items() if k[1] == 'C0'}
        new = {k: v for k, v in fresh.items() if k[1] == c}
        within[f'{c}:FS'] = paired(ref, new, (c,), 'FS')
        within[f'{c}:CS'] = paired(ref, new, (c,), 'CS')
    d['within_fresh'] = within
    d['parse_error_steps'] = float(np.mean([v.get('n_parse_error_steps') or 0 for v in fresh.values()])) if fresh else None
    d['steps'] = float(np.mean([v['steps'] for v in fresh.values()])) if fresh else None
    d['usd'] = float(sum(v['cost'] for v in fresh.values()))
    return d


def main():
    main_rows = [r for r in load(DATA / 'episodes_main.jsonl.gz') if r['policy'] == 'B0']
    old = index(main_rows, model=DS)
    a = part([r for r in load(R3) if r.get('provider', 'opencode') == 'opencode'])
    b_rows = [r for p in R3B for r in load(p)]
    b = part(b_rows)
    assert not (set(a) & set(b)), 'a cell was repeated through both providers'
    providers = sorted({r.get('provider', 'opencode') for r in b_rows})
    res = {'providers_part_b': providers, 'n_a': len(a), 'n_b': len(b),
           'part_a_opencode': compare(old, a), 'part_b': compare(old, b), 'pooled': compare(old, {**a, **b})}
    (OUT / 'repeat_ds_raw.json').write_text(json.dumps(res, indent=1, sort_keys=True))

    f = lambda x: '-' if x is None else f'{x:.1f}'
    print(f"# DeepSeek-V4.1-Flash B0 repeat: {len(a)} cells via the first gateway, {len(b)} via {providers}\n")
    for name in ('part_a_opencode', 'part_b', 'pooled'):
        d = res[name]
        print(f"## {name}: {json.dumps(d['agreement'])}")
        print(f"  steps={f(d['steps'])} parse_err/ep={f(d['parse_error_steps'])} usd={d['usd']:.2f}")
        for k in ('exposure', 'C0:CS', 'C0:FS', 'silent:FS', 'silent:CS', 'silent:RB', 'F4:CS', 'F4:MS', 'F4:D', 'F4:RB'):
            v = d[k]
            if v:
                print(f"  {k}: n={v['n_pairs']} {f(v['ref'])} -> {f(v['new'])} diff {f(v['diff'])} "
                      f"[{f(v['ci_lo'])}, {f(v['ci_hi'])}] b/c={v['b_ref_only']}/{v['c_new_only']} "
                      f"p={v['p_exact']:.3g} p_cl={v['p_clustered'] if v['p_clustered'] is None else format(v['p_clustered'], '.3g')}")
        for k, v in d['within_fresh'].items():
            if v:
                print(f"  within {k}: n={v['n_pairs']} {f(v['ref'])} -> {f(v['new'])} p={v['p_exact']:.3g}")
        print('  new rates:', {c: (s['n'], s['n_exposed'], f(s['CS']), f(s['FS']), f(s['MS']), f(s['D']), f(s['RB']))
                               for c, s in d['new'].items()})


if __name__ == '__main__':
    main()
