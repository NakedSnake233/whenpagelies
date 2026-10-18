"""M5d resume health evidence; reads queue/episode artifacts, never racing DB writes."""
import json
from pathlib import Path
import sqlite3
import time

from tools.pristine_state import ROOT, save


def sample(resume):
    with sqlite3.connect(ROOT / 'runs/m5_queue.sqlite') as db:
        db.row_factory = sqlite3.Row
        running = [dict(r) for r in db.execute("SELECT id,site,worker,model,condition,started_at FROM queue WHERE name='m5-main' AND status='running'")]
        completed = db.execute('''SELECT q.site,h.episode_id,h.timestamp FROM history h JOIN queue q ON q.id=h.queue_id
            WHERE q.name='m5-main' AND h.timestamp>=? AND h.episode_id IS NOT NULL ORDER BY h.timestamp''',
                               (resume['at'],)).fetchall()
    first = {}
    for row in completed:
        if row['site'] in first:
            continue
        directory = ROOT / 'runs' / row['episode_id']
        record = json.loads((directory / 'record.json').read_text())
        reset_path = directory / 'global_reset.json'
        report = json.loads(reset_path.read_text()) if reset_path.exists() else {}
        first[row['site']] = dict(episode=row['episode_id'], at=row['timestamp'],
                                 restore_ok=record['restore_ok'], drift=report.get('after', {}).get('drift'),
                                 reset_seconds=report.get('seconds'))
    counts = {site: sum(r['site'] == site for r in running) for site in ('reddit', 'shopping_admin')}
    alive = Path(f'/proc/{resume["pid"]}').exists()
    dirty = [p.name for p in (ROOT / 'runs').glob('dirty_*')]
    return dict(at=time.time(), elapsed=time.time()-resume['at'], scheduler_alive=alive,
                running=running, running_per_site=counts, dirty_markers=dirty,
                first_completed=first, healthy=alive and not dirty and all(v == 1 for v in counts.values())
                and len(first) == 2 and all(r['restore_ok'] and r['drift'] == 0 for r in first.values()))


def main():
    resume = json.loads((ROOT / 'logs/m5d_resume.json').read_text())
    while time.time() < resume['at'] + 180:
        time.sleep(min(10, resume['at'] + 180 - time.time()))
    three_minute = sample(resume)
    save(ROOT / 'logs/m5d_health_3min.json', three_minute)
    deadline = resume['at'] + 1800
    while True:
        result = sample(resume)
        save(ROOT / 'logs/m5d_health.json', result)
        if result['healthy'] or result['dirty_markers'] or not result['scheduler_alive'] or time.time() >= deadline:
            print(json.dumps(result), flush=True)
            if not result['healthy']:
                raise SystemExit(1)
            return
        time.sleep(10)


if __name__ == '__main__':
    main()
