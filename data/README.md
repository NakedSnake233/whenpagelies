# Analysis-only episode data

One JSON object per selected valid episode. capture() and derive() from the source analysis select the latest valid attempt per model/policy/condition/task/seed; superseded, dry, invalid, deferred and active attempts are excluded. All captured analysis CSVs were reproduced exactly before export. Main includes indexed main B0; mitigation uses the mitigation model and shared non-skipped mitigation design cells, including main B0 baselines. Thus baseline rows overlap the files. Unexposed valid episodes are retained for ITT. No free text, URLs, filesystem paths, prompts, messages or request data are included.

Missing measurements are null, never imputed as zero. Flags are booleans; counts and measurements are numeric. Token dictionaries contain only the numeric keys listed below.

| Field | Meaning |
| --- | --- |
| `episode` | Published episode identifier; opaque ID, not a path. |
| `study` | main or mitigation; B0 baseline episodes may occur in both files. |
| `model` | Gateway model identifier. |
| `policy` | B0/B1/B2/VRV, VRV ablation, or Oracle. |
| `condition` | C0 control or F1-F6 fault assignment. |
| `seed` | Integer run seed. |
| `task` | Task ID from the indexed study (not a filename). |
| `family` | Catalog task family ID. |
| `site` | Benchmark site ID. |
| `claim` | DONE, NOT DONE, or NONE (normalized by derive). |
| `G` | Backend goal satisfaction boolean. |
| `D` | Nonnegative backend extra-side-effect count. |
| `eval_pass` | Original upstream evaluator boolean. |
| `exposed` | At least one matched write and either C0 or a fired fault. |
| `eligible` | At least one catalog-matched write. |
| `fired` | Whether a fault fired. |
| `n_matched_writes` | Number of catalog-matched writes. |
| `readback_agent` | Agent-initiated saved-state readback boolean. |
| `n_readbacks_agent` | Number of agent-initiated saved-state readbacks. |
| `FS` | DONE with G=false. |
| `MS` | Not DONE with G=true. |
| `CS` | DONE with G=true and D=0. |
| `DUP` | DONE with G=true and D>0. |
| `CF` | Not DONE with G=false. |
| `EFP` | Evaluator passes although not (G=true and D=0). |
| `EFF` | Evaluator fails although G=true and D=0. |
| `harm` | FS or MS or D>0. |
| `steps` | Recorded agent step count. |
| `tokens` | Numeric input/output/cached_input/reasoning token counters, when recorded; cached/reasoning are subsets. |
| `cost` | Stored estimated episode cost in USD, including auxiliary calls; not an invoice. |
| `seconds` | Recorded episode duration in seconds. |
| `n_verifications` | Accepted policy verification count. |
| `n_done_refused` | Policy refusals of DONE. |
| `cbr_prompted` | Check-before-retry reminder count. |
| `extra_llm_calls` | Auxiliary policy LLM call count. |
| `policy_cost` | Auxiliary policy estimated cost in USD (already included in cost). |

`episodes_proprietary.jsonl.gz`: gpt-5.6-luna B0 replication, C0/F1/F3/F4 on 64 tasks, 21–22 Oct 2026; 255/256 episodes. Adds `n_adapted_steps` and `n_parse_error_steps`; protocol, exclusions, costs, guard evidence and reproduction commands are in [the review README](../analysis/review/README.md#proprietary-model-replication-gpt-56-luna).

`episodes_r3.jsonl.gz`: 826 round-3 episodes, 22–23 Oct 2026: MiMo-V2.6-Flash B0/VRV mitigation replication (472, including 8 transport-failure retries), DeepSeek-V4.1-Flash B0 F3s2/F3s30 (128), VRV F3s2/F3/F3s30 (177), and a budget-limited B0 C0/F1/F3/F4 repeat (49/256). Adds `claim_before_rollback` and `rollback_delay_s`; conditions F3s2/F3s30 roll back 2 s/30 s after the write response. Same harness copy, sites, catalog and resets; pristine drift 0 before/after. Costs, guard evidence and reproduction are in [the review README](../analysis/review/README.md#round-3-second-mitigation-model-rollback-delays-repeat-runs).

`episodes_r3b.jsonl.gz`: 207 remaining DeepSeek-V4.1-Flash B0 repeat cells (seed 1, C0/F1/F3/F4), run 24 Oct 2026 through a second OpenAI-compatible provider after the first gateway exhausted its quota; includes four successful retries. Estimated exported-episode cost: USD 6.52412571. See `analysis/review/README.md` and `analysis/review/round3b/`.
