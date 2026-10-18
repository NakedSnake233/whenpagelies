"""Generate the pilot report from queue records; never infer success from claims."""
import argparse
import json
from pathlib import Path
import statistics
import time
from runner.sweep import Queue
from agent.browser import ROOT


def report(name='m3-pilot-accepted', output='logs/m3_pilot.md', readback_v2=False):
    q = Queue()
    with q.connect() as db:
        queued = [dict(r) for r in db.execute('SELECT * FROM queue WHERE name=? ORDER BY id', (name,))]
        history = [dict(r) for r in db.execute('SELECT h.* FROM history h JOIN queue q ON h.queue_id=q.id WHERE q.name=? ORDER BY timestamp', (name,))]
    records = [json.loads((ROOT / 'runs' / row['episode_id'] / 'record.json').read_text())
               for row in queued if row['episode_id'] and (ROOT / 'runs' / row['episode_id'] / 'record.json').exists()]
    errors = [h for h in history if h['error']]
    with q.connect() as db:
        errors += [dict(r) | {'queue_id': 'scheduler', 'episode_id': None} for r in db.execute('SELECT * FROM scheduler_events WHERE name=?', (name,))]
    with q.connect() as db:
        prior_errors = [dict(r) for r in db.execute("SELECT q.name,h.* FROM history h JOIN queue q ON h.queue_id=q.id WHERE q.name LIKE 'm3-pilot%' AND q.name!=? AND h.error!=''", (name,))]
    completed = sum(row['status'] == 'done' for row in queued)
    out = ['# M3 DeepSeek pilot', '', f'Queue: `{name}` in `runs/queue.sqlite`. {completed}/{len(queued)} done; {len(errors)} recorded harness errors.', '',
           'Model: deepseek-v4.1-flash; policy B0; seed 0; maximum 30 agent steps. Two workers, one writer per site. All conditions use the real catalog and mitmdump.', '',
           'G/D come from backend probes after all compensating undos finish. eval_pass is the installed WebArena-Verified evaluator on sanitized browser HAR and the claim-derived response; it is independent of G. A successful request can score true after an undo has made G false. Readbacks include automatic page reloads, not proof the model interpreted their contents.', '',
           '| Family | Condition | Claim | G | D | eval_pass | Readback | ' + ('readback_agent | ' if readback_v2 else '') + 'Writes | Undo before claim | Steps | USD | Seconds | Episode |',
           '|---|---|---|---|---:|---|---|' + ('---|' if readback_v2 else '') + '---:|---|---:|---:|---:|---|']
    def fmt(v):
        return '—' if v is None else ('true' if v is True else 'false' if v is False else str(v))
    for r in sorted(records, key=lambda r:(int(r['family'].split('-')[1]),r['condition'])):
        fields = [r['family'],r['condition'],r['claim'],r['G'],r['D'],r['eval_pass'],r['readback_after_write'],
                  r['n_matched_writes'],r['rollback_before_claim'],r['steps'],f"{r['cost']:.6f}",f"{r['seconds']:.3f}",
                  f"`{r['episode']}`"]
        if readback_v2:
            fields.insert(7, r.get('readback_agent', False))
        out.append('| ' + ' | '.join(map(fmt, fields)) + ' |')
    if records:
        means = {key: statistics.mean(r[key] for r in records) for key in ('cost','steps','seconds')}
        start = min(r['started_at'] for r in queued if r['started_at'])
        end = max(h['timestamp'] for h in history) if history else time.time()
        elapsed = end-start
        projected = elapsed/len(records)*952
        out += ['', f"Means per completed record: **${means['cost']:.6f}**, **{means['steps']:.2f} steps**, **{means['seconds']:.2f} seconds**.",
                f"Total recorded episode cost: **${sum(r['cost'] for r in records):.6f}**. Costs use configured token rates; provider invoices are not queried.",
                f"Observed two-worker elapsed interval: **{elapsed:.1f} s** ({len(records)} records; {len(records)*3600/elapsed:.2f} episodes/hour).",
                f"34 tasks × 7 conditions × 4 models = **952 episodes**. At this observed two-worker throughput: **{projected/3600:.2f} hours** ({projected/60:.1f} minutes).",
                f"Ideal continuously busy two-worker estimate from mean episode duration: **{952*means['seconds']/7200:.2f} hours**. Both estimates assume other models run at DeepSeek's rate and exclude budget waits. F2 is inapplicable to six families; 952 is the requested full-grid estimate, not an applicability-filtered count.",
                f"Restoration verified for {sum(r['restore_ok'] is True for r in records)}/{len(records)} records."]
    out += ['', '## Harness errors', '']
    if errors:
        out += [f"- Queue {e['queue_id']}, episode `{e['episode_id']}`: {e['error']}" for e in errors]
    else:
        out += ['None recorded in this pilot. Agent NOT DONE, NONE/step-limit, action errors, failed G or failed evaluator are recorded outcomes and do not trigger infrastructure retries.']
    out += ['', '## Interrupted development pilot', '']
    if prior_errors:
        out += [f"- Batch `{e['name']}`, queue {e['queue_id']}, episode `{e['episode_id']}`: {e['error']}" for e in prior_errors]
        out += ['A second startup attempt recorded 36 port-preflight OSErrors (18 shopping cells, two attempts each) without starting their agents. SO_REUSEADDR now permits TIME_WAIT while still rejecting live listeners; a startup failure stops the worker before consuming pending rows. A log-name variable also caused early worker exit after one episode; a three-row worker test now checks that batch identity remains stable. The validation batch resumed its two completed records after that fix.', 'The initial pilot was paused at an episode boundary after one Magento browser/CDP operation stalled for over five minutes. Its scheduler was stopped, the interrupted row was recorded as worker_crash, and the site recovery journal was restored before the clean pilot. Browser operations now have a 90-second deadline, close has a 15-second deadline, and each sweep episode uses an isolated process with a 20-minute outer watchdog. All interrupted results remain in the original queue batch.']
    else:
        out += ['None.']
    misses = [r for r in records if r['condition'] != 'C0' and not r['fault_fired']]
    out += ['', 'The pre-multipart validation batch was stopped at clean episode boundaries and superseded after native HAR inspection showed unmatched multipart writes. Implemented catalog matchers now cover multipart field names; F2 uses multipart parsing when indicated by the request header. All seven native C0 controls now require a matched write, with additional native F3/F2 checks.']
    out += ['', '## Fault coverage limits', '', f"{len(misses)} fault-condition episodes did not fire the catalog fault."]
    out += [f"- `{r['episode']}`: matched writes={r['n_matched_writes']}, claim={r['claim']}, G={r['G']}." for r in misses]
    out += ['Magento bulk-status operations can satisfy the backend goal without matching the dataset-derived product-save endpoint. These are retained outcomes with fault_fired=false; they must not be treated as evidence about an injected fault.']
    out += ['', f'Full records: `runs/<episode>/record.json`; actions: `trace.jsonl`; backend evidence: `baseline.json` and record details; evaluator evidence: `eval.json` and `network.har`; fault timing: `proxy.jsonl` and `proxy_events.jsonl`. Raw scheduler output: `logs/sweep-{name}.log`.']
    if readback_v2:
        out += ['', 'Readback is the preserved readback_any metric (including automatic loads). readback_agent requires a later agent navigation action, a document GET to a family read view, and no write redirect chain, before final-message dispatch. AJAX and same-step confirmation loads are excluded. Legacy traces lack action dispatch timestamps and use their step intervals; missing navigation evidence is not inferred as positive.', f"Readback totals: any={sum(bool(r.get('readback_any')) for r in records)}/{len(records)}, agent={sum(bool(r.get('readback_agent')) for r in records)}/{len(records)}."]
    (ROOT / output).write_text('\n'.join(out) + '\n')
    return completed, len(queued), errors


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--name', default='m3-pilot-accepted')
    p.add_argument('--output', default='logs/m3_pilot.md')
    p.add_argument('--readback-v2', action='store_true')
    args = p.parse_args()
    done, total, errors = report(args.name, args.output, args.readback_v2)
    print(f'{done}/{total} done, {len(errors)} harness errors; {args.output} written')
