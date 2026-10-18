#!/usr/bin/env python3
"""Restore one episode's scoped pre-images; never reset a whole site container."""

import argparse
import importlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from probes.db import ROOT
from probes.site_common import task_data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("baseline", type=Path)
    args = p.parse_args()
    path = args.baseline.resolve()
    if not path.is_relative_to(ROOT / "runs"):
        p.error("baseline must be inside runs/")
    b = json.loads(path.read_text())
    task = task_data(b["task_id"])
    site = task["sites"][0]
    if site not in {"reddit", "shopping_admin"}:
        p.error("unsupported site")
    importlib.import_module("probes." + site).restore(task, b)
    print("Scoped episode restore complete.")


if __name__ == "__main__":
    main()
