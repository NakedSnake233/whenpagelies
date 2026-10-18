"""Build M5 queues without launching workers: python -m runner.plans."""
import argparse
from collections import Counter, defaultdict
import json
import math
import re
from pathlib import Path

import yaml

from agent.llm import CONFIG
from runner.sweep import Queue, ROOT

MODELS = ('deepseek-v4.1-flash', 'mimo-v2.6-flash', 'minimax-m3', 'qwen3.7-plus')
POLICIES = ('B1', 'B2', 'VRV', 'VRV-modeltrigger', 'VRV-samepage', 'VRV-nocbr', 'Oracle')
CONDITIONS = ('C0', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6')
DEFAULT_DB = ROOT / 'runs/m5_queue.sqlite'


def task_paths(task_list=None):
    """Select indexed tasks, optionally narrowed by JSON or newline path list."""
    indexed = {(ROOT / 'tasks' / name).resolve() for name in re.findall(
        r'\[[^\]]*\]\(([^)\s]+\.json)\)', (ROOT / 'tasks/INDEX.md').read_text())}
    if task_list is None:
        paths = sorted(indexed)
    else:
        source = Path(task_list).read_text()
        names = json.loads(source) if source.lstrip().startswith('[') else [
            line.strip() for line in source.splitlines() if line.strip() and not line.lstrip().startswith('#')]
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ValueError('task list must contain task file paths')
        paths = [ROOT / name for name in names]
    catalogs = {}
    selected = []
    for path in sorted(set(p.resolve() for p in paths)):
        if not path.is_relative_to(ROOT):
            raise ValueError('task outside repository')
        if path not in indexed:
            raise ValueError(f'task not listed in tasks/INDEX.md: {path.name}')
        task = json.loads(path.read_text())
        if not isinstance(task, dict) or not task.get('family') or not task.get('site'):
            if task_list is not None:
                raise ValueError(f'not a family task: {path}')
            continue
        site = task['site']
        if site not in catalogs:
            if not site.replace('_', '').isalnum():
                raise ValueError('invalid site')
            catalog = ROOT / 'catalog' / f'{site}.yaml'
            catalogs[site] = {entry['id']: entry for entry in
                              yaml.safe_load(catalog.read_text())['entries']} if catalog.exists() else {}
        family = catalogs[site].get(task['family'], {})
        if family.get('implemented') is True:
            f2 = family.get('f2') or {}
            applicable = f2.get('applicable', True) and bool(
                f2.get('strip_fields') or f2.get('strip_json_paths'))
            selected.append((path, bool(applicable)))
        elif task_list is not None:
            raise ValueError(f'task family is not implemented: {path.name}')
    if not selected:
        raise ValueError('no implemented tasks selected')
    return selected


def plan_rows(kind, tasks):
    models = sorted(MODELS, key=lambda model: (
        CONFIG['models'][model]['input'] + CONFIG['models'][model]['output'], model)) if kind == 'main' else [MODELS[0]]
    policies = ['B0'] if kind == 'main' else POLICIES
    # Task rounds, then condition cells, then cheapest model, then policy.
    for path, f2 in tasks:
        for condition in CONDITIONS:
            if condition == 'F2' and (kind == 'mitigation' or not f2):
                continue
            for model in models:
                for policy in policies:
                    yield str(path), model, policy, condition, 0


def episode_means(directory):
    samples = defaultdict(list)
    for path in sorted(Path(directory).glob('*/record.json')):
        record = json.loads(path.read_text())
        if record.get('dry_run') or record.get('error') or not record.get('steps'):
            continue
        cost = record.get('cost')
        if isinstance(cost, (int, float)) and math.isfinite(cost) and cost >= 0:
            samples[record['model']].append(cost)
    return {model: dict(mean_usd=sum(samples[model])/len(samples[model]) if samples[model] else .01,
                        observed_episodes=len(samples[model]), fallback=not bool(samples[model]))
            for model in MODELS}


def build(kind, tasks, queue, means, name):
    rows = list(plan_rows(kind, tasks))
    Queue.require_enabled({row[1] for row in rows})
    added = 0
    for task, model, policy, condition, seed in rows:
        added += queue.add(name, [task], [model], [policy], [condition], [seed])
    counts = Counter(row[1] for row in rows)
    return dict(plan=kind, name=name, tasks=len(tasks), planned_episodes=len(rows), added=added,
                episodes_per_model=dict(counts),
                estimated_cost_usd=sum(n * means[model]['mean_usd'] for model, n in counts.items()),
                model_cost_basis={model: means[model] for model in counts})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--task-list', type=Path)
    parser.add_argument('--plan', choices=['main', 'mitigation', 'both'], default='both')
    parser.add_argument('--name-prefix', default='m5')
    args = parser.parse_args()
    tasks = task_paths(args.task_list)
    means = episode_means(ROOT / 'runs')
    queue = Queue(args.db)
    for kind in (('main', 'mitigation') if args.plan == 'both' else (args.plan,)):
        print(json.dumps(build(kind, tasks, queue, means, f'{args.name_prefix}-{kind}'), sort_keys=True))


if __name__ == '__main__':
    main()
