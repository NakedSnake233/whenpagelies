# M3 episodes and sweeps

Generate canonical tasks with `.venv/bin/python -m tools.build_m3_tasks`.
Both `tasks/<site>_<id>.json` and the older `config/tasks/` fixtures are accepted.
The canonical task ID joins to the site's catalog/probe data; evaluation loads
the unmodified installed WebArena-Verified dataset.

```sh
.venv/bin/python -m runner.run_episode --task tasks/reddit_400.json --dry-run
.venv/bin/python -m runner.run_episode --task tasks/reddit_400.json --dry-run noop
.venv/bin/python -m runner.sweep add --name example --tasks tasks/reddit_400.json tasks/shopping_admin_453.json --models deepseek-v4.1-flash --policies B0 --conditions C0 F1 F3 F4 F5 F6 --seeds 0
.venv/bin/python -m tools.background sweep-example .venv/bin/python -m runner.sweep run --name example --workers 2
.venv/bin/python -m runner.sweep status --name example
```

Port zero on the episode CLI means manage a local proxy at 8300+worker; real
benchmark episodes always use mitmdump, including C0. For an externally managed
proxy, pass its port and set its OSD_PLAN to `runs/plans/worker-N.json`,
OSD_CATALOG_DIR to `catalog/`, and OSD_RUNS_DIR to `runs/`.

Each sweep worker owns one proxy and invokes isolated episode processes. Proxy
startup checks never terminate an unknown port owner. SQLite transactions claim
work atomically; scheduler/worker locks and the same per-site locks as the site
proof suite prevent overlapping writers. An agent NOT DONE, step limit, failed
G, or evaluator failure is a completed experiment, not an infrastructure retry.
Infrastructure errors receive one retry. Budget waits preserve retry counts.

Baselines, active-site journals and records publish atomically. A crashed row is
reconciled against a committed episode record, or retried once; the next episode
restores an interrupted site's journal before taking its baseline. Restore checks
exact scoped pre-images, excluding sequence watermarks which are not rewound.
Dirty-site markers block later writers. Never remove them without checking the
recorded baseline and performing a verified scoped recovery.

The agent's final message timestamp is separate from episode completion. The
runner closes the browser, waits up to 60 seconds for all scheduled undos, scores
backend G/D and HAR evidence, then restores state while holding the site lock.
Native browser operations have a 90-second watchdog, close has 15 seconds, and a
sweep episode has a 20-minute outer watchdog. Persistent budget accounting covers
known and conservatively reserved charges.

Readbacks count matching GET requests begun after the first completed matched
write and before the final message, including automatic reloads. They do not
establish that the agent understood the page. A fault condition can have
fault_fired=false if no eligible write occurs (including a valid alternative
endpoint such as Magento massStatus). The original WebArena evaluator is request
based and can disagree with backend G after an injected undo or an alternative
workflow. Keep these distinctions when curating tasks or computing results.

Acceptance commands and results are in `logs/m3_status.md`. Pilot tables are in
`logs/m3_pilot.md`; background curation guidance is in `logs/m3_curation_howto.md`.

M3b keeps that inclusive flag as `readback_any` (`readback_after_write` remains
an alias) and adds `readback_agent`: a later agent goto/click/history/reload/tab
action must cause a document GET to a family read view before the final message.
The write step, redirect chains, AJAX, and requests outside an action interval
are excluded. HAR navigation headers supply evidence for older records; newer
traces also retain action dispatch times. Legacy step intervals cannot recover
an exact dispatch time, and absent navigation evidence is never a positive.

```sh
.venv/bin/python -m tools.recompute_metrics
.venv/bin/python -m tools.m3_report --readback-v2 --output logs/m3_pilot_v2.md
```

The metric migration preserves `record.before_m3b_metrics.json` and updates the
aggregate while holding its append lock, so it can run alongside curation.

Sweep policies: B0, B1, B2, VRV, VRV-modeltrigger, VRV-samepage, VRV-nocbr, Oracle.
Unknown names fail before any queue insertion. Oracle alone receives the live
zero-argument backend G/D callback. To replace a stopped worker while another
scheduler remains live, choose an unused worker ID and use `attach-worker`;
it takes the same worker ownership lock and never recovers another worker's row.
For example (after verified site repair and scoped requeue):

```sh
.venv/bin/python -m tools.background sweep-shopping-resume .venv/bin/python -m runner.sweep attach-worker --name m3-curation --worker 6 --site shopping_admin
```

M5a accepts the observed MiMo `<tool_call>` forms and MiniMax
`<function_calls><invoke ...>` form when no action fence is present. Adapted
calls pass the same allowed-action, signature, literal, and numeric-bid checks.
Trace rows mark these calls with `native_format: true`; ordinary unfenced calls
retain `lenient_parse: true`. Multiple native calls remain invalid.

For an offline compatibility check, save the pre-change `agent/agent.py` locally
and run `python -m tools.replay_action_adapter --old-parser PATH`. This checks
every primary reply in the trace files, including interrupted episodes, and
requires identical DeepSeek/Qwen actions and errors.

After stopping the scheduler and recovering its interrupted sites and rows,
`python -m tools.supersede_episodes --db runs/m5_queue.sqlite --name m5-main
--models mimo-v2.6-flash minimax-m3 --before UNIX_TIMESTAMP` records exclusions
in `runs/superseded.jsonl` and resets the corresponding plans to pending. The
cutoff is fixed at adapter installation time. Repeating the command is safe;
new attempts receive fresh UUID episode directories. Old directories, queue
history, and aggregate records remain available for audit. `tools.sweep_summary`
excludes those IDs; `tools.recompute_metrics` leaves their records untouched and
excludes them from its metric totals. M5a's operational report is
`logs/m5a_status.md`.

M5d adds a fixed pristine reset after verified scoped restore, while the episode
still holds its site lock. Backend G/D and HAR evaluation remain before cleanup;
F3 continues to call only the original scoped compensator. Every cleanup writes
`global_reset.json`, and any snapshot, SQL, or final-diff failure sets the existing
dirty-site marker. Interrupted recovery uses the interrupted episode's evidence.

`config/pristine_manifest.json` pins the live image IDs and SHA256 of private,
gitignored snapshots in `data/pristine/`. Capture once, while paused, with
`python -m tools.background m5d-capture .venv/bin/python -m tools.pristine_state capture`.
References have no network or published ports and only this command's newly
created audit container IDs are removed. The immutable archive also retains
M5c projections; a derived SQLite index avoids scanning millions of comment
scores during each episode. Snapshot bodies include private user fields and must
never be printed or committed.

Reddit cleanup restores every actor-13915 row, foreign parent scores and all
submission counters, with dependent rows deleted before extra comments/posts.
Magento cleanup diffs all store-0 attributes, stock, product timestamps and
reviews. This is a superset of episode timestamp/write-selected products and
study-period products, covering untimestamped bulk writes or incomplete HARs.
Only differing rows are changed inside a transaction; unchanged parents retain
their IDs. Unexpected product identity changes fail closed.

With the scheduler stopped, `python -m tools.pristine_state reconcile` repairs
both sites and repeats M5c's primary-key diff without recent-row exclusions;
`python -m tools.pristine_state check` checks the runtime scope. These commands
require the same site locks as episodes. `tools.state_reset_live` runs the two
scripted off-target proofs through real browser/proxy episodes (use background).
`supersede_drift` in `tools.supersede_episodes` requeues the exact audited IDs,
including standalone seed replicas, without overwriting prior attempts. Counts
and operational evidence are in `logs/m5d_status.md` and its linked JSON files.

M5e raises the shared allowance to $120, with five-hour/seven-day/month fractions
of 0.25/0.85/1.00. The provider reset anchor and per-model monthly caps remain
unchanged. `logs/m5e_status.md` records the smoke test and resumed sweep health.

`tools/chain_mitigation.sh` waits for the scheduler in `logs/sweep-m5-main.pid`
to exit, then requires no main rows in pending/running/budget_wait/deferred.
It checks the workspace pause, dirty markers, and pristine drift under scheduler
and site locks before launching mitigation with two workers. The detached
scheduler writes `logs/sweep-m5-mitigation.log` and its real PID to the matching
`.pid` file. The chain itself uses `logs/chain_mitigation.log` and `.pid`.
An early main exit or failed check writes `logs/chain_mitigation.blocked` and
stops; it never resumes the main sweep automatically. Review and resolve that
reason before explicitly re-arming. A chain lock prevents concurrent watchers,
and `logs/chain_mitigation.started.json` prevents repeat launches.
