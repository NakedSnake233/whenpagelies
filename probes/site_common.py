"""Task lookup and episode snapshot persistence shared by real-site probes."""

import json
from pathlib import Path
from probes.db import ROOT


def task_data(task):
    if isinstance(task, dict) and "intent_template_id" in task:
        return task
    tid = task.get("task_id") if isinstance(task, dict) else task
    data = next(
        t
        for t in json.loads((ROOT / "catalog/site_tasks.json").read_text())
        if t["task_id"] == int(tid)
    )
    if isinstance(task, dict):
        data.update(task)
    return data


def mutations(task):
    return [
        e["expected"]
        for e in task["eval"]
        if e["evaluator"] == "NetworkEventEvaluator"
        and e["expected"].get("http_method") in {"POST", "PUT", "PATCH", "DELETE"}
    ]


def persist(task, baseline, episode_dir=None):
    directory = episode_dir or task.get("episode_dir")
    if directory:
        p = Path(directory).resolve()
        if not p.is_relative_to(ROOT / "runs"):
            raise ValueError("episode dir must be inside runs/")
        p.mkdir(parents=True, exist_ok=True)
        (p / "baseline.json").write_text(json.dumps(baseline, indent=2) + "\n")
    return baseline


def compensate(plan, entry, restore):
    task = task_data(plan.params["task_id"])
    if entry["id"] != f"fam-{task['intent_template_id']}":
        raise ValueError("task/family mismatch")
    path = Path(plan.params["baseline_path"]).resolve()
    if not path.is_relative_to(ROOT / "runs"):
        raise ValueError("baseline must be inside runs/")
    b = json.loads(path.read_text())
    if b["task_id"] != task["task_id"]:
        raise ValueError("baseline/task mismatch")
    restore(task, b)


def timed(function):
    """Record end-to-end probe latency, including all DB round trips."""
    from functools import wraps
    import time
    from probes.db import DEADLINE

    @wraps(function)
    def wrapped(*args, **kwargs):
        start = time.monotonic()
        token = DEADLINE.set(min(DEADLINE.get() or float("inf"), start + 2.8))
        try:
            return function(*args, **kwargs)
        finally:
            DEADLINE.reset(token)
            path = ROOT / "logs/m1b_call_timings.jsonl"
            path.parent.mkdir(exist_ok=True)
            with path.open("a") as out:
                out.write(
                    json.dumps(
                        {
                            "site": function.__module__.split(".")[-1],
                            "call": function.__name__,
                            "seconds": time.monotonic() - start,
                        }
                    )
                    + "\n"
                )

    return wrapped
