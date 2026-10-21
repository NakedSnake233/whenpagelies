# Review analyses

These analyses were added during the paper's internal review. They are recomputed from the per-episode tables in `data/`, and they back Appendices A–F of the paper.

| File | Content |
|---|---|
| `episode_flags.csv` | One row per selected episode (4,109 unique). Columns: the start time in UTC; `in_window`, which marks forum episodes that started between 2026-10-10 11:04 and 2026-10-11 06:56 UTC, before the global reset, with leftover forum subscriptions; `rerun_selected`; `n_delete_req` and `mentions_delete` (forum deletion requests and mentions); and `prefix_len`/`prefix_sha1` (the length and hash of the action sequence up to the first task write). |
| `prefix_groups.csv` | Per (model, task) in the main study: whether the pre-write prefixes are identical across conditions. |
| `job1.md` | How the flags were computed. |
| `robustness.json`, `robustness.md` | Results of `tools/review/robustness.py`. |
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
