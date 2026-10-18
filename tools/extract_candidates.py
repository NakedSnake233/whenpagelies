#!/usr/bin/env python3
"""Extract network-mutation candidates without importing environment managers.

Run with .venv/bin/python tools/extract_candidates.py. start_url retains the
dataset's start_urls list so multi-site entry points are not lost. POST reads
(including GraphQL) and should_not_exist assertions remain candidates under
the requested HTTP-method rule; evaluator configs preserve these distinctions.
"""

import argparse
import json
from collections import Counter
from importlib.metadata import distribution
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITES = {"shopping_admin", "reddit", "shopping", "gitlab"}
METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def mutation_reasons(config):
    expected = config.get("expected", {})
    reasons = []
    if expected.get("event_type", config.get("event_type")) == "modification":
        reasons.append("event_type=modification")
    method = expected.get("http_method", expected.get("method", ""))
    if str(method).upper() in METHODS:
        reasons.append(f"http_method={str(method).upper()}")
    return reasons


def extract(task):
    mutations = []
    for index, config in enumerate(task["eval"]):
        if config["evaluator"] != "NetworkEventEvaluator":
            continue
        reasons = mutation_reasons(config)
        if not reasons:
            continue
        expected = config["expected"]
        urls = expected.get("url", [])
        mutations.append({
            "evaluator_index": index,
            "reasons": reasons,
            "expected_url_patterns": urls if isinstance(urls, list) else [urls],
            "expected_query_params": expected.get("query_params"),
            "expected_post_params": expected.get("post_data"),
            "config": config,
        })
    return {
        "task_id": task["task_id"],
        "sites": task["sites"],
        "intent": task["intent"],
        "start_url": task["start_urls"],
        "evaluator_names": [config["evaluator"] for config in task["eval"]],
        "mutation": bool(mutations),
        "mutation_evaluators": mutations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "tasks/webarena_candidates.json")
    args = parser.parse_args()
    source = args.dataset or Path(distribution("webarena-verified").locate_file(
        "webarena_verified/assets/dataset/webarena-verified.json"))
    tasks = json.loads(source.read_text())
    candidates = [extract(t) for t in tasks if set(t["sites"]) <= SITES]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(candidates, indent=2, ensure_ascii=False) + "\n")

    mutations = [t for t in candidates if t["mutation"]]
    singles = Counter(t["sites"][0] for t in mutations if len(t["sites"]) == 1)
    multis = Counter(" + ".join(sorted(t["sites"])) for t in mutations if len(t["sites"]) > 1)
    example = next(t for t in tasks if t["sites"] == ["shopping_admin"] and
                   any(e["evaluator"] == "NetworkEventEvaluator" and mutation_reasons(e)
                       for e in t["eval"]))
    print(f"Dataset: {source.resolve()}\nTotal tasks: {len(tasks)}")
    print("Task keys: " + ", ".join(tasks[0]))
    print(f"Allowed-site tasks: {len(candidates)}; mutation candidates: {len(mutations)}")
    print("start_url preserves the original start_urls list and unresolved site placeholders.")
    print("Mutation rule: modification event type or POST/PUT/PATCH/DELETE; no URL heuristic needed.")
    print("Caveat: POST reads and should_not_exist assertions also qualify under this rule.")
    print("Single-site mutation counts:")
    for site in sorted(SITES):
        print(f"  {site}: {singles[site]}")
    print(f"Multi-site mutation count: {sum(multis.values())}")
    for sites, count in sorted(multis.items()):
        print(f"  {sites}: {count}")
    for site in ("shopping_admin", "reddit", "shopping"):
        print(f"\n{site}: 8 example mutation intents")
        for task in [t for t in mutations if t["sites"] == [site]][:8]:
            print(f"  {task['task_id']}: {task['intent']}")
    print(f"\nTask {example['task_id']} evaluator config (complete eval list):")
    print(json.dumps(example["eval"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
