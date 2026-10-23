"""Validated, atomically published per-worker fault plans."""
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile


# None preserves the catalog delay for the historical F3 condition.
F3_DELAYS = {"F3": None, "F3s2": 2.0, "F3s30": 30.0}
CONDITIONS = ("C0", *(f"F{i}" for i in range(1, 7)), "F3s2", "F3s30")
FAULTS = set(CONDITIONS)


def rollback_delay(condition, entry=None):
    if condition == "F1":
        return 0.5
    fixed = F3_DELAYS.get(condition)
    return fixed if fixed is not None else (entry or {}).get("f3", {}).get("delay_s", 0.5)


def rollback_timeout(condition, entry=None):
    return max(60, rollback_delay(condition, entry) + 30)
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


@dataclass
class Plan:
    episode_id: str
    site: str
    fault: str
    seed: int
    target: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def __post_init__(self):
        for name in ("episode_id", "site"):
            value = getattr(self, name)
            if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
                raise ValueError(f"plan.{name}: expected a safe nonempty identifier")
        if not isinstance(self.fault, str) or self.fault not in FAULTS:
            raise ValueError("plan.fault: expected C0, F1-F6, F3s2 or F3s30")
        if type(self.seed) is not int:
            raise ValueError("plan.seed: expected integer")
        if not isinstance(self.target, list) or not all(isinstance(x, str) for x in self.target):
            raise ValueError("plan.target: expected list of entry IDs")
        if not isinstance(self.params, dict):
            raise ValueError("plan.params: expected mapping")
        count = self.params.get("fire_count", 1)
        if type(count) is not int or count < 0:
            raise ValueError("plan.params.fire_count: expected nonnegative integer")


def write_plan(path, plan: Plan):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(asdict(plan), stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_plan(path) -> Plan | None:
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("plan: invalid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("plan: expected JSON object")
    try:
        return Plan(**data)
    except TypeError as exc:
        raise ValueError("plan: missing or unknown fields") from exc
