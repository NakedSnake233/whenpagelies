"""Summarise LLM-judge verdicts against backend truth and the WebArena-Verified evaluator.

Inputs: data/episodes_main.jsonl.gz and data/judge/<variant>__<model>.jsonl
(one line per judged episode, written by scripts/llm_judge.py).
Output: data/judge_summary.json and a printed table.
"""
import gzip
import json
from pathlib import Path

import os

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get('WPL_DATA', ROOT / 'data'))
JUDGE = Path(os.environ.get('WPL_JUDGE', ROOT / 'analysis' / 'review' / 'judge'))
OUT = Path(os.environ.get('WPL_OUT', ROOT / 'analysis' / 'review'))
CONDS = ['C0', 'F1', 'F3', 'F4', 'F5']


def kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def main():
    truth = {}
    with gzip.open(DATA / 'episodes_main.jsonl.gz', 'rt') as fh:
        for line in fh:
            r = json.loads(line)
            if r['policy'] == 'B0' and r['exposed'] and r['condition'] in CONDS:
                truth[r['episode']] = r
    out = {'n_target': len(truth), 'judges': {}}
    files = sorted(JUDGE.glob('*__*.jsonl'))
    for f in files:
        variant, model = f.stem.split('__')
        verdicts = {}
        errors = 0
        unparsed = 0
        cost = 0.0
        for line in f.read_text().splitlines():
            rec = json.loads(line)
            if rec.get('error'):
                errors += 1
                continue
            cost += rec.get('cost') or 0
            if rec.get('verdict') is None:
                unparsed += 1
                continue
            verdicts[rec['episode']] = bool(rec['verdict'])
        res = {'n_verdicts': len(verdicts), 'errors': errors, 'unparsed': unparsed, 'cost_usd': round(cost, 4), 'by_condition': {}}
        for c in CONDS + ['silent', 'lostack', 'all']:
            cs = {'silent': ('F1', 'F3'), 'lostack': ('F4', 'F5'), 'all': tuple(CONDS)}.get(c, (c,))
            eps = [e for e, r in truth.items() if r['condition'] in cs and e in verdicts]
            if not eps:
                continue
            right = {e: bool(truth[e]['G']) and truth[e]['D'] == 0 for e in eps}
            n = len(eps)
            jp = sum(verdicts[e] for e in eps)
            jfp = sum(verdicts[e] and not right[e] for e in eps)
            jff = sum((not verdicts[e]) and right[e] for e in eps)
            efp = sum(bool(truth[e]['EFP']) for e in eps)
            eff = sum(bool(truth[e]['EFF']) for e in eps)
            agree_eval = sum(verdicts[e] == bool(truth[e]['eval_pass']) for e in eps)
            res['by_condition'][c] = {
                'n': n, 'state_right': round(100 * sum(right.values()) / n, 1),
                'judge_pass': round(100 * jp / n, 1), 'JFP': round(100 * jfp / n, 1), 'JFF': round(100 * jff / n, 1),
                'eval_pass': round(100 * sum(bool(truth[e]['eval_pass']) for e in eps) / n, 1),
                'EFP': round(100 * efp / n, 1), 'EFF': round(100 * eff / n, 1),
                'judge_acc': round(100 * (n - jfp - jff) / n, 1),
                'eval_acc': round(100 * (n - efp - eff) / n, 1),
                'agree_with_evaluator': round(100 * agree_eval / n, 1),
                'kappa_vs_state': None if kappa([(verdicts[e], right[e]) for e in eps]) is None else round(kappa([(verdicts[e], right[e]) for e in eps]), 3),
            }
            # false passes among false-success episodes (claimed DONE, goal not met)
            fs = [e for e in eps if truth[e]['FS']]
            if fs:
                res['by_condition'][c]['pass_given_FS'] = round(100 * sum(verdicts[e] for e in fs) / len(fs), 1)
                res['by_condition'][c]['n_FS'] = len(fs)
            cs_ = [e for e in eps if truth[e]['CS']]
            if cs_:
                res['by_condition'][c]['pass_given_CS'] = round(100 * sum(verdicts[e] for e in cs_) / len(cs_), 1)
                res['by_condition'][c]['n_CS'] = len(cs_)
        out['judges'][f'{variant}__{model}'] = res
    (OUT / 'judge_summary.json').write_text(json.dumps(out, indent=1))
    for k, res in out['judges'].items():
        print(f"== {k}: verdicts {res['n_verdicts']}/{out['n_target']}, errors {res['errors']}, unparsed {res['unparsed']}, cost ${res['cost_usd']}")
        for c, s in res['by_condition'].items():
            print(f"  {c}: {json.dumps(s)}")


if __name__ == '__main__':
    main()
