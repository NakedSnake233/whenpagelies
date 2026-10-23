"""Read-only r2 export; run with PYTHONDONTWRITEBYTECODE=1 python -m tools.export_r2.

Only the requested output/report files are written. The queue is opened by
analyze.capture with mode=ro; completed artifacts and publication order use its
unchanged selection rules. No runner, browser, credentials, or locks are used.
"""
import argparse
from collections import defaultdict
import gzip
import json
from pathlib import Path
import statistics

from tools.analyze import (ROOT, CONDITIONS, POLICY_MEANS, bounded_read,
                           budget_deferred, capture, derive, harness_reason, json_lines)

FIELDS = '''CF CS D DUP EFF EFP FS G MS cbr_prompted claim condition cost eligible
 episode eval_pass exposed extra_llm_calls family fired harm model n_done_refused
 n_matched_writes n_readbacks_agent n_verifications policy policy_cost readback_agent
 seconds seed site steps study task tokens'''.split()
# These are the three ActionParseError messages emitted by agent.agent and
# action_adapter. Importing the agent would unnecessarily load browser packages.
PARSE_ERRORS = {
    'Reply with exactly one action per turn.',
    'Use exactly one ```action fenced block containing one valid action call.',
    'Elements must be referenced by the bracketed numeric bid shown in the observation.',
}


def episode_row(record, trace):
    """Project analyze's outcome definitions onto the released episode schema."""
    row = derive(record)
    row['study'] = 'proprietary'
    for key in POLICY_MEANS:
        row.setdefault(key, 0.0 if key == 'policy_cost' else 0)
    result = {key: row[key] for key in FIELDS}
    for key in ('claim_before_rollback', 'rollback_delay_s'):
        result[key] = row[key]
    result['n_adapted_steps'] = sum(s.get('lenient_parse') == 'first_of_many' for s in trace)
    result['n_parse_error_steps'] = sum(
        s.get('action') == 'noop()' and s.get('error') in PARSE_ERRORS for s in trace)
    return result


def validate(released, runs, count=40):
    """Deterministic evenly spaced sample across the released table, endpoints included."""
    with gzip.open(released, 'rt') as source:
        reference = [json.loads(line) for line in source if line.strip()]
    if len(reference) < count or count < 2:
        raise ValueError('Validation needs at least 40 released episodes')
    mismatches = []
    for i in range(count):
        expected = reference[i * (len(reference) - 1) // (count - 1)]
        directory = runs / expected['episode']
        record = json.loads((directory / 'record.json').read_bytes())
        trace, torn = json_lines(bounded_read(directory / 'trace.jsonl'))
        if torn or budget_deferred(record) or harness_reason(record):
            raise ValueError('Invalid original validation episode')
        actual = episode_row(record, trace)
        if set(expected) != set(FIELDS):
            mismatches.append({'episode': expected['episode'], 'field': '<schema>'})
        for key in FIELDS:
            if key != 'study' and (actual[key] != expected.get(key)
                                  or type(actual[key]) is not type(expected.get(key))):
                mismatches.append({'episode': expected['episode'], 'field': key})
    return mismatches


def summary(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['condition']].append(row)
    lines = ['| condition | n | exposed | CS | FS | MS | D>0 | readback_agent | EFP | EFF | mean n_adapted_steps |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for condition in CONDITIONS:
        cell = grouped[condition]
        exposed = [r for r in cell if r['exposed']]
        counts = [sum(bool(r[k]) for r in exposed)
                  for k in ('CS', 'FS', 'MS', 'D', 'readback_agent', 'EFP', 'EFF')]
        mean = f"{statistics.mean(r['n_adapted_steps'] for r in cell):.3f}" if cell else 'NA'
        lines.append('| ' + ' | '.join(map(str, [condition, len(cell), len(exposed), *counts, mean])) + ' |')
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sweep', default='r2-luna-b0')
    parser.add_argument('--model', help='Keep only this model')
    parser.add_argument('--policy', help='Keep only this policy')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--released', type=Path, default=Path('data/episodes_main.jsonl.gz'))
    parser.add_argument('--original-runs', type=Path, default=Path('runs'))
    args = parser.parse_args()
    validation = 'Validation not requested.'
    if args.validate:
        mismatches = validate(args.released, args.original_runs)
        validation = f'Validation: 40 original episodes; {len(mismatches)} field mismatches (all released fields except study; types checked).'
        print(validation)
        for mismatch in mismatches:
            print(json.dumps(mismatch))
        if mismatches:
            raise SystemExit('Validation failed; export not written.')
    selected, _, _, _, _, queue, metadata = capture(ROOT, queue_file='r2_queue.sqlite', sweep=args.sweep)
    def matches(row):
        return ((args.model is None or row['model'] == args.model)
                and (args.policy is None or row['policy'] == args.policy))
    selected = [r for r in selected if matches(r)]
    queue = [q for q in queue if matches(q)]
    # Check the filtered queue, including unfinished planned episodes.
    if len({(q['model'], q['policy']) for q in queue}) > 1:
        raise ValueError(f'Expected a single model and policy in {args.sweep}')
    rows = [episode_row(r, r['_trace']) for r in selected]
    with gzip.open(args.out, 'wt', encoding='utf-8') as output:
        for row in rows:
            output.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
    table = summary(rows)
    print('\n'.join(table))
    report = [f'# {args.sweep} export ({metadata["snapshot_utc"]})',
              f'Output: {args.out}', validation,
              f'Exported {len(rows)} latest completed valid episodes; study=proprietary.',
              'Selection uses analyze.capture (read-only queue, stable artifacts, publication order).',
              'Harness failures, budget deferrals, active/incomplete, dry-run and superseded attempts excluded.',
              'Outcome counts are among exposed; mean adapted steps is over all n selected episodes.',
              'Parse errors count noop() trace steps carrying one of the three ActionParseError messages.',
              '', *table]
    if args.report:
        args.report.write_text('\n'.join(report) + '\n')


if __name__ == '__main__':
    main()
