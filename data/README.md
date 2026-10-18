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
