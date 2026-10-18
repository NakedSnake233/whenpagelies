"""Generate the 34 runnable tasks, retaining the M2 task IDs and notes."""
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
ORIGINS = {'reddit': 'http://127.0.0.1:9999', 'shopping_admin': 'http://localhost:7780/admin'}


def main():
    count = 0
    for site, origin in ORIGINS.items():
        catalog = yaml.safe_load((ROOT / f'catalog/{site}.yaml').read_text())
        ids = {i: e['id'] for e in catalog['entries'] if e.get('implemented') for i in e['task_ids']}
        for task in json.loads((ROOT / 'catalog/site_tasks.json').read_text()):
            tid = task['task_id']
            if tid not in ids:
                continue
            old = ROOT / f'config/tasks/{site}_{tid}.json'
            result = json.loads(old.read_text()) if old.exists() else {'id': f'{site}-{tid}'}
            result.update(site=site, family=ids[tid], start_url=task['start_urls'][0].replace(
                f'__{site.upper()}__', origin), intent=task['intent'], webarena_task_id=tid,
                instantiation=task['instantiation_dict'])
            (ROOT / f'tasks/{site}_{tid}.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
            count += 1
    print(f'Generated {count} tasks')


if __name__ == '__main__':
    main()
