"""Offline old/new parser replay; preserves per-step outcomes for regression review."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import runpy

from agent.action_adapter import to_action_call
from agent.agent import ActionParseError, parse_action
from agent.llm import CONFIG
from runner.storage import write_json


def replay(root, old_parser):
    counts = defaultdict(lambda: dict(steps=0, old_errors=0, new_errors=0, changed=0))
    outcomes = []
    def outcome(parser, text, policy):
        try:
            return ['ok', parser(text, policy)]
        except (ActionParseError, ValueError) as exc:
            return ['error', str(exc)]
    def adapted(text, policy):
        call = to_action_call(text)
        return parse_action(text if call is None else call, policy)
    for path in sorted((root / 'runs').glob('*/trace.jsonl')):
        record = path.with_name('record.json')
        meta = json.loads(record.read_text()) if record.exists() else {}
        model = meta.get('model') or next((m for m in CONFIG['models'] if f'-{m}-' in path.parent.name), 'unknown')
        policy = meta.get('policy') or next((p for p in ('VRV-modeltrigger', 'VRV-samepage', 'VRV-nocbr', 'Oracle', 'VRV', 'B0', 'B1', 'B2') if f'-{p}-' in path.parent.name), 'B0')
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if 'raw_model_text' not in row:
                continue
            before = outcome(old_parser, row['raw_model_text'], policy)
            after = outcome(adapted, row['raw_model_text'], policy)
            count = counts[model]
            count['steps'] += 1
            count['old_errors'] += before[0] == 'error'
            count['new_errors'] += after[0] == 'error'
            count['changed'] += before != after
            outcomes.append(dict(episode=path.parent.name, step=row.get('step'), model=model,
                                 old=before, new=after))
    for model in ('deepseek-v4.1-flash', 'qwen3.7-plus'):
        if counts[model]['changed']:
            raise AssertionError(f'{model}: parser regression')
    return dict(models=dict(counts), outcomes=outcomes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-parser', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('logs/m5a_replay.json'))
    args = parser.parse_args()
    result = replay(Path(__file__).resolve().parents[1], runpy.run_path(str(args.old_parser))['parse_action'])
    write_json(args.output, result)
    print(json.dumps(result['models'], indent=2))


if __name__ == '__main__':
    main()
