# Configuration

`models.yaml` defines gateway routing, wire formats and estimated token prices. Cached input and reasoning tokens are subsets of input/output, not additional usage. `budget.yaml` sets shared reservation limits; uncertain failed-call reservations require reconciliation with provider billing before release. Keep private credentials outside the repository as described in the top-level README.

`tasks/` and `reddit_399_actions.json` provide scripted task fixtures. The runner accepts a JSON task with id, site, start_url and intent. Dry runs still use real browser/site state.

Pristine snapshots and their machine-specific manifest are excluded. On a new dedicated installation, after starting the benchmark sites, create the baseline with `python -m tools.pristine_state capture`. This utility creates temporary isolated site containers and writes local snapshots plus `config/pristine_manifest.json`. See runner documentation for state checking and restoration. Do not capture over an existing baseline.
