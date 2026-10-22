"""Read-only, balanced recorded-reply regression replay against a saved parser."""
import argparse
import json
from pathlib import Path
import runpy
from agent.agent import parse_action


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--old-parser', type=Path, default=Path('.tmp/pre_luna_agent.py'))
    args = cli.parse_args()
    old = runpy.run_path(str(args.old_parser))['parse_action']
    models = ['deepseek-v4.1-flash', 'qwen3.7-plus', 'minimax-m3', 'mimo-v2.6-flash']
    counts = {}
    paths = sorted(Path('runs').glob('*/trace.jsonl'))
    for model in models:
        count = dict(replies=0, old_errors=0, new_errors=0, changed=0)
        for path in paths:
            if f'-{model}-' not in path.parent.name:
                continue
            record = path.with_name('record.json')
            policy = json.loads(record.read_text()).get('policy', 'B0') if record.exists() else 'B0'
            for line in path.read_text().splitlines():
                row = json.loads(line)
                if 'raw_model_text' not in row or count['replies'] >= 75:
                    continue
                outcomes = []
                for parser, kwargs in [(old, {}), (parse_action, {'model': model})]:
                    meta = {}
                    try:
                        result = ('ok', parser(row['raw_model_text'], policy, meta, **kwargs), meta)
                    except ValueError as exc:
                        result = ('error', str(exc), meta)
                    outcomes.append(result)
                count['replies'] += 1
                count['old_errors'] += outcomes[0][0] == 'error'
                count['new_errors'] += outcomes[1][0] == 'error'
                count['changed'] += outcomes[0] != outcomes[1]
            if count['replies'] >= 75:
                break
        counts[model] = count
    Path('logs/r2_adapter_replay.json').write_text(json.dumps(counts, indent=2) + '\n')
    print(json.dumps(counts, indent=2))
    assert sum(c['replies'] for c in counts.values()) == 300
    assert not any(c['changed'] for c in counts.values())


if __name__ == '__main__':
    main()
