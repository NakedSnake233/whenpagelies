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
