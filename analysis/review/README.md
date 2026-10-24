# Review analyses

These analyses were added during the paper's internal review. They are recomputed from the per-episode tables in `data/`, and they back Appendices A–F of the paper.

| File | Content |
|---|---|
| `episode_flags.csv` | One row per selected episode (4,109 unique). Columns: the start time in UTC; `in_window`, which marks forum episodes that started between 2026-10-10 11:04 and 2026-10-11 06:56 UTC, before the global reset, with leftover forum subscriptions; `rerun_selected`; `n_delete_req` and `mentions_delete` (forum deletion requests and mentions); and `prefix_len`/`prefix_sha1` (the length and hash of the action sequence up to the first task write). |
| `prefix_groups.csv` | Per (model, task) in the main study: whether the pre-write prefixes are identical across conditions. |
| `job1.md` | How the flags were computed. |
| `robustness.json`, `robustness.md` | Results of `tools/review/robustness.py`. |
| `mitigation_itt.json` | Exposure by policy and the 21 policy-vs-B0 comparisons over all episodes, exposed or not (`tools/review/mitigation_itt.py`) |
| `rb_audit.csv`, `rb_audit_result.md` | Manual audit of the read-back label on 60 episodes from `traces/sample.tar.gz`, built by `tools/review/rb_audit_dump.py`. |
| `judge/*.jsonl`, `judge_summary.json` | LLM-judge verdicts for the 843 exposed B0 episodes of the main study under C0/F1/F3/F4/F5 (`tools/review/llm_judge.py`), and their summary (`tools/review/judge_analysis.py`). |

## Reproduce

```
python tools/review/robustness.py      # needs numpy, scipy, statsmodels
python tools/review/judge_analysis.py  # standard library only
```

The robustness script runs these analyses:
- exposure balance across conditions (Cochran's Q);
- paired tests with a task-clustered bootstrap and the cluster-adjusted McNemar test (Durkalski et al., 2003);
- Holm adjustment over the 21 policy-vs-B0 tests;
- GEE models;
- leave-one-family-out rates;
- step-budget exhaustion;
- per-model rates;
- sensitivity to the start-state contamination.

Re-running the judge needs the full run directories, which are released after review, and gateway access:

```
python -m tools.review.llm_judge --repo . --runs <runs> --episodes data/episodes_main.jsonl.gz --out analysis/review/judge --model gpt-5.6-luna --variant standard
```

The judge sees each episode's task, its per-step URL, the agent's reasoning and action, the page status, and the final message. It does not see screenshots or the backend.

## Proprietary-model replication (gpt-5.6-luna)

B0 baseline agent, C0/F1/F3/F4 on all 64 tasks, run 21–22 Oct 2026 from a separate copy of the harness with the same sites, catalog and resets. An exact pristine-state drift check guarded both main and retry runs before and after: drift 0 each time. The export contains 255/256 episodes: shopping_admin-454/C0 was excluded after the browser crashed in all four attempts; shopping_admin-782 C0/F1 were each retried once after gateway transport errors.

The Luna-only first-of-many adapter adapted 62.0% of steps (2623/4233); replay of 300 recorded replies from the other models was unchanged. There were 35 parse-error steps. Cost was USD 8.45 for exported episodes, USD 9.31 including smoke tests and failed attempts. Outputs and historical guard evidence are in `proprietary/`; the episode table is `data/episodes_proprietary.jsonl.gz`.

Run from the repository root (analysis needs numpy/scipy). The released table suffices for `proprietary.md` and `proprietary_raw.json`:

```sh
mkdir -p analysis/review/proprietary
WPL_DATA=data WPL_PROP=data/episodes_proprietary.jsonl.gz WPL_OUT=analysis/review/proprietary python tools/review/proprietary.py > analysis/review/proprietary/proprietary.md
```

The remaining commands require the archived completed artifacts and queue/budget databases in `runs/`. Export reports include historical snapshot times; compressed bytes may differ on regeneration. The two exports have disjoint task/condition/seed keys; merge their rows sorted by task and condition. To reproduce the released gzip bytes, omit the gzip filename header, set mtime to zero, and write UTF-8 bytes directly (a text wrapper adds a flush boundary):

```sh
python -m tools.export_r2 --sweep r2-luna-b0 --out analysis/review/proprietary/episodes_main_sweep.jsonl.gz --report analysis/review/proprietary/export_main.md --validate --original-runs runs
python -m tools.export_r2 --sweep r2-luna-b0-retry --out analysis/review/proprietary/episodes_retry_sweep.jsonl.gz --report analysis/review/proprietary/export_retry.md
python - <<'PY'
import gzip, json
from pathlib import Path
out = Path('analysis/review/proprietary')
rows = []
for name in ('episodes_main_sweep.jsonl.gz', 'episodes_retry_sweep.jsonl.gz'):
    with gzip.open(out / name, 'rt') as f:
        rows.extend(json.loads(line) for line in f)
assert len(rows) == len({(r['task'], r['condition'], r['seed']) for r in rows}) == 255
rows.sort(key=lambda r: (r['task'], r['condition']))
with open('data/episodes_proprietary.jsonl.gz', 'wb') as raw:
    with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as f:
        for row in rows:
            f.write((json.dumps(row, sort_keys=True, allow_nan=False) + '\n').encode('utf-8'))
PY
python tools/review/adapter_audit.py --export data/episodes_proprietary.jsonl.gz --out-dir analysis/review/proprietary
mv analysis/review/proprietary/report.md analysis/review/proprietary/adapter_audit_report.md
mv analysis/review/proprietary/details.json analysis/review/proprietary/adapter_audit_details.json
python tools/review/adapter_anchor_done.py > analysis/review/proprietary/anchor_done.txt
python tools/review/adapter_stats.py > analysis/review/proprietary/adapter_stats.txt
mkdir -p logs .tmp
python -m tools.replay_luna_adapter --old-parser .tmp/pre_luna_agent.py
```

Replay requires the saved pre-adapter parser and 75 recorded replies for each of the four other models. `adapter_stats.txt` retains only sections (a), (c), (d), (f).

The five `r2_guard*.json` / `r2_pristine*.log` files are historical execution evidence, not derivable from episode tables. The isolated-run guard recorded child status and ran `python -m tools.pristine_state check` immediately before and after each sweep. Those checks require the original site snapshots and live sites; no live-site checks are needed for offline analysis. Main logs were archived with `_main` suffixes before retry. Guard JSON retains its original relative log references; the retry's before-check status is in `r2_guard.json`.

## Round 3: second mitigation model, rollback delays, repeat runs

Run 22–23 Oct 2026 using the same harness copy, sites, catalog and resets. MiMo-V2.6-Flash B0 and VRV covered the 59 mitigation tasks under C0/F1/F3/F4: 472 exported episodes, including 8 successful transport-failure retries. DeepSeek-V4.1-Flash B0 covered F3 delays of 2 s and 30 s on 64 tasks; VRV covered 2, 8 and 30 s on 59 tasks. A DeepSeek-V4.1-Flash B0 repeat under C0/F1/F3/F4 stopped at the local budget limit after 49 of 256 episodes. The operational workspace allowance was USD 135; that local `config/budget.yaml` change is not part of this release.

F3s2 and F3s30 roll back 2 s and 30 s after the write response (F3 remains 8 s). Exports add `claim_before_rollback` and `rollback_delay_s`, alongside the adapter/parse-error counters, and support `--model` and `--policy` filters. The main-study claim-timing supplement is in `round3/main_f3_claim_timing.json`.

Exported-episode costs (sum of `cost`, USD; estimates, not invoices):

| Model | Policy | Episodes | Cost |
|---|---|---:|---:|
| DeepSeek-V4.1-Flash | B0 | 177 | 4.748280 |
| DeepSeek-V4.1-Flash | VRV | 177 | 6.046460 |
| MiMo-V2.6-Flash | B0 | 236 | 2.278230 |
| MiMo-V2.6-Flash | VRV | 236 | 3.855615 |

The 826 episodes total USD 16.928584. Guard evidence for the live fault check, smoke run and round-3 run is in `round3/`: pristine drift was 0 before and after each. The live fault check exited with status 1 because one assertion, the shop probe under F3s2, cannot finish within the 2 s rollback window; the rollback timings recorded in the round-3 export confirm that the fault fired as scheduled. Archived guard references are repository-relative and process IDs are omitted. Export reports describe component exports before merging; their output paths identify historical intermediate files. `round3/stop_watcher.md` records the budget stop without raw process information.

Reproduce from the repository root (numpy/scipy required):

```sh
mkdir -p analysis/review/round3
PYTHONDONTWRITEBYTECODE=1 WPL_DATA=data WPL_R3=data/episodes_r3.jsonl.gz WPL_OUT=analysis/review/round3 python tools/review/round3.py > analysis/review/round3/round3.md
```

`round3/diagnostics.md` reports parse-error rates and composition (DeepSeek-V4.1-Flash replies failed to parse far more often in round 3, mostly as native tool-call markup under VRV and as empty replies under B0) and the timing of VRV’s last read-back, last verification report and DONE claim relative to the rollback. Reproduce with `python tools/review/round3_diagnostics.py --runs-round3 <dir> --runs-original <dir>` (requires the full run directories).


## Round 3b: DeepSeek repeat through a second provider

On 24 Oct 2026, the 207 remaining repeat cells (deepseek-v4.1-flash, B0, seed 1, C0/F1/F3/F4) ran through a second OpenAI-compatible provider serving the same model identifier because the first gateway's quota was exhausted. Selection used `OSD_PROVIDER=lmuai`, with per-model keys from a local env file (not included). The provider probe summary is `round3b/probe.md`; matching request identifiers do not independently verify backend model identity. Across the 207 episodes, all 3,177 LLM calls requested and were answered under the identifier deepseek-v4.1-flash (per-episode llm_models.jsonl); the probe in round3b/probe.md predates the switch from the alias deepseek-flash to that identifier.

Accounting used a separate local ledger, `runs/budget_lmuai.sqlite`, with USD 15 workspace and per-model caps, all window fractions 1.0, and a UTC month anchor at day 1, 00:00:00. The local `config/budget.yaml` changes remain excluded. To reproduce provider runs, add a `providers.lmuai` section locally with `workspace_monthly_usd: 15`, `per_model_monthly_usd: 15`, `provider_month_anchor: {day: 1, hour: 0, minute: 0, second: 0}` and `fractions: {five_hours: 1.0, seven_days: 1.0, month: 1.0}`. Prices are estimates at 2× the first gateway's list prices; the 207 exported episodes sum to USD 6.52412571 (`cost`), not an invoice or total ledger spend.

Four cells were rerun: two browser crashes and two transport failures. The merged export contains 203 repeat episodes and four successful retries. Smoke, repeat and retry pristine checks reported drift 0 on both sites. The smoke guard and pristine files were overwritten and no separate archive is available; `round3b/smoke_checks.md` records the contemporaneous diagnosis. The repeat's archived guard and before/after logs are included. The retry's guard wrapper ended early; its unchanged running-state guard, before-check log and manual post-check are included, with the recovery note. The manual check confirms final pristine state but does not restore guard coverage for the unguarded interval. Generic guard log references are historical repository-relative paths; process IDs are omitted.

Reproduce the analysis from the repository root (numpy/scipy required):

```sh
mkdir -p analysis/review/round3b
PYTHONDONTWRITEBYTECODE=1 python tools/review/repeat_ds.py > analysis/review/round3b/repeat_ds.md
```
