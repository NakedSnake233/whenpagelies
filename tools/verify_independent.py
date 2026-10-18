#!/usr/bin/env python3
"""Independent M5 verification; reads only the six explicitly allowed inputs.

No analysis code is imported. JSONL append order defines latest: exclusions
precede replacement, so an excluded later attempt cannot hide a valid one.
Queue/history episode membership establishes m5-main provenance; queue status
does not disqualify a historical eligible attempt. All SQLite access is read-only.
"""

import csv
import hashlib
import io
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METRICS = ("CS", "FS", "MS", "D", "EFP", "EFF")
COMPOSITION = ("n_exposed", "CS", "DUP", "FS", "MS", "CF")
HARNESS_KINDS = {"infrastructure", "dirty_site", "harness", "harness_error"}


def task_id(path):
    """reddit_399.json -> reddit-399; retain shopping_admin's underscore."""
    stem = Path(path).stem
    site, number = stem.rsplit("_", 1)
    return site + "-" + number


def read_queue(path, allowed):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        rows = connection.execute(
            "SELECT id,name,task,model,policy,condition,seed,status,episode_id "
            "FROM queue WHERE name='m5-main' AND policy='B0' AND seed=0 "
            "ORDER BY id"
        ).fetchall()
        history = connection.execute(
            "SELECT h.queue_id,h.episode_id,h.timestamp FROM history h "
            "JOIN queue q ON q.id=h.queue_id "
            "WHERE q.name='m5-main' AND q.policy='B0' AND q.seed=0 "
            "ORDER BY h.queue_id,h.timestamp,h.episode_id"
        ).fetchall()
    finally:
        connection.close()
    rows = [r for r in rows if task_id(r[2]) in allowed]
    keys = {r[0]: (r[3], task_id(r[2]), r[5]) for r in rows}
    history = [r for r in history if r[0] in keys]
    episodes = {}
    for queue_id, episode in [(r[0], r[8]) for r in rows] + [
        (r[0], r[1]) for r in history
    ]:
        if episode:
            if episode in episodes and episodes[episode] != keys[queue_id]:
                raise ValueError("Episode belongs to conflicting queue cells")
            episodes[episode] = keys[queue_id]
    digest = hashlib.sha256(json.dumps([rows, history]).encode()).hexdigest()
    return episodes, digest


def json_lines(data):
    for line in data.decode("utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def select_records(records, episodes, superseded):
    latest = {}
    excluded = Counter()
    for record in records:
        episode = record["episode"]
        if episode not in episodes:
            continue
        key = (record["model"], record["task"], record["condition"])
        if key != episodes[episode] or record["policy"] != "B0" or record["seed"] != 0:
            raise ValueError("Record metadata disagrees with queue provenance")
        if episode in superseded:
            excluded["superseded"] += 1
            continue
        kind = record.get("error_kind")
        if kind == "budget" or record.get("budget_deferred"):
            excluded["budget"] += 1
            continue
        if kind in HARNESS_KINDS or record.get("harness_error"):
            excluded["harness"] += 1
            continue
        if kind or record.get("error"):
            # Do not print errors, which may contain sensitive provider replies.
            raise ValueError("Unclassified record error; independent classification required")
        if record.get("dry_run"):
            raise ValueError("Dry-run record unexpectedly belongs to main queue")
        if record["G"] is not True and record["G"] is not False:
            raise ValueError("Eligible record lacks Boolean G")
        if type(record["D"]) is not int or record["D"] < 0:
            raise ValueError("Eligible record lacks nonnegative integer D")
        if record["claim"] not in {"DONE", "NOT DONE", "NONE"}:
            raise ValueError("Unrecognized claim field")
        if key in latest:
            excluded["older_eligible"] += 1
        latest[key] = record
    return latest, excluded


def summarize(latest):
    counts = defaultdict(Counter)
    for r in latest.values():
        c = counts[r["condition"]]
        c["n"] += 1
        if not (r["n_matched_writes"] >= 1 and
                (r["condition"] == "C0" or r["fault_fired"])):
            continue
        if type(r["eval_pass"]) is not bool:
            raise ValueError("Exposed record lacks Boolean eval_pass")
        c["n_exposed"] += 1
        done, g, d, e = r["claim"] == "DONE", r["G"], r["D"], r["eval_pass"]
        outcomes = {
            "CS": done and g and d == 0,
            "FS": done and not g,
            "MS": not done and g,
            "D": d > 0,
            "EFP": e and not (g and d == 0),
            "EFF": not e and g and d == 0,
            "DUP": done and g and d > 0,
            "CF": not done and not g,
        }
        for name, value in outcomes.items():
            c[name] += int(value)
    return counts


def matches(actual, reported, percentage=False):
    try:
        reference = Decimal(reported)
    except (InvalidOperation, TypeError):
        return False
    if not reference.is_finite():
        return False
    # Accept exactly half a unit in the last printed decimal place, including ties.
    tolerance = Decimal(5).scaleb(reference.as_tuple().exponent - 1) if percentage else 0
    return abs(actual - reference) <= tolerance


def compare(counts, rows, composition=False):
    by_condition = defaultdict(list)
    for row in rows:
        if row["model"] == "ALL" and (not composition or row["policy"] == "B0"):
            by_condition[row["condition"]].append(row)
    failures = defaultdict(list)
    checked = 0
    for condition in sorted(set(counts) | set(by_condition)):
        if condition not in counts:
            failures[condition].append("unexpected CSV condition")
            continue
        if len(by_condition[condition]) != 1:
            failures[condition].append(f"expected one CSV row; found {len(by_condition[condition])}")
            continue
        c, row = counts[condition], by_condition[condition][0]
        for metric in COMPOSITION if composition else ("n", "n_exposed") + METRICS:
            percentage = not composition and metric in METRICS
            checked += 1
            if percentage and not c["n_exposed"]:
                failures[condition].append(f"{metric}: percentage undefined (no exposures)")
                continue
            actual = Decimal(c[metric])
            if percentage:
                actual = actual * 100 / Decimal(c["n_exposed"])
            reported = row.get(metric, "<missing>")
            if not matches(actual, reported, percentage):
                failures[condition].append(f"{metric}: independent={actual}, CSV={reported}")
    return failures, checked


def main():
    paths = ["tasks/INDEX.md", "runs/records.jsonl", "runs/superseded.jsonl",
             "analysis/main_by_model_condition.csv", "analysis/outcome_composition.csv"]
    payloads = {name: (ROOT / name).read_bytes() for name in paths}
    allowed = {task_id(p) for p in re.findall(
        r"\]\(([^)]+\.json)\)", payloads[paths[0]].decode("utf-8"))}
    if not allowed:
        raise ValueError("INDEX contains no task links")
    episodes, queue_hash = read_queue(ROOT / "runs/m5_queue.sqlite", allowed)
    superseded = {r["episode_id"] for r in json_lines(payloads[paths[2]])}
    latest, excluded = select_records(json_lines(payloads[paths[1]]), episodes, superseded)
    if not latest:
        raise ValueError("No eligible records selected")
    counts = summarize(latest)
    comparisons = []
    for name, composition in [(paths[3], False), (paths[4], True)]:
        rows = list(csv.DictReader(io.StringIO(payloads[name].decode("utf-8"))))
        failures, checked = compare(counts, rows, composition)
        comparisons.append((name, failures, checked))
    changed = [name for name in paths if (ROOT / name).read_bytes() != payloads[name]]
    _, queue_hash_after = read_queue(ROOT / "runs/m5_queue.sqlite", allowed)
    if queue_hash != queue_hash_after:
        changed.append("runs/m5_queue.sqlite (selected queue/history projection)")
    lines = [
        "# Independent M5 verification",
        f"UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "Scope: m5-main; B0; seed 0; INDEX tasks only; all models pooled.",
        "Provenance: queue + history episode IDs; SQLite mode=ro/query_only; runs and sweep unchanged.",
        "Latest = JSONL append order after excluding superseded, budget, infrastructure/dirty-site/harness errors.",
        f"Eligible: {len(latest)}; exposed: {sum(c['n_exposed'] for c in counts.values())}; INDEX tasks: {len(allowed)}; exclusions: {dict(excluded)}.",
        "Exposure: n_matched_writes >= 1 and (C0 or fault_fired). Claim uses only record['claim'] == 'DONE'.",
        "CS=DONE & G & D==0; FS=DONE & !G; MS=!DONE & G; D counts D>0.",
        "EFP=eval_pass & !(G & D==0); EFF=!eval_pass & G & D==0. All outcomes use exposed episodes.",
        "Main cells: independent count (independent % / CSV %); n and n_exposed: independent / CSV.",
        "",
        "| Condition | n | n_exposed | CS | FS | MS | D | EFP | EFF | Main | Composition |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    main_rows = {r['condition']: r for r in csv.DictReader(io.StringIO(payloads[paths[3]].decode())) if r['model'] == 'ALL'}
    for condition, c in sorted(counts.items()):
        row = main_rows.get(condition, {})
        cells = [condition] + [f"{c[k]} / {row.get(k, 'missing')}" for k in ("n", "n_exposed")]
        cells += [f"{c[k]} ({100*c[k]/c['n_exposed']:.1f} / {row.get(k, 'missing')})"
                  if c['n_exposed'] else f"{c[k]} (undefined / {row.get(k, 'missing')})" for k in METRICS]
        cells += ["MISMATCH" if condition in failures else "match" for _, failures, _ in comparisons]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("Composition: exact counts for n_exposed, CS, DUP (DONE & G & D>0), FS, MS, CF (!DONE & !G).")
    lines.append("Main: counts exact; percentages tolerate half the last printed decimal unit (0.05 percentage points here).")
    for name, failures, checked in comparisons:
        lines.append(f"{Path(name).name}: {checked} numeric cells compared; " +
                     ("; ".join(f"{c}: {', '.join(items)}" for c, items in sorted(failures.items())) or "no mismatches."))
    lines.append("Input stability: " + ("CHANGED during verification: " + ", ".join(changed) if changed else "all permitted inputs stable during verification."))
    for name, data in payloads.items():
        lines.append(f"SHA256 {name}: `{hashlib.sha256(data).hexdigest()}`")
    lines.append(f"SHA256 selected queue/history projection: `{queue_hash}`")
    ok = not changed and all(not failures for _, failures, _ in comparisons)
    lines.append("Verdict: " + ("PASS — both CSVs match the independent recomputation beyond rounding." if ok else "FAIL — mismatches or changing inputs reported above."))
    if len(lines) > 40:
        raise ValueError("Report exceeds 40 lines")
    destination = ROOT / "logs/verify_independent.md"
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[-1])
    print(f"Report: {destination.relative_to(ROOT)} ({len(lines)} lines)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
