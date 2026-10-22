"""Offline adapter audit. Reads only the fixed export and selected completed artifacts."""
import argparse
import ast
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re

OUT = Path('analysis/review/proprietary')
SOURCE = Path('data/episodes_proprietary.jsonl.gz')
RUNS = Path('runs')
TYPES = ('click', 'fill', 'goto', 'select_option', 'send_msg_to_user', 'scroll', 'other')
WORD = re.compile(r'verify|check|confirm', re.I)


def calls(text):
    # Same top-level AST extraction as agent/action_adapter.py:first_of_many.
    blocks = re.findall(r'```action\s*\n(.*?)```', text, re.S)
    if blocks:
        assert len(blocks) == len(re.findall(r'```action(?=\s|$)', text))
    else:
        assert '```' not in text
        blocks = [text]
    result = []
    for block in blocks:
        block = block.strip()
        for node in ast.parse(block).body:
            assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            result.append(ast.get_source_segment(block, node.value))
    return result


def kind(action):
    node = ast.parse(action.strip()).body[0].value
    return node.func.id if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) else 'other'


def canonical(action):
    return ast.dump(ast.parse(action.strip()), include_attributes=False)


def done(action):
    if kind(action) != 'send_msg_to_user':
        return False
    node = ast.parse(action.strip()).body[0].value
    values = node.args or [k.value for k in node.keywords]
    return bool(values and str(ast.literal_eval(values[0])).lstrip().startswith('DONE'))


def stable_read(path):
    # No directory-wide scans, queue imports, runner imports, HARs, or credentials.
    assert not path.is_symlink()
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    return data, (after.st_size, after.st_mtime_ns)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', type=Path, default=SOURCE)
    parser.add_argument('--out-dir', type=Path, default=OUT)
    args = parser.parse_args()
    compressed = args.export.read_bytes()
    rows = [json.loads(s) for s in gzip.decompress(compressed).splitlines() if s.strip()]
    rows = [r for r in rows if r['model'] == 'gpt-5.6-luna']
    assert len({r['episode'] for r in rows}) == len(rows)
    histogram, discarded_types, other_types = Counter(), Counter(), Counter()
    details, examples, signatures = [], [], []
    adapted = 0
    rejected_first = 0
    for row in rows:
        episode = row['episode']
        assert Path(episode).name == episode and episode not in ('.', '..')
        directory = RUNS / episode
        assert not directory.is_symlink()
        blobs = {}
        for name in ('record.json', 'trace.jsonl', 'proxy.jsonl'):
            path = directory / name
            data, signature = stable_read(path)
            signatures.append((path, signature))
            blobs[name] = data
        record = json.loads(blobs['record.json'])
        assert all(record[k] == row[k] for k in ('episode', 'model', 'condition', 'task', 'n_matched_writes'))
        trace = [json.loads(s) for s in blobs['trace.jsonl'].splitlines() if s.strip()]
        flows = [json.loads(s) for s in blobs['proxy.jsonl'].splitlines() if s.strip()]
        dropped = {}
        for i, step in enumerate(trace):
            if step.get('lenient_parse') != 'first_of_many':
                continue
            actions = calls(step['raw_model_text'])
            assert len(actions) == step['n_actions_in_reply'] >= 2
            if canonical(actions[0]) != canonical(step['action']):
                assert step['action'] == 'noop()' and step.get('error')
                rejected_first += 1
            adapted += 1
            histogram[min(len(actions), 5)] += 1
            dropped[i] = actions[1:]
            for action in actions[1:]:
                name = kind(action)
                discarded_types[name if name in TYPES else 'other'] += 1
                if name not in TYPES:
                    other_types[name] += 1
        assert len(dropped) == row['n_adapted_steps']
        if not row['exposed']:
            continue
        writes = sorted([f for f in flows if f.get('matched_entry') == row['family']
                         and f['method'] in ('POST', 'PUT', 'PATCH', 'DELETE')],
                        key=lambda f: f.get('request_ts', f['ts']))
        assert len(writes) == row['n_matched_writes']
        first = writes[0]
        timestamp = first.get('request_ts', first['ts'])
        candidates = [i for i, s in enumerate(trace)
                      if s.get('action_started_at', float('inf')) <= timestamp <= s.get('ended_at', 0)
                      and any(m['method'] == first['method'] and m['url'] == first['url']
                              for m in s.get('mutating_requests', []))]
        if len(candidates) == 1:
            start, anchor = candidates[0], 'action interval + matching mutating request'
        else:
            candidates = [i for i, s in enumerate(trace) if s.get('ended_at', float('inf')) <= timestamp]
            start, anchor = (max(candidates), 'last completed step before request') if candidates else (None, 'unresolved')
        item = dict(episode=episode, condition=row['condition'], anchor=anchor,
                    has_final_message=any(kind(s['action']) == 'send_msg_to_user' for s in trace),
                    start_step=trace[start]['step'] if start is not None else None,
                    a=False, b=False, c=False, events=[])
        if start is not None:
            for i, actions in dropped.items():
                if i < start:
                    continue
                a = any(kind(x) == 'goto' or (kind(x) == 'click' and canonical(x) != canonical(trace[i]['action'])) for x in actions)
                b = any(done(x) for x in actions)
                raw = trace[i]['raw_model_text']
                reasoning = re.sub(r'```action\s*\n.*?```', '', raw, flags=re.S) if '```action' in raw else ''
                c = bool(WORD.search(reasoning) or any(WORD.search(x) for x in actions))
                item['a'] |= a
                item['b'] |= b
                item['c'] |= c
                if a:
                    later = []
                    for s in trace[i + 1:]:
                        if kind(s['action']) == 'send_msg_to_user':
                            break
                        later.append(s)
                    item['events'].append(dict(step=trace[i]['step'],
                        later_navigation=any(kind(s['action']) in ('goto', 'click') for s in later),
                        later_navigation_page=any(kind(s['action']) in ('goto', 'click') and s.get('document_responses') for s in later),
                        later_nonmessage=bool(later),
                        later_any_page=any(s.get('document_responses') for s in later),
                        next_done=i + 1 < len(trace) and done(trace[i + 1]['action'])))
            if start in dropped:
                examples.append((row, trace[start], anchor))
        details.append(item)
    for path, signature in signatures:
        stat = path.stat()
        assert (stat.st_size, stat.st_mtime_ns) == signature, 'Completed input changed during audit'
    lines = ['# gpt-5.6-luna first_of_many audit', '',
             f'Fixed export: `{args.export}`; SHA-256 `{hashlib.sha256(compressed).hexdigest()}`.',
             f'{len(rows)} selected episodes; {adapted} adapted steps; {sum(discarded_types.values())} discarded actions. All adapted replies parsed and matched the recorded action count. In {rejected_first} adapted steps the first action failed validation and became noop(); all other first actions matched the executed action. Discarded counts include only calls after the first, including in those rejected-first replies.', '',
             '## 1. All selected episodes', '', '| Actions in reply | Adapted steps |', '|---|---:|']
    lines += [f'| {n if n < 5 else "5+"} | {histogram[n]} |' for n in (2, 3, 4, 5)]
    lines += ['', '| Discarded action type | Count | Share of discarded actions |', '|---|---:|---:|']
    total = sum(discarded_types.values())
    lines += [f'| {k} | {discarded_types[k]} | {discarded_types[k]/total:.2%} |' for k in TYPES]
    lines += ['', 'Other actions: ' + ', '.join(f'{k}={v}' for k, v in sorted(other_types.items())) + '.']
    lines += ['', '## 2–3. Exposed episodes, from first write onward', '',
              'The anchor is the first matched write by request time: proxy family match and POST/PUT/PATCH/DELETE, following runner.metrics. Attribution requires the request timestamp inside the executed action interval plus matching method/URL in trace mutating_requests; fallback is the last completed trace step before that request. The anchor step is included.',
              'Counts are distinct episodes. (a) Discarded goto, or click AST-distinct from the executed action. (b) Discarded message whose text starts with DONE (leading whitespace ignored). (c) Case-insensitive verify/check/confirm substring in a discarded action or the reply’s prose outside action fences. Shared prose cannot reliably be attributed to an individual discarded action, so (c) is a broad lexical indicator, not proof of verification intent.', '',
              '| Condition | Exposed | Anchored | (a) Navigation | (b) DONE | (c) Verification words |', '|---|---:|---:|---:|---:|---:|---:|']
    for condition in ('F1', 'F3', 'C0', 'F4'):
        cell = [x for x in details if x['condition'] == condition]
        lines.append('| ' + ' | '.join(map(str, [condition, len(cell), sum(x['start_step'] is not None for x in cell), *[sum(x[k] for x in cell) for k in ('a', 'b', 'c')]])) + ' |')
    lines += ['', 'Among (a), follow-up is measured strictly after a qualifying discarded-navigation step and before the first subsequent executed user message. The page-load measure requires an executed goto/click with a recorded document response (including error pages). Also shown are the looser “any later non-message action” proxy and navigation without page-load evidence. Next-step DONE means the immediately following trace step executed a DONE message. Episode-level columns can overlap when different qualifying steps have different follow-ups.', '',
              '| Condition | (a) episodes | Later goto/click with page response | Later goto/click, any | Any later non-message action | Any later action with page response | Next-step DONE | Page response AND next-step DONE across events |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for condition in ('F1', 'F3', 'C0', 'F4'):
        cell = [x for x in details if x['condition'] == condition and x['a']]
        keys = ('later_navigation_page', 'later_navigation', 'later_nonmessage', 'later_any_page', 'next_done')
        counts = [sum(any(e[k] for e in x['events']) for x in cell) for k in keys]
        both = sum(any(e['later_navigation_page'] for e in x['events']) and any(e['next_done'] for e in x['events']) for x in cell)
        lines.append('| ' + ' | '.join(map(str, [condition, len(cell), *counts, both])) + ' |')
    lines += ['', 'If no final user message was executed, follow-up runs to trace end. Among (a), episodes without an executed final message: ' + ', '.join(f"{condition}={sum(x['a'] and not x['has_final_message'] for x in details if x['condition'] == condition)}" for condition in ('F1', 'F3', 'C0', 'F4')) + '.',
              'Anchor methods: ' + '; '.join(f'{k}: {v}' for k, v in Counter(x['anchor'] for x in details).items()) + '.',
              'These are observed follow-ups, not evidence that the exact discarded action was re-issued or that the written state was verified. The write step’s own redirects/page responses do not count as later navigation. No counterfactual adapter effect can be inferred from these counts.', '',
              '## 4. Verbatim write-step replies (first 600 characters)', '']
    chosen = []
    for condition in ('F1', 'F3', 'F4'):
        options = [x for x in examples if x[0]['condition'] == condition]
        options.sort(key=lambda x: (not any(kind(a) == 'goto' for a in calls(x[1]['raw_model_text'])[1:]), x[0]['episode']))
        if options:
            chosen.append(options[0])
    assert len(chosen) == 3
    for row, step, anchor in chosen:
        reply = step['raw_model_text'][:600]
        # Reject potentially sensitive examples rather than ever output headers/keys.
        assert not re.search(r'authorization|bearer\s|api[_ -]?key|password|cookie|headers?|sk-[A-Za-z0-9]', reply, re.I)
        lines += [f"{row['condition']} — `{row['episode']}`, step {step['step']}; executed `{step['action']}`; {step['n_actions_in_reply'] - 1} discarded actions.", '', '````text', reply, '````', '']
    lines += ['## Reproduction and limits', '',
              '`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/review/adapter_audit.py`',
              'Only the fixed export and selected runs/<episode>/{record.json,trace.jsonl,proxy.jsonl} were read as data. Inputs were checked for stable size/mtime during the audit. No queue DB, processes, secrets, HARs, or original original harness artifacts were accessed. Outputs are confined to adapter_audit/. The fixed export defines selection; live sweep additions are excluded.',
              'Episode-level flags and follow-up step evidence are saved in details.json for inspection. No raw replies, URLs, headers, or keys are included in that file.']
    report = '\n'.join(lines) + '\n'
    args.out_dir.mkdir(exist_ok=True)
    (args.out_dir / 'details.json').write_text(json.dumps(details, indent=2) + '\n')
    (args.out_dir / 'report.md').write_text(report)
    print(report)


if __name__ == '__main__':
    main()
