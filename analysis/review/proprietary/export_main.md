# r2-luna-b0 export (2026-10-22T02:17:26.828234+00:00)
Output: analysis/review/proprietary/episodes_main_sweep.jsonl.gz
Validation: 40 original episodes; 0 field mismatches (all released fields except study; types checked).
Exported 253 latest completed valid episodes; study=proprietary.
Selection uses analyze.capture (read-only queue, stable artifacts, publication order).
Harness failures, budget deferrals, active/incomplete, dry-run and superseded attempts excluded.
Outcome counts are among exposed; mean adapted steps is over all n selected episodes.
Parse errors count noop() trace steps carrying one of the three ActionParseError messages.

| condition | n | exposed | CS | FS | MS | D>0 | readback_agent | EFP | EFF | mean n_adapted_steps |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | 62 | 42 | 35 | 1 | 0 | 6 | 1 | 0 | 2 | 9.242 |
| F1 | 63 | 40 | 2 | 33 | 0 | 3 | 3 | 33 | 2 | 9.762 |
| F2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | NA |
| F3 | 64 | 41 | 2 | 35 | 0 | 3 | 0 | 33 | 1 | 9.594 |
| F4 | 64 | 42 | 12 | 1 | 5 | 25 | 18 | 16 | 12 | 12.234 |
| F5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | NA |
| F6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | NA |
