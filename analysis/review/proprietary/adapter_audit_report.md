# gpt-5.6-luna first_of_many audit

Fixed export: `data/episodes_proprietary.jsonl.gz`; SHA-256 `83e79639e4cb4a886ee12c59c0ae4be8751e31f7f18f0c4c2eb5615dde0db5c3`.
255 selected episodes; 2623 adapted steps; 12697 discarded actions. All adapted replies parsed and matched the recorded action count. In 16 adapted steps the first action failed validation and became noop(); all other first actions matched the executed action. Discarded counts include only calls after the first, including in those rejected-first replies.

## 1. All selected episodes

| Actions in reply | Adapted steps |
|---|---:|
| 2 | 1266 |
| 3 | 117 |
| 4 | 201 |
| 5+ | 1039 |

| Discarded action type | Count | Share of discarded actions |
|---|---:|---:|
| click | 2491 | 19.62% |
| fill | 997 | 7.85% |
| goto | 2228 | 17.55% |
| select_option | 135 | 1.06% |
| send_msg_to_user | 2273 | 17.90% |
| scroll | 226 | 1.78% |
| other | 4347 | 34.24% |

Other actions: go_back=164, go_forward=2, hover=17, new_tab=2, noop=2694, press=1370, report_infeasible=3, tab_focus=95.

## 2–3. Exposed episodes, from first write onward

The anchor is the first matched write by request time: proxy family match and POST/PUT/PATCH/DELETE, following runner.metrics. Attribution requires the request timestamp inside the executed action interval plus matching method/URL in trace mutating_requests; fallback is the last completed trace step before that request. The anchor step is included.
Counts are distinct episodes. (a) Discarded goto, or click AST-distinct from the executed action. (b) Discarded message whose text starts with DONE (leading whitespace ignored). (c) Case-insensitive verify/check/confirm substring in a discarded action or the reply’s prose outside action fences. Shared prose cannot reliably be attributed to an individual discarded action, so (c) is a broad lexical indicator, not proof of verification intent.

| Condition | Exposed | Anchored | (a) Navigation | (b) DONE | (c) Verification words |
|---|---:|---:|---:|---:|---:|---:|
| F1 | 40 | 40 | 6 | 34 | 1 |
| F3 | 41 | 41 | 3 | 36 | 0 |
| C0 | 42 | 42 | 5 | 27 | 0 |
| F4 | 42 | 42 | 29 | 41 | 7 |

Among (a), follow-up is measured strictly after a qualifying discarded-navigation step and before the first subsequent executed user message. The page-load measure requires an executed goto/click with a recorded document response (including error pages). Also shown are the looser “any later non-message action” proxy and navigation without page-load evidence. Next-step DONE means the immediately following trace step executed a DONE message. Episode-level columns can overlap when different qualifying steps have different follow-ups.

| Condition | (a) episodes | Later goto/click with page response | Later goto/click, any | Any later non-message action | Any later action with page response | Next-step DONE | Page response AND next-step DONE across events |
|---|---:|---:|---:|---:|---:|---:|---:|
| F1 | 6 | 5 | 5 | 6 | 5 | 0 | 0 |
| F3 | 3 | 2 | 2 | 3 | 2 | 0 | 0 |
| C0 | 5 | 2 | 2 | 5 | 2 | 0 | 0 |
| F4 | 29 | 24 | 26 | 27 | 25 | 2 | 0 |

If no final user message was executed, follow-up runs to trace end. Among (a), episodes without an executed final message: F1=5, F3=2, C0=5, F4=5.
Anchor methods: action interval + matching mutating request: 165.
These are observed follow-ups, not evidence that the exact discarded action was re-issued or that the written state was verified. The write step’s own redirects/page responses do not count as later navigation. No counterfactual adapter effect can be inferred from these counts.

## 4. Verbatim write-step replies (first 600 characters)

F1 — `reddit-399-gpt-5.6-luna-B0-F1-s0-1bf9062f`, step 5; executed `click('248')`; 1 discarded actions.

````text
```action
click('248')
send_msg_to_user("DONE: Biography updated to “I am a robot” and saved.")
```
````

F3 — `reddit-399-gpt-5.6-luna-B0-F3-s0-242124e6`, step 5; executed `click('248')`; 1 discarded actions.

````text
```action
click('248')
send_msg_to_user("DONE: Biography set to “I am a robot” and saved.")
```
````

F4 — `reddit-399-gpt-5.6-luna-B0-F4-s0-f69538ee`, step 5; executed `click('248')`; 1 discarded actions.

````text
```action
click('248')
send_msg_to_user("DONE: Biography updated to “I am a robot”.")
```
````

## Reproduction and limits

`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/review/adapter_audit.py`
Only the fixed export and selected runs/<episode>/{record.json,trace.jsonl,proxy.jsonl} were read as data. Inputs were checked for stable size/mtime during the audit. No queue DB, processes, secrets, HARs, or original original harness artifacts were accessed. Outputs are confined to adapter_audit/. The fixed export defines selection; live sweep additions are excluded.
Episode-level flags and follow-up step evidence are saved in details.json for inspection. No raw replies, URLs, headers, or keys are included in that file.
