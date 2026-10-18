"""Offline checks for catalog coverage, family matching and reset boundaries."""

import json
from pathlib import Path
import re
from urllib.parse import urlencode
import pytest
from proxy.catalog import load_catalog
from probes.site_common import task_data, mutations

ROOT = Path(__file__).resolve().parents[1]


def test_family_counts_and_task_join():
    tasks = json.loads((ROOT / "catalog/site_tasks.json").read_text())
    candidates = json.loads((ROOT / "tasks/webarena_candidates.json").read_text())
    expected = {
        t["task_id"]
        for t in candidates
        if t["mutation"] and t["sites"] in [["reddit"], ["shopping_admin"]]
    }
    assert {t["task_id"] for t in tasks} == expected
    assert len({(t["sites"][0], t["intent_template_id"]) for t in tasks}) == 35


@pytest.mark.parametrize("site", ["reddit", "shopping_admin"])
def test_catalog_all_families_and_matchers(site):
    catalog = load_catalog(ROOT / f"catalog/{site}.yaml")
    tasks = json.loads((ROOT / "catalog/site_tasks.json").read_text())
    for t in tasks:
        if t["sites"] != [site]:
            continue
        entry = next(
            e for e in catalog.entries if e["id"] == f"fam-{t['intent_template_id']}"
        )
        assert t["task_id"] in entry["task_ids"]
        assert entry["f1"]["mode"] == "none" and entry["f3"]["delay_s"] == (8 if entry["implemented"] else 30)
        assert entry["f6"]["stale_reads"] == 2 and entry["f6"]["stale_seconds"] == 0
        for e in mutations(t):
            urls = e["url"] if isinstance(e["url"], list) else [e["url"]]
            for u in urls:
                # Materialize representative URLs from the dataset's regex syntax.
                u = (
                    u.replace("__REDDIT__", "")
                    .replace("__SHOPPING_ADMIN__", "/admin")
                    .removeprefix("^")
                    .removesuffix("$")
                )
                u = u.replace("(simple|configurable)", "simple").replace(r"\d+", "4")
                # Other regex-heavy unimplemented entries are validated by load_catalog.
                if not any(x in u for x in ["(", ".*", "["]):
                    assert re.search(entry["url_regex"], u), (entry["id"], u)
            if e.get("post_data") and "body_regex" in entry:
                assert re.search(entry["body_regex"], urlencode(e["post_data"])), entry["id"]
                if entry.get("implemented"):
                    multipart = ''.join(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in e['post_data'].items())
                    assert re.search(entry["body_regex"], multipart), entry['id']


def test_compensator_rejects_cross_task_baseline(tmp_path):
    from probes.site_common import compensate
    from proxy.plan import Plan

    called = []
    p = Plan(
        "test",
        "reddit",
        "F1",
        0,
        [],
        {"task_id": 400, "baseline_path": str(tmp_path / "baseline.json")},
    )
    with pytest.raises(ValueError):
        compensate(p, {"id": "fam-22"}, lambda *args: called.append(args))
    assert not called


def test_unsupported_probes_fail_explicitly():
    from probes import reddit, shopping_admin

    with pytest.raises(NotImplementedError):
        reddit.baseline(580)
    with pytest.raises(NotImplementedError):
        shopping_admin.baseline(694)


@pytest.mark.parametrize(
    "price,goal,extra", [("62.10", True, 0), ("55.89", False, 1), ("50.30", False, 2)]
)
def test_percentage_price_overapplication(monkeypatch, price, goal, extra):
    from probes import shopping_admin as probe

    def row(value):
        return {"entity_id": 237, "attribute_id": 77, "store_id": 0, "value": value}

    before = {
        "task_id": 459,
        "scopes": {"catalog_product_entity_decimal": "entity_id=237"},
        "rows": {"catalog_product_entity_decimal": [row("69.00")]},
    }
    monkeypatch.setattr(probe, "query", lambda *args: [[row(price)]])
    result = probe.evaluate(459, before)
    assert result["G"] is goal and result["D"] == extra


def test_templates_do_not_embed_live_session_values():
    from bs4 import BeautifulSoup
    from urllib.parse import parse_qsl, urlsplit

    for path in (ROOT / "catalog/templates").glob("*/*.html"):
        soup = BeautifulSoup(path.read_text(), "html.parser")
        assert not soup.find_all("script"), path.name
        assert all(not e.get("value") for e in soup.find_all("input")), path.name
        for element in soup.find_all(True):
            for attr in ("href", "src", "action"):
                if not element.has_attr(attr):
                    continue
                keys = [k for k, _ in parse_qsl(urlsplit(element[attr]).query)]
                assert not any(
                    re.search(r"token|csrf|auth|session|^key$|form_key", k, re.I)
                    for k in keys
                ), path.name
