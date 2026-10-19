# Trace sample

This sample contains a complete cross-section of four tasks plus nine cited cases: 275 unique episodes (102 main, 199 mitigation; 26 shared by both studies).

Tasks: reddit-620 (fam-12), reddit-404 (fam-22), shopping_admin-458 (fam-247), shopping_admin-453 (fam-242).

`sample.tar.gz` expands to `sample/<episode>/`; `manifest.csv` records study membership, outcomes and selection reasons. `showcase/` contains record, agent trace and proxy logs for cited cases. FS = DONE and not G; CS = DONE and G and D = 0.

Available per-file schemas (top-level keys; JSONL keys are the union across events):
- `record.json`: D, G, browser_killed, cbr_prompted, claim, condition, cost, details, dry_run, episode, error, error_kind, eval_detail, eval_pass, extra_llm_calls, family, fault_fired, final_message, final_message_at, final_message_observed_at, fired_step, fired_time, har, model, n_done_refused, n_matched_writes, n_readbacks, n_readbacks_agent, n_side_effecting, n_verifications, policy, policy_cost, proxy_port, readback_after_write, readback_agent, readback_any, recovered_episode, restore_ok, rollback_before_claim, seconds, seed, site, started_at, steps, task, tokens, worker.
- `trace.jsonl`: action, action_llm_call, action_started_at, ax_chars, cost, document_responses, ended_at, error, expected_effect, extra_llm_calls, finish_reason, first_write_ts, lenient_parse, main_response_status, matched_writes, mutating_requests, native_format, oracle, policy_event, prompt_chars, prompt_tokens, raw_model_text, seconds, side_effecting, started_at, step, url, usage, verification_report.
- `proxy.jsonl`: document_navigation, fault_applied, flow_id, forwarded, matched_entry, method, redirect_url, req_body_sha256, request_ts, resp_bytes, status, ts, url.
- `eval.json`: error_msg, evaluators_results, intent_template_id, score, sites, status, task_id, task_revision, webarena_verified_data_checksum, webarena_verified_evaluator_checksum, webarena_verified_version.
- `baseline.json`: affected_ids, episode_started_at, forum_id, forum_name, rows, scopes, site, submission_id, target_ids, task_id, user_id, watermarks.
- `network.har`: log.

HARs use the release exporter’s metadata-only scrubber: request/response headers and cookies are emptied, postData and content text are dropped, query values are redacted, and URL credentials and fragments are removed.
Proxy URLs point to local test sites on 127.0.0.1. All other files are copied byte-for-byte from the run directories.
Missing optional files: 0; see `missing-files.csv` for the complete list.

The full trace archive will be released after review. Its current raw directory size is 8,343,202,644 bytes (apparent size via du -sb over 4,109 unique episode directories: 1,631 main rows plus 2,832 mitigation rows, deduplicated).

The sample archive is 20,841,175 bytes. It is reproducible: sorted members, zero modification times, numeric owner/group zero, fixed modes and a zero gzip timestamp.
