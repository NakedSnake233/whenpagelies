"""Native-browser positive/negative controls and optional multipart fault checks."""
import argparse
import json
from agent.browser import ROOT
from runner.run_episode import parser, run_episode

REPRESENTATIVES = [('reddit', 400), ('reddit', 404), ('reddit', 595), ('reddit', 630),
                   ('shopping_admin', 453), ('shopping_admin', 458), ('shopping_admin', 768)]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--condition', choices=['C0','F3','F2'], default='C0')
    p.add_argument('--worker', type=int, default=3)
    options = p.parse_args()
    cases = [('reddit',630)] if options.condition == 'F2' else REPRESENTATIVES.copy()
    if options.condition == 'C0':
        cases += [('reddit',400)]
    results = []
    suffix = '' if options.condition == 'C0' else '_' + options.condition.lower()
    for site, tid in cases:
        noop = options.condition == 'C0' and len(results) == 7
        args = parser().parse_args(['--task', f'tasks/{site}_{tid}.json', '--dry-run',
            'noop' if noop else 'default', '--max-steps', '20', '--worker', str(options.worker),
            '--condition', options.condition])
        result = run_episode(args)
        results.append(result)
        print(json.dumps({k: result[k] for k in ('episode','family','G','D','eval_pass','fault_fired','n_matched_writes','restore_ok','error')}), flush=True)
        (ROOT / f'logs/m3_dry{suffix}_results.json').write_text(json.dumps(results, indent=2) + '\n')
    positive = results[:7] if options.condition == 'C0' else results
    expected_g = options.condition == 'C0'
    assert all(not r['error'] and r['G'] == expected_g and r['D'] == 0 and r['eval_pass'] and
               r['restore_ok'] and r['n_matched_writes'] >= 1 for r in positive)
    if options.condition != 'C0':
        assert all(r['fault_fired'] for r in results)
    else:
        assert not results[-1]['error'] and not results[-1]['G'] and not results[-1]['eval_pass'] and results[-1]['restore_ok']


if __name__ == '__main__':
    main()
