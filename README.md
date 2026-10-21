# OSD-Bench

OSD-Bench measures observation-state divergence: a browser agent's visible acknowledgement, error or stale page can disagree with saved backend state. An HTTP fault proxy injects six faults (F1–F6) and a C0 control. A catalog matches eligible writes and saved-state views; independent backend probes measure goal satisfaction (G) and extra side effects (D). The runner also records the WebArena-Verified evaluator verdict.

The indexed study covers 64 tasks in 13 families on the forum (`reddit`, Postmill) and store administration (`shopping_admin`, Magento). See [tasks/INDEX.md](tasks/INDEX.md) for inclusion and [docs/FAULTS.md](docs/FAULTS.md) for fault and outcome definitions.

## Repository layout

- `proxy/`, `catalog/`: fault injection, endpoint matching, saved-state views and templates.
- `probes/`, `tasks/`: independent backend evaluation, scoped restoration and task definitions.
- `agent/`: browser agent, gateway client and verification policies.
- `runner/`, `config/`, `infra/`: episodes, sweep scheduling, model/budget configuration and site setup.
- `tools/`, `tests/`: analysis, maintenance utilities and tests.
- `analysis/`: final paper tables in CSV/LaTeX plus definitions and summaries.
- `data/`: compressed analysis-only episode rows and a complete field dictionary.

## Setup

Use Linux, Docker, Git, Bash, curl and Python 3.11. Site images, browser dependencies and extracted image layers require substantial disk space. The site scripts use WebArena-Verified images; upstream sources and licenses are in [THIRD_PARTY.md](THIRD_PARTY.md). Commands below run from the repository root, in a dedicated benchmark environment.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
bash infra/pull_images.sh
bash infra/start_site.sh reddit
bash infra/start_site.sh shopping_admin
bash infra/wait_health.sh reddit
bash infra/wait_health.sh shopping_admin
bash infra/check_autologin.sh reddit
bash infra/check_autologin.sh shopping_admin
.venv/bin/python -m tools.pristine_state capture
```

Install Playwright's required OS libraries if unavailable. [infra/sites.yaml](infra/sites.yaml) defines ports; site scripts check ports before starting containers. Public benchmark auto-login credentials belong to the benchmark images. Pristine database snapshots are excluded; initialization and restoration tools create local state under `data/` as needed. See [runner/README.md](runner/README.md) for restoration and worker ownership.

## LLM gateway configuration

[config/models.yaml](config/models.yaml) defines the gateway base URL, model identifiers, wire formats and estimated prices. [config/budget.yaml](config/budget.yaml) sets spending limits. API usage requires a private key loaded by [tools/secrets.py](tools/secrets.py) from `~/.config/osd-bench/opencode.env`, outside the repository:

```text
OPENCODE_API_KEY=<your-private-key>
```

Create that file privately with owner-only permissions. Never commit it or place the key in command arguments or logs. An existing `OPENCODE_API_KEY` environment variable takes precedence. The gateway supports chat, messages and responses formats; enable models appropriate to your account. Stored USD costs are client estimates, including configured cache discounts, rather than provider invoices.

## Episodes and sweeps

With healthy sites and the gateway configured, run one model episode:

```sh
.venv/bin/python -m runner.run_episode \
  --task tasks/reddit_400.json --model deepseek-v4.1-flash \
  --policy B0 --condition F3 --seed 0 --max-steps 30
```

`--dry-run` selects scripted actions without model calls, but still uses the sites, browser and proxy. Episodes modify site state, then perform scoped restoration; dirty-site markers require recovery before continuing.

Create and run a separate sweep:

```sh
.venv/bin/python -m runner.sweep --db runs/demo_queue.sqlite add --name demo \
  --tasks tasks/reddit_400.json tasks/shopping_admin_453.json \
  --models deepseek-v4.1-flash --policies B0 VRV --conditions C0 F3 --seeds 0
.venv/bin/python -m runner.sweep --db runs/demo_queue.sqlite run --name demo --workers 2
.venv/bin/python -m runner.sweep --db runs/demo_queue.sqlite status --name demo
```

Each episode uses site and worker locks. Concurrent queues share budgets and site state; use the scheduler's recovery and resume procedures. Local outputs go to `runs/` and `logs/`.

## Policies

B0 is the base agent; B1 adds a verification instruction; B2 adds reflection after each action. **VRV** creates obligations after catalog-matched writes, generates expected postconditions, requires fresh agent-initiated saved-state reads and verification reports, performs a final audit before DONE, and supplies a check-before-retry reminder after errors. The ablations `VRV-modeltrigger`, `VRV-samepage` and `VRV-nocbr` respectively replace enforced triggers with instructions, permit the current page, and remove the retry reminder. Oracle supplies privileged backend G/D information. Only Oracle sees backend truth; VRV accepts model verification reports and does not guarantee correct interpretation.

## Paper results

[analysis/README.md](analysis/README.md) defines populations, denominators and units. CSV and matching LaTeX files map to the paper as follows:

| Paper component | Files in `analysis/` |
| --- | --- |
| Main outcomes | `main_by_model_condition.csv`, `outcome_composition.csv` |
| Mitigation | `mitigation.csv`, `cost_mitigation.csv`, `mitigation_paired_by_group.csv` |
| RQ1–RQ3 additional results | `readback.csv`, `writes.csv`, `exposure.csv`, `main_itt*.csv`, `family_condition.csv` |

[data/README.md](data/README.md) documents the released episode rows, including unexposed episodes for ITT and overlapping B0 baselines in the mitigation design. Invalid and superseded attempts are excluded. The supplied tables are final archived outputs. To analyze original-format runs, `python -m tools.analyze` expects `runs/m5_queue.sqlite`, named studies `m5-main` and `m5-mitigation`, aggregate records and closed episode artifacts; it writes `analysis/`. The example demo queue requires adapting study selection. The compact episode files omit raw evidence and failed-attempt counters, so cannot alone reconstruct every provenance counter.

## Traces

See [traces/README.md](traces/README.md) for the trace sample, manifest and full archive release plan.

Apart from the sample in traces/, full traces are excluded from this release. The compact episode data in data/ contain only analysis fields, without prompts, model text, URLs, paths, request headers, HARs or screenshots.

## Review analyses

Robustness analyses, validity flags, a read-back audit and LLM-judge results added during review are in [analysis/review/](analysis/review/README.md).

## Tests and limitations

Pure unit tests can run without sites:

```sh
.venv/bin/python -m pytest -q tests/test_analyze.py tests/test_agent_parse.py \
  tests/test_action_adapter.py tests/test_agent_policies.py tests/test_proxy_unit.py
```

Other tests may start services or need live sites. Endpoint-specific matching can miss valid alternative workflows, F2 support depends on family, and F1 uses a rollback fallback on the validated sites. Readback does not establish comprehension. Scoped restoration does not rewind sequence counters. Dependencies and images are not fully pinned. Original code is MIT licensed; see [LICENSE](LICENSE) and third-party notices.
