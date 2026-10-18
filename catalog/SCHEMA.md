# Fault catalog schema

Each `<site>.yaml` contains `site`, `origin`, and an ordered `entries` list.
`site` and entry `id` values are safe identifiers (letters, digits, `.`, `_`, `-`,
starting with a letter or digit); entry IDs must be unique. `origin` is an explicit
`http://host:port` or `https://host:port`, with no path, query, or credentials.
Origins compare by scheme, case-insensitive hostname, and effective port.
`proxy.catalog.load_catalog(path)` validates the file and raises `CatalogError`
with the filename and affected field. Unknown extension keys are allowed.

Entry fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable entry ID used in plan targets and recordings. |
| `method` | Uppercase HTTP method. |
| `url_regex` | Python regex searched against path plus query, excluding origin. |
| `body_regex` | Optional regex searched against mitmproxy's decoded request text. |
| `content_type` | `form`, `json`, `multipart`, or `graphql`; governs F2 decoding. |
| `effect` | `create`, `update`, `delete`, or `toggle`. |
| `f1` | `{mode: replay\|redirect\|none, location?: string}`; default `none`. Redirect requires `location`. |
| `f2` | `{strip_fields: [string], strip_json_paths: [dotted.path]}`; lists default empty. |
| `f3` | `{delay_s: number, compensator: 'module:function'}`; delay defaults to 0.5 seconds. |
| `f5` | `{template: path, status: integer, content_type: string}`; defaults: status 200, type `text/html; charset=utf-8`. Template must exist, resolved relative to the YAML directory (absolute paths also accepted). |
| `f6` | `{views: [regex], stale_reads: integer, stale_seconds: number}`; defaults: empty views, 2 reads, 30 seconds. |

Fault sections are optional: entries only need to configure supported behaviors.
F3 and F1 fallback need an actual compensator to undo the write. A missing or
failing compensator produces `rollback_error`; it cannot undo backend state.
F5 requires its template section when selected. Numbers must be finite and
nonnegative; counts must be integers. Regexes are validated at load time.

F2 removes all repeated form fields bearing a configured name. Multipart edits
preserve the retained parts, their upload filenames, headers and binary bytes.
JSON paths traverse objects and numeric array indices; missing paths are no-ops.
For GraphQL, paths are relative to `variables` unless prefixed with `variables.`.
The GraphQL query document is preserved. Invalid payloads pass through with a
`fallback` event, without consuming the firing budget.

F6 takes a pre-write snapshot of full-URL-keyed 200 GET responses matching the
selected entries' views. After a matched write responds, stale reads apply while
**either** the read budget remains **or** the time window is open. Use
`stale_seconds: 0` for exactly N stale reads, or `stale_reads: 0` for time only.
Each matching request consumes a read, including cache misses. A miss forwards
with conditional validators removed and emits `stale_miss`; a later miss does
not substitute a post-write response into that window's snapshot. Caches are
per worker and episode, not per cookie or user. Use one browser identity per
worker. Query strings are part of the cache key.

# Worker plan and runtime

`proxy.plan.Plan` contains `episode_id`, `site`, `fault` (`C0`, `F1`–`F6`),
`seed`, `target` (eligible entry IDs; empty means all), and `params` (a mapping).
`fire_count` is a nonnegative integer defaulting to 1. Real-site compensators
also require `task_id` and `baseline_path` (inside `runs/`). Seed is retained for reproducibility; current faults have no random choices.
The first matching entry in catalog order wins. C0 never consumes a firing.
`write_plan` publishes JSON by fsync plus atomic rename; `read_plan` returns
`None` for a missing file and raises `ValueError` for invalid content.

The proxy checks plan mtime (nanosecond precision), size and inode on each
request. A new episode clears counters and stale caches and changes the log
folder. Rewriting the same episode keeps its state. Changing site requires a
new episode. In-flight requests and delayed rollback logs retain their original
episode. Invalid plans/catalogs disable injection until the next plan change.
Catalog edits take effect when a plan is republished. With no active plan,
traffic passes through and there is no episode log directory to write to.

Environment:

- `OSD_PLAN`: plan JSON path (default `plan.json`).
- `OSD_CATALOG_DIR`: YAML and recordings directory (default `catalog/`).
- `OSD_RUNS_DIR`: episode output directory (default `runs/`).
- `OSD_RECORD=1`: write matched real responses to
  `recordings/<site>/<entry_id>.json` beneath the catalog directory. Recordings
  contain `status`, `headers` as a list of pairs, and base64 `body`; Set-Cookie,
  Date and Content-Length are excluded. Use a C0 calibration pass to capture
  suitable acknowledgements. Generated recordings are ignored by Git.

Run from the repository root with `.venv/bin/mitmdump --mode regular
--listen-port <port> --set confdir=.cache/mitmproxy -s proxy/osd_faults.py` and
`OSD_PLAN` pointing to a published plan. Keep local HTTP clients on
`trust_env=False`; clear inherited HTTP(S)/ALL proxy variables in local test
subprocesses and set `NO_PROXY=127.0.0.1,localhost`.

Each completed/error flow writes one `runs/<episode_id>/proxy.jsonl` record:
`ts` (completion Unix seconds), `request_ts` (request-start Unix seconds), `method`, `url`, `status` (null on transport failure),
`matched_entry`, `fault_applied` (null for pass-through), `forwarded`,
`req_body_sha256` (original wire body), and `resp_bytes` (final buffered wire
body size; streamed body bytes are not counted). `forwarded` means the proxy
attempted upstream forwarding, not proof of backend receipt.

Events in `proxy_events.jsonl` carry `event`, `ts`, `episode_id`,
`matched_entry`, and `fault_applied`: `fired`, `rollback_scheduled`,
`rollback_done`, `rollback_error`, `stale_served`, `stale_miss`, or `fallback`.
Exceptions are recorded by type only; request headers and exception/reply
bodies are never included in diagnostic events. F1 fallback uses `F1->F3` and
a fixed 0.5-second rollback delay.

Compensators are trusted local Python functions taking `(plan, entry,
flow_summary)` on daemon background threads. The summary contains method, URL,
origin, status, redirect location and original request-body hash. Compensators
must use direct backend access, bounded timeouts, and `trust_env=False` for
local httpx traffic. They should implement operation-specific undo. Threads
are not persisted across proxy shutdown. `probes.toy:rollback_last` is only
safe for the sequential toy workload, not concurrent production writes.

WebSocket upgrades and explicitly streamed flows bypass injection. SSE
(`text/event-stream`) and bodies exceeding 20 MiB are streamed automatically;
response rewriting, rollback scheduling, recording and stale caching are skipped
for those replies. A write already selected in the request hook can consume its
firing budget before its response is discovered to be streaming. Streaming
HTTP/2 responses without an SSE type require mitmproxy's streaming configuration.

Ordinary HTTP/1 chunked responses are buffered and support every fault.
The addon configures mitmproxy `stream_large_bodies=20m` when unset, including
unknown-length responses. Real-site family inventory is in `FAMILIES.md`;
`implemented: false` entries are metadata only and have no usable compensator.

## Real-site probe and reset contract

`probes.reddit` implements families 6, 22, 4, 15, 12, 13, 16 and 6100;
`probes.shopping_admin` implements 242, 247, 241, 742 and 251. `baseline(task, episode_dir=None)` accepts a full
joined task, a candidate with `task_id`, or a numeric task ID. Passing an episode
directory (or `task['episode_dir']`) saves `baseline.json` below `runs/`.
`evaluate(task, baseline)` returns backend `G`, integer `D`, and evidence;
`restore(task, baseline)` is idempotent. Unsupported families fail explicitly.

The PostgreSQL client reads DATABASE_URL inside osd-reddit; the PDO client reads
Magento env.php inside osd-shopping_admin. Credentials never leave the container or appear in stdout, shell arguments,
or errors. Calls share a 2.8-second deadline across subprocess and DB round trips. End-to-end measurements are in `logs/m1b_call_timings.jsonl`.

Postmill user snapshots contain only ID and biography, never password hashes.
Votes restore the vote rows and affected score/ranking; subscriptions use scoped
UUID pre-images. Post creation saves the actor/forum pre-images and numeric ID
watermarks, then removes new rows and their votes/flairs/notifications on undo.
The image's submission sequence lags MAX(id), so set difference against the
scoped pre-images also detects newly created low IDs. Sequences are never reset.
No full Reddit table is dumped. Magento expands the target products to their configurable parents, siblings
and descendants, then captures scoped rows in existing EAV, stock, relation
and index tables plus auto-increment watermarks. Restore
leaves unchanged tables alone, keeps product parent IDs in place, and replaces
changed scoped child rows transactionally.

Use one writer per episode user and affected product/forum scope. These probes
are not a merge mechanism for concurrent human writes to the same rows. DB undo
does not clear sessions, rewrite application flash messages, or rewind global
sequences. A proxy must remain alive through its F3 timer. The site proof suite
serializes each site's tests, terminates its own proxy before final cleanup, and
checks both repeated restore and exact equality of the scoped pre-images.

Manual recovery: `.venv/bin/python infra/reset_site.py runs/<episode>/baseline.json`.
Reddit restore refuses to write until `logs/m2_status.md` exists. No container
lifecycle operations or full-site resets are part of this command.

The F6 catalog includes Magento `/admin/mui/index/render/`; grid proofs send the
native AJAX/Accept headers and warm the exact filtered URL. Grid prices are
formatted currency strings. Created Reddit post URLs cannot be warmed before
their ID exists, so family 15 stales its existing forum listing/user-submissions
views while allowing the newly created post's confirmation page through.

M3 uses an 8-second F3 delay for all seven implemented families; F1 remains
0.5 seconds. Episode runners share `runs/locks/m1b-<site>.lock` with site tests,
wait for rollback terminal events, and verify exact scoped rows after restore.

M3 native-browser compatibility: implemented field matchers accept both URL-encoded
field names and multipart Content-Disposition names. F2 detects multipart form
headers even when the logical catalog content_type is form, preserving boundaries
and unrelated fields. HAR sanitization retains original task fields, redacts form
authentication/CSRF fields, and keeps only evaluator-required JSON response keys.


## M1c per-task admission

`tools/validate_tasks.py` exercises each implemented task's own evaluator-resolved
form fields through a browser and the local proxy with C0. Multi-product tasks
perform every expected write. It records a real browser HAR, strips credentials
and CSRF tokens, and invokes the installed, unmodified WebArena-Verified evaluator
with a DONE claim. No LLM client is constructed or called. Browser requests are
restricted to the local benchmark sites.

The validator holds `runs/locks/m1b-<site>.lock` from baseline through browser close,
proxy shutdown, two restores and exact scoped-row verification. A failed restore
writes the normal site dirty marker and stops the batch. Only G=true, D=0,
restore_ok=true and eval_pass=true tasks are admitted to `tasks/INDEX.md`.
`logs/task_validation.md` includes failures and reasons; task JSONs for exclusions
remain as reproducible input, not admitted study cases. Per-task evidence is in
`runs/m1c-validation-*/` (baseline, proxy traffic, scrubbed HAR, eval.json,
validation.json). URL spellings in the upstream evaluator are never repaired to
make a case pass.

New Reddit post families use the evaluator's concrete forum ID and resolve its
native slug from the backend. All new actor posts in that forum count toward D;
created posts and their dependent votes/flairs/notifications are removed on undo.
The four new families each require title, body and forum; F2 strips the body.
New Magento families reuse full scoped product-save snapshots: family 742 checks
every selected price and counts over-application across targets, and family 251
compares the store-0 short_description EAV value (attribute 76) with task text.
Both have a single task-required field per write, so F2 does not apply. Family
251's assignment retry is idempotent (D=0); family 742's relative retry has D>=1.
F1/F3 retain M3's 0.5/8-second delays and F6 serves two stale reads.
