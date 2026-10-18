"""Load catalogs with contextual validation errors before processing traffic."""
from dataclasses import dataclass
import math
from pathlib import Path
import re
from urllib.parse import urlsplit
import yaml

from proxy.plan import SAFE_ID


class CatalogError(ValueError):
    pass


def origin_key(url):
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port or (443 if parts.scheme == "https" else 80)


@dataclass
class Catalog:
    site: str
    origin: str
    entries: list[dict]
    directory: Path


def load_catalog(path) -> Catalog:
    path = Path(path)
    def fail(where, message):
        raise CatalogError(f"{path.name}: {where}: {message}")

    def regex(value, where):
        if not isinstance(value, str):
            fail(where, "expected regex string")
        try:
            re.compile(value)
        except re.error:
            fail(where, "invalid regular expression")

    def strings(value, where):
        if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
            fail(where, "expected list of nonempty strings")

    def number(value, where, integer=False):
        if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or value < 0:
            fail(where, "expected finite nonnegative " + ("integer" if integer else "number"))

    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise CatalogError(f"{path.name}: cannot load YAML ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        fail("catalog", "expected mapping")
    site = data.get("site")
    if not isinstance(site, str) or not SAFE_ID.fullmatch(site):
        fail("site", "expected safe identifier")
    origin = data.get("origin")
    try:
        parts = urlsplit(origin) if isinstance(origin, str) else None
        valid = (parts and parts.scheme in ("http", "https") and parts.hostname and parts.port
                 and not parts.username and not parts.password and not parts.path and not parts.query and not parts.fragment)
    except ValueError:
        valid = False
    if not valid:
        fail("origin", "expected scheme://host:port without path or credentials")
    entries = data.get("entries")
    if not isinstance(entries, list):
        fail("entries", "expected list")
    seen = set()
    for i, entry in enumerate(entries):
        where = f"entries[{i}]"
        if not isinstance(entry, dict):
            fail(where, "expected mapping")
        eid = entry.get("id")
        if not isinstance(eid, str) or not SAFE_ID.fullmatch(eid) or eid in seen:
            fail(where + ".id", "expected unique safe identifier")
        seen.add(eid)
        where = eid
        if not isinstance(entry.get("method"), str) or not re.fullmatch(r"[A-Z]+", entry["method"]):
            fail(where + ".method", "expected uppercase HTTP method")
        regex(entry.get("url_regex"), where + ".url_regex")
        if "body_regex" in entry:
            regex(entry["body_regex"], where + ".body_regex")
        if not isinstance(entry.get("content_type"), str) or entry["content_type"] not in {"form", "json", "multipart", "graphql"}:
            fail(where + ".content_type", "expected form, json, multipart, or graphql")
        if not isinstance(entry.get("effect"), str) or entry["effect"] not in {"create", "update", "delete", "toggle"}:
            fail(where + ".effect", "expected create, update, delete, or toggle")
        for key in ("f1", "f2", "f3", "f5", "f6"):
            if key in entry and not isinstance(entry[key], dict):
                fail(where + "." + key, "expected mapping")
        f1 = entry.get("f1", {"mode": "none"})
        if not isinstance(f1.get("mode"), str) or f1["mode"] not in {"none", "replay", "redirect"}:
            fail(where + ".f1.mode", "expected none, replay, or redirect")
        if f1.get("mode") == "redirect" and (not isinstance(f1.get("location"), str) or not f1["location"] or any(c in f1["location"] for c in "\r\n")):
            fail(where + ".f1.location", "redirect requires a valid location")
        for key in ("strip_fields", "strip_json_paths"):
            strings(entry.get("f2", {}).get(key, []), where + ".f2." + key)
        f3 = entry.get("f3", {})
        number(f3.get("delay_s", 0.5), where + ".f3.delay_s")
        if "compensator" in f3 and (not isinstance(f3["compensator"], str) or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", f3["compensator"])):
            fail(where + ".f3.compensator", "expected module:function")
        f5 = entry.get("f5")
        if f5 is not None:
            if not isinstance(f5.get("template"), str) or not (path.parent / f5["template"]).is_file():
                fail(where + ".f5.template", "expected existing file relative to catalog")
            if type(f5.get("status", 200)) is not int or not 100 <= f5.get("status", 200) <= 599:
                fail(where + ".f5.status", "expected HTTP status 100..599")
            if not isinstance(f5.get("content_type", "text/html; charset=utf-8"), str):
                fail(where + ".f5.content_type", "expected string")
        f6 = entry.get("f6", {})
        strings(f6.get("views", []), where + ".f6.views")
        for view in f6.get("views", []):
            regex(view, where + ".f6.views")
        number(f6.get("stale_reads", 2), where + ".f6.stale_reads", integer=True)
        number(f6.get("stale_seconds", 30), where + ".f6.stale_seconds")
    return Catalog(site, origin, entries, path.parent)
