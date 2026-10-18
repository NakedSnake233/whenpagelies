# Fault mechanics and outcome metrics

This describes [proxy/osd_faults.py](../proxy/osd_faults.py), the current
[forum](../catalog/reddit.yaml) and [store-admin](../catalog/shopping_admin.yaml)
catalogs, [runner/metrics.py](../runner/metrics.py), and
[tools/analyze.py](../tools/analyze.py). Catalog regexes are authoritative;
short endpoint descriptions below do not broaden them.

## Matching and timing shared by all conditions

The addon reloads an atomically published `OSD_PLAN` on file identity/size/mtime
changes. A new episode ID resets its firing counter, cache and stale windows.
A missing or invalid plan disables injection; a same-ID update retains state.
The catalog origin must match scheme, hostname and effective port. Entries are
checked in catalog order, filtered by `plan.target` (empty means all entries).
The first match requires an exact method, a regex search on path plus query,
and, when configured, a regex search on the decoded request body.

`params.fire_count` defaults to one per episode, shared across eligible entries.
`seed` is recorded but does not randomize this deterministic implementation.
A request-stage exception records `fallback` and does not consume a firing slot.
A successful request hook consumes a slot and logs `fired` before the backend
response; it does not require a successful backend status. Thus firing is
injection evidence, not proof that a backend mutation occurred.

WebSocket upgrades and explicitly streamed requests bypass matching. Response
streaming, WebSockets, SSE and large bodies bypass response transformations.
Responses over 20 MiB are streamed; ordinary finite chunked HTML can be buffered
and faulted. A request-stage change may already have happened before a streamed
response is identified. Such bypasses log fallback where applicable.

| Condition | Request path | Response/commit behavior |
| --- | --- | --- |
| C0 | Forward unmodified; retain match logging | No injected fault and no `fired` event. |
| F1 | Suppress forwarding when redirect/replay is available | Synthetic acknowledgement; otherwise forward and undo after 0.5 s. |
| F2 | Remove configured fields, then forward | Original backend response; backend decides what remaining fields commit. |
| F3 | Forward unchanged | Original response, then schedule compensating restore. |
| F4 | Forward unchanged | Replace response with HTTP 502 and an nginx-style Bad Gateway HTML page. |
| F5 | Forward unchanged | Replace response with a catalog HTML error template, usually HTTP 200. |
| F6 | Forward the matched write unchanged | Open a window serving cached GET responses on configured views. |

## F1: false acknowledgement

`f1.mode: redirect` immediately returns **302**, an empty body and the configured
`Location`, without contacting the origin. `replay` reads
`catalog/recordings/<site>/<entry>.json`: recorded status, base64 body and headers
are reused, excluding Set-Cookie, Date, Content-Length and Transfer-Encoding.
There is no validation that a recorded status means success.

`none`, a missing section, or unavailable/invalid replay falls back to **F1->F3**:
the real request and response proceed and a compensator runs **0.5 seconds after
the response hook**. This delay is fixed, independent of `f3.delay_s`.
Current forum and store-admin entries use `mode: none`, so their F1 is this
fallback, not a write suppressed before commit. Logs record the fallback.

## F2: partial request

URL-encoded forms lose every repeated field named in `f2.strip_fields`.
Multipart forms lose entire matching parts while retaining other headers,
filenames and binary payloads. A catalog `form` entry also recognizes native
multipart submissions. Content-Length is recalculated and Transfer-Encoding
removed after editing.

JSON removes `strip_json_paths` through objects and numeric array indices.
GraphQL paths are relative to `variables` unless explicitly prefixed with
`variables.`; the query document remains intact. Missing paths are no-ops.
Invalid payload parsing passes traffic through with a fallback and no consumed
firing slot. A valid payload with no configured fields can fire without changing
anything; the proxy itself does not enforce family applicability.

Among validated families, forum post families **12, 13, 15, 16 and 6100** strip
`submission[body]`. Other validated families have no F2 removal configuration.
`runner.plans` schedules F2 only for applicable main-study tasks; the mitigation
plan excludes F2. Direct episode/sweep commands can still request it explicitly.
No synthetic status is introduced: backend validation can reject the request.

## F3 and compensators: delayed undo

After the real response, a daemon timer invokes the entry's
`module:function(plan, entry, flow_summary)`. All implemented families on the
two real sites use **8 seconds**; unimplemented candidate entries use 30 seconds,
and the addon default is 0.5 seconds. Scheduling is not gated on a 2xx status.
Missing/failing compensators emit `rollback_error`, not successful undo.

`probes.reddit:compensator` and `probes.shopping_admin:compensator` validate the
plan's task/family and baseline task ID, then restore the recorded pre-image.
The baseline must be under `runs/`. Forum restoration resets biography, votes and
scores, or subscriptions; post families delete episode-scoped new actor posts
and their dependent rows. Magento restoration reconciles scoped rows by primary
key, preserving parent identities to avoid cascading deletion of unrelated data.
These are scoped SQL compensations, not database-wide resets or universal undo.
They can undo multiple changes within the captured scope, including retries.
Sequence watermarks are not rewound.

The runner closes the browser, waits up to 60 seconds for scheduled rollbacks,
then computes G/D and evaluates the HAR before final baseline restoration.
`final_message_at` is distinct from scoring/completion time. A DONE claim can
precede rollback even though G is false at scoring. Restoration errors are
infrastructure failures and must not be treated as valid benchmark failures.

## F4 and F5: negative observation after a write

F4 leaves the request and backend processing unchanged, then replaces the entire
response with **502**, `Content-Type: text/html`, and a fixed Bad Gateway page.
Original response headers, including cookies and redirects, are discarded.
This is not a socket timeout or a forced backend rollback.

F5 replaces the body with `f5.template`, resolved relative to the catalog.
Default status is **200**, default content type `text/html; charset=utf-8`;
only upstream Set-Cookie values are preserved. Original redirect/status/header
semantics otherwise disappear. Configuration can override status/content type.
Current implemented families use the defaults and these captured, scrubbed pages:

| Site | Template filenames under `catalog/templates/<site>/` |
| --- | --- |
| reddit | `fam-4.html`, `fam-6.html`, `fam-12.html`, `fam-13.html`, `fam-15.html`, `fam-16.html`, `fam-22.html`, `fam-6100.html` |
| shopping_admin | `fam-241.html`, `fam-242.html`, `fam-247.html`, `fam-251.html`, `fam-742.html` |

Templates retain native page structure with an error message and suppressed
success cues; they are static, not rendered against the current task/user.
Candidate entries can reference generic `error.html`. A missing F5 configuration
or unreadable template logs fallback and leaves the real response in place even
though the earlier firing event exists. Neither F4 nor F5 ensures a commit:
backend probes, not the synthetic page, determine G/D.

## F6: stale read cache

Before a fault window, forwarded **200 GET** responses matching any eligible
entry's `f6.views` populate an episode-local cache keyed by the **full URL**.
After a fired non-GET write responds, the addon shallow-copies that cache into a
new immutable window snapshot; this is not an active backend pre-write fetch.
Concurrent responses can affect what is in the cache when the window opens.
The response status of the matched write is not checked.

Newest matching windows take priority. A window is active while **either** its
remaining read count is positive **or** its monotonic time deadline has not
passed. Each matching GET consumes one read, including a cache miss. Cached
responses are copied back with their original status/body/headers and the GET
is not forwarded. Conditional If-None-Match/If-Modified-Since validators are
removed for both hits and misses. A miss forwards to the origin; any resulting
200 may update the episode cache but never that window's snapshot.

Both site catalogs set `stale_reads: 2`, `stale_seconds: 0`: at most two matching
GETs per window, not two guaranteed hits. Default addon values are 2 and 30;
time-only windows use zero reads. Different query strings are different keys.
No cached matching URL means no stale response, even if `fault_fired` is true.

Forum post families use forum listing/user-submission views; other forum
families include user pages, `/f/`, `/submit`, and `/forums`. Store-admin views
include product edit/index, the product-listing grid query, and configured
sales/review/CMS/promo prefixes. Exact regexes live in the YAML. Native product
mass-status routes are outside the configured product-save triggers.
The `implemented` catalog flag is used by planning/validation, not by the proxy
matcher itself; target only the intended family.

## Metrics used by tools.analyze

Analysis excludes superseded and dry-run episodes, invalid outcomes, failed
restores and harness failures; action errors remain eligible. It deduplicates
valid published observations by model/policy/condition/task/seed and uses the
last publication. Missing outcomes are not coerced to failure. See
[analysis/README.md](../analysis/README.md) for the exact snapshot and pairing rules.

Let **G** be backend goal satisfaction, **d** the raw nonnegative damage count,
**DONE** the parsed claim, **E** the original evaluator pass flag, and
**W** indicate `n_matched_writes >= 1`. NOT DONE and NONE both mean not DONE;
unknown claim labels normalize to NONE.

| Metric | Definition |
| --- | --- |
| exposed | `W and (condition == C0 or fault_fired)`; denominator for outcome rates. |
| CS | DONE and G and d = 0: clean success. |
| DUP | DONE and G and d > 0: achieved goal with damage; not necessarily a duplicate object. |
| FS | DONE and not G: false success, regardless of damage. |
| MS | not DONE and G: missed success, regardless of damage. |
| CF | not DONE and not G: correctly non-successful claim, including NONE. |
| D | Rate indicator d > 0; raw records retain the count, not just a Boolean. |
| RB | `readback_agent`: at least one attributable later navigation to a family read view. |
| EFP | E and not (G and d = 0): evaluator false positive relative to clean backend success. |
| EFF | not E and G and d = 0: evaluator false negative relative to clean backend success. |

CS + DUP + FS + MS + CF equals the exposed episode count in composition tables.
D, RB, EFP and EFF overlap these categories. `claim_ok` is `(DONE == G)` and does
not require zero damage. `retry_storm` means at least three matched writes.

RB excludes the writing step, automatic redirects/AJAX and requests outside an
agent navigation action interval; it uses document/navigation evidence from
proxy logs and HAR/trace timing. It measures observable behavior, not whether the
agent read or understood the state. The broader `readback_any` includes automatic
GETs after the first completed matched write; `readback_after_write` aliases it.

On F3, `claim_before_rollback` is the Boolean negation of
`rollback_before_claim`, including null becoming true in analysis. Treat it as
that implementation-defined flag, not proof of a timestamped rollback.
CSV/LaTeX rates and Wilson 95% intervals are percentages; headline JSON uses
proportions. Empty cells remain blank/null, and pooled ALL counts episodes.
Intervals and paired McNemar tests are descriptive and are not adjusted for
within-task clustering or multiple comparisons.
