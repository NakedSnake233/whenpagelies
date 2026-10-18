"""Shared exclusion list for replaced benchmark episodes; artifacts stay intact."""
import json


def superseded_ids(root):
    path = root / 'runs/superseded.jsonl'
    if not path.exists():
        return set()
    return {json.loads(line)['episode_id'] for line in path.read_text().splitlines() if line.strip()}
