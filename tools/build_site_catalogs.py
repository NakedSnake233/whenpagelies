"""Reproducible family inventory joined to the installed benchmark dataset."""

import json
from collections import defaultdict
from importlib.metadata import distribution
from pathlib import Path
import re
from urllib.parse import quote_plus
import yaml

ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTED = {6, 22, 4, 15, 242, 247, 241}
# Goal and physical scope; all goals quantify over every evaluator target for a task.
SEMANTICS = {
    6: (
        "users.biography = content",
        "users (biography only)",
        "0; assignment is idempotent",
    ),
    22: (
        "episode user has upvote=true on newest submission in forum",
        "submission_votes; submissions score/ranking",
        "max(0, matching votes - 1); unique vote is idempotent",
    ),
    23: (
        "reply text = content_description under selected parent",
        "comments; comment_votes; submissions counters; notifications",
        "extra matching comments beyond one",
    ),
    4: (
        "episode user subscribed to forum",
        "forum_subscriptions",
        "max(0, matching subscriptions - 1); subscription is idempotent",
    ),
    15: (
        "post title = category + recommendations?; body contains category and price; forum = forum",
        "submissions; submission_votes; notifications",
        "extra matching posts beyond one",
    ),
    242: (
        "status attribute = 2 for product",
        "catalog_product_entity; product EAV; stock and index rows",
        "0; assignment is idempotent",
    ),
    247: (
        "price = baseline price adjusted by action and amount",
        "catalog_product_entity; product EAV; stock and index rows",
        "number of extra relative applications inferred from price",
    ),
    241: (
        "each selected SKU quantity = evaluator quantity resolved from arrival_phrase",
        "catalog_product_entity; product EAV; stock and index rows",
        "extra relative stock increments beyond requested quantity",
    ),
    251: (
        "description = review-count-dependent requested text for product",
        "catalog_product_entity_text",
        "0; assignment is idempotent",
    ),
    257: (
        "order id state/status is canceled",
        "sales_order; sales_order_item; sales_order_status_history; inventory",
        "extra history rows / repeated stock refunds",
    ),
    275: (
        "CMS title for old_heading = heading",
        "cms_page",
        "0; assignment is idempotent",
    ),
    280: (
        "latest pending order for name has customer-visible history message",
        "sales_order; sales_order_status_history",
        "extra matching history messages",
    ),
    284: (
        "order shipment has service and tracking",
        "sales_shipment; sales_shipment_track; sales_order",
        "extra shipment/tracking rows",
    ),
    287: (
        "all variants of product have is_in_stock=0",
        "cataloginventory_stock_item; cataloginventory_stock_status",
        "0; assignment is idempotent",
    ),
    240: (
        "order_id address fields equal parsed address",
        "sales_order_address",
        "0; assignment is idempotent",
    ),
    252: (
        "product has requested option_spec for base_setting",
        "catalog_product_entity; EAV; catalog_product_super_link; catalog_product_relation",
        "extra child SKUs or option links",
    ),
    7: (
        "forum name/title=name; description=description; sidebar=sidebar_list",
        "forums; moderators; forum_subscriptions",
        "extra matching forums",
    ),
    3765: (
        "submission title = question in relevant forum",
        "submissions; submission_votes",
        "extra matching posts",
    ),
    5: (
        "submission contains question in forum relevant to topic",
        "submissions; submission_votes",
        "extra matching posts",
    ),
    9: (
        "post title=book in books AND child comment=content",
        "submissions; comments; votes; notifications",
        "extra matching posts/comments",
    ),
    11: (
        "reposted image URL of content, title=from /f/pics, forum=forum",
        "submissions; submission_votes; images",
        "extra matching posts",
    ),
    12: (
        "title=issue; body=Please help; forum=relationships",
        "submissions; submission_votes",
        "extra matching posts",
    ),
    13: (
        "title=topic; body=your opinion; relevant forum",
        "submissions; submission_votes",
        "extra matching posts",
    ),
    6100: (
        "title/body express category and price; relevant forum",
        "submissions; submission_votes",
        "extra matching posts",
    ),
    16: (
        "title=interest + Meet up!; body includes interest and date; forum=forum",
        "submissions; submission_votes",
        "extra matching posts",
    ),
    19: (
        "title=title in forum",
        "submissions; submission_votes",
        "extra matching posts",
    ),
    256: (
        "new simple SKU has product, stock, size, color and price",
        "catalog_product_entity; product EAV; stock; indexes",
        "extra matching SKUs",
    ),
    258: (
        "salesrule name=topic and conditions/actions implement rule",
        "salesrule; salesrule_*",
        "extra matching rules",
    ),
    24: (
        "top k posts in forum have user downvote",
        "submission_votes; submissions",
        "extra votes beyond selected set",
    ),
    25: (
        "all posts by user in forum have episode-user upvote",
        "submission_votes; submissions",
        "extra votes beyond selected set",
    ),
    1510: (
        "all posts by user in forum have episode-user downvote",
        "submission_votes; submissions",
        "extra votes beyond selected set",
    ),
    27: (
        "selected post body = old body plus content, title preserved",
        "submissions",
        "extra copies of appended content",
    ),
    243: (
        "all qualifying reviews status_id=1",
        "review; review_detail; review_store; review_entity_summary",
        "0; assignment is idempotent",
    ),
    246: (
        "all reviews selected by review_type absent",
        "review; review_detail; review_store; rating_option_vote",
        "extra non-target rows deleted",
    ),
    742: (
        "all config-matched prices adjusted by action and amount",
        "catalog_product_entity; product EAV; stock and indexes",
        "extra relative applications inferred from prices",
    ),
}


def main():
    candidates = json.loads((ROOT / "tasks/webarena_candidates.json").read_text())
    ids = {
        t["task_id"]
        for t in candidates
        if t["mutation"] and t["sites"] in [["reddit"], ["shopping_admin"]]
    }
    source = Path(
        distribution("webarena-verified").locate_file(
            "webarena_verified/assets/dataset/webarena-verified.json"
        )
    )
    tasks = [t for t in json.loads(source.read_text()) if t["task_id"] in ids]
    (ROOT / "catalog/site_tasks.json").write_text(
        json.dumps(tasks, indent=2, ensure_ascii=False) + "\n"
    )
    groups = defaultdict(list)
    for t in tasks:
        groups[(t["sites"][0], t["intent_template_id"])].append(t)
    rows = [
        "# Real-site mutation families",
        "",
        "Generated by `tools/build_site_catalogs.py` from the candidate IDs and installed dataset. G uses instantiation_dict and resolved evaluator targets. Counts include multi-write tasks. Unimplemented families have catalog metadata but their probes deliberately raise NotImplementedError.",
        "",
        "F1/F3 use commit + scoped compensating restore at 0.5/8 seconds for implemented families. F4/F5 apply to all. F6 applies to all listed read views. F2 applies only where one write has at least two required data fields; CSRF tokens do not count.",
        "",
        "| Site / family | Tasks | Implemented | Goal G | Side effect D | Tables | Read views | F2 strip field |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    for site in ["reddit", "shopping_admin"]:
        entries = []
        for (s, fam), ts in groups.items():
            if s != site:
                continue
            patterns = []
            fields = []
            all_fields = set()
            method = "POST"
            for t in ts:
                for e in t["eval"]:
                    if e["evaluator"] != "NetworkEventEvaluator":
                        continue
                    ex = e.get("expected", {})
                    m = ex.get("http_method", "GET")
                    if m not in ["POST", "PUT", "PATCH", "DELETE"]:
                        continue
                    method = m
                    urls = ex.get("url", [])
                    urls = [urls] if isinstance(urls, str) else urls
                    for u in urls:
                        p = u.replace("__REDDIT__", "").replace(
                            "__SHOPPING_ADMIN__", "/admin"
                        )
                        # Evaluator strips Magento's admin base and optional trailing slash/query.
                        p = p.removesuffix("$").rstrip("/") + r"/?(?:\?.*)?$"
                        if not p.startswith("^"):
                            p = "^" + p
                        patterns.append(p)
                    post = ex.get("post_data") or {}
                    all_fields.update(post)
                    if len(post) >= 2:
                        fields.append(list(post))
            strip = next((k for fs in fields for k in fs if k.endswith("[body]")), None)
            if strip is None and fields:
                strip = fields[0][-1]
            views = (
                [r"^/user/[^/]+(?:/|$)", r"^/f/", r"^/submit", r"^/forums"]
                if site == "reddit"
                else [
                    r"^/admin/catalog/product/(?:edit|index)/",
                    r"^/admin/mui/index/render/\?(?:[^#]*&)?namespace=product_listing(?:&|$)",
                    r"^/admin/(?:sales|review|cms|promo)/",
                ]
            )
            if fam == 15:
                views = [
                    r"^/f/[^/]+(?:/(?:new|hot|top))?/?(?:\?.*)?$",
                    r"^/user/[^/]+/submissions",
                ]
            goal, tables, d = SEMANTICS[fam]
            entry = dict(
                id=f"fam-{fam}",
                method=method,
                url_regex="(?:" + "|".join(dict.fromkeys(patterns)) + ")",
                content_type="form",
                effect="create" if "extra matching posts" in d else "update",
                f1={"mode": "none"},
                f3={"delay_s": 8 if fam in IMPLEMENTED else 30, "compensator": f"probes.{site}:compensator"},
                f5={
                    "template": (
                        f"templates/{site}/fam-{fam}.html"
                        if fam in IMPLEMENTED
                        else f"templates/{site}/error.html"
                    )
                },
                f6={"views": views, "stale_reads": 2, "stale_seconds": 0},
                implemented=fam in IMPLEMENTED,
                task_ids=[t["task_id"] for t in ts],
                goal=goal,
                side_effect=d,
            )
            if strip:
                entry["f2"] = {"strip_fields": [strip]}
            # Retain field-name selector from expected data without pinning one instantiation.
            first = next(
                (
                    e["expected"].get("post_data")
                    for e in ts[0]["eval"]
                    if e["evaluator"] == "NetworkEventEvaluator"
                    and e["expected"].get("post_data")
                ),
                None,
            )
            if all_fields:
                entry["body_regex"] = (
                    "(?:"
                    + "|".join(
                        (re.escape(quote_plus(k)) + "=" +
                         ('|name="' + re.escape(k) + '"' if fam in IMPLEMENTED else ''))
                        for k in sorted(all_fields)
                    )
                    + ")"
                )
            entries.append(entry)
            rows.append(
                f"| {site} / fam-{fam} | {len(ts)} | {'yes' if fam in IMPLEMENTED else 'no'} | {goal} | {d} | {tables} | {'; '.join('`'+v+'`' for v in views)} | {strip or 'N/A: one required field per write'} |"
            )
        (ROOT / f"catalog/{site}.yaml").write_text(
            yaml.safe_dump(
                {
                    "site": site,
                    "origin": (
                        "http://127.0.0.1:9999"
                        if site == "reddit"
                        else "http://localhost:7780"
                    ),
                    "entries": entries,
                },
                sort_keys=False,
            )
        )
    rows += [
        "",
        "F4 retries of absolute assignments, votes and subscriptions are idempotent: D=0 is correct. The requested D>=1 assertion is inapplicable for those cells; tests assert committed state and retry idempotence. Relative price/stock and creation flows test actual over-application/duplicates.",
        "",
        "Magento URL adaptation: dataset __SHOPPING_ADMIN__ includes /admin; catalog origin does not. Only that prefix and optional query/trailing slash are normalized. Numeric entity IDs and method/body-field selectors come from NetworkEventEvaluator. F6 snapshots are exact URLs and must be warmed, including grid AJAX query variants.",
    ]
    (ROOT / "catalog/FAMILIES.md").write_text("\n".join(rows) + "\n")


if __name__ == "__main__":
    main()
