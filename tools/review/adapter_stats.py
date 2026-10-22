#!/usr/bin/env python3
"""Print supplementary episode statistics without modifying input files."""
import gzip
import json
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path


def main():
    root = Path('data')
    with gzip.open(root / 'episodes_proprietary.jsonl.gz', 'rt') as stream:
        episodes = [json.loads(line, parse_float=Decimal) for line in stream if line.strip()]
    steps = sum(e['steps'] for e in episodes)
    adapted = sum(e['n_adapted_steps'] for e in episodes)
    print('(a) overall')
    print('total_steps:', steps)
    print('total_n_adapted_steps:', adapted)
    print('share_adapted:', adapted / steps if steps else 'undefined (zero steps)')
    print('total_n_parse_error_steps:', sum(e['n_parse_error_steps'] for e in episodes))
    print('sum_cost:', sum((e['cost'] for e in episodes), Decimal(0)))

    exposed = [e for e in episodes if e['exposed'] is True]
    print('\n(c) counts by claim')
    for label, conditions in [('C0', ('C0',)), ('F1+F3', ('F1', 'F3'))]:
        counts = Counter(e['claim'] for e in exposed if e['condition'] in conditions)
        print(label, json.dumps([{'claim': claim, 'count': count}
                                for claim, count in sorted(counts.items(), key=lambda x: str(x[0]))]))

    print('\n(d) exposed episodes with steps >= 30 and claim != DONE')
    for condition in sorted({e['condition'] for e in episodes}):
        count = sum(e['condition'] == condition and e['steps'] >= 30
                    and e['claim'] != 'DONE' for e in exposed)
        print(condition, count)

    print('\n(f) gpt-5.6-luna calls: SUM(cost), COUNT(*)')
    connection = sqlite3.connect(
        'file:runs/budget.sqlite?mode=ro', uri=True)
    try:
        print(connection.execute(
            "SELECT SUM(cost), COUNT(*) FROM calls WHERE model='gpt-5.6-luna'"
        ).fetchone())
    finally:
        connection.close()


if __name__ == '__main__':
    main()
