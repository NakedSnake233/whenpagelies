"""Magento product probes; transactional, entity-scoped pre-images and undo."""

import math
import re
from decimal import Decimal
from probes.db import query, select, sql_literal
from probes.site_common import task_data, mutations, persist, compensate, timed

SITE = "shopping_admin"
SUPPORTED = {242, 247, 241, 742, 251}


def targets(task):
    task = task_data(task)
    if task["intent_template_id"] not in SUPPORTED:
        raise NotImplementedError("Magento family not implemented")
    out = []
    for e in mutations(task):
        url = e["url"]
        url = url[0] if isinstance(url, list) else url
        out.append((int(re.search(r"/id/(\d+)", url)[1]), e["post_data"]))
    return out


@timed
def baseline(task, episode_dir=None):
    task = task_data(task)
    target_ids = [i for i, _ in targets(task)]
    affected = set(target_ids)
    # A simple SKU's price/stock also affects its configurable parent's indexes.
    # Include parent, siblings and descendants before capturing any pre-images.
    for _ in range(2):
        ids = ",".join(map(str, sorted(affected)))
        relations = query(
            SITE,
            [
                f"SELECT parent_id,child_id FROM catalog_product_relation WHERE parent_id IN ({ids}) OR child_id IN ({ids})"
            ],
        )[0]
        affected.update(int(r[k]) for r in relations for k in ("parent_id", "child_id"))
    ids = ",".join(map(str, sorted(affected)))
    # Product saves touch EAV, inventory, relations and derived index tables.
    # Discover existing entity-scoped tables; never restore unrelated products.
    meta = query(
        SITE,
        [
            "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=DATABASE() AND column_name IN ('entity_id','product_id') AND (table_name LIKE 'catalog_product_%' OR table_name LIKE 'cataloginventory_%' OR table_name IN ('catalog_category_product','catalog_category_product_index','catalog_category_product_index_store1'))"
        ],
    )[0]
    scopes = {r["table_name"]: f"{r['column_name']} IN ({ids})" for r in meta}
    scopes["catalog_product_relation"] = f"parent_id IN ({ids}) OR child_id IN ({ids})"
    auto = query(
        SITE,
        [
            "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=DATABASE() AND extra LIKE '%auto_increment%'"
        ],
    )[0]
    auto = {
        r["table_name"]: r["column_name"] for r in auto if r["table_name"] in scopes
    }
    data = query(
        SITE,
        [select(SITE, t, w) for t, w in scopes.items()]
        + [
            f"SELECT COALESCE(MAX(`{c}`),0) AS watermark FROM `{t}`"
            for t, c in auto.items()
        ],
    )
    marks = {t: rows[0]["watermark"] for t, rows in zip(auto, data[len(scopes) :])}
    b = {
        "site": SITE,
        "task_id": task["task_id"],
        "scopes": scopes,
        "rows": dict(zip(scopes, data)),
        "watermarks": marks,
        "target_ids": target_ids,
        "affected_ids": sorted(affected),
    }
    return persist(task, b, episode_dir)


def _value(rows, pid, code):
    if code == "short_description":
        return next((r["value"] for r in rows["catalog_product_entity_text"]
                     if int(r["entity_id"]) == pid and int(r["attribute_id"]) == 76
                     and int(r["store_id"]) == 0), "")
    if code == "qty":
        return Decimal(
            str(
                next(
                    r["qty"]
                    for r in rows["cataloginventory_stock_item"]
                    if int(r["product_id"]) == pid
                )
            )
        )
    table = (
        "catalog_product_entity_decimal"
        if code == "price"
        else "catalog_product_entity_int"
    )
    aid = 77 if code == "price" else 97
    return Decimal(
        str(
            next(
                r["value"]
                for r in rows[table]
                if int(r["entity_id"]) == pid
                and int(r["attribute_id"]) == aid
                and int(r["store_id"]) == 0
            )
        )
    )


@timed
def evaluate(task, baseline):
    task = task_data(task)
    ts = targets(task)
    current = query(SITE, [select(SITE, t, w) for t, w in baseline["scopes"].items()])
    rows = dict(zip(baseline["scopes"], current))
    checks = []
    extra = 0
    details = []
    for pid, fields in ts:
        field, wanted = next(iter(fields.items()))
        if field == "product[short_description]":
            now = _value(rows, pid, "short_description")
            before = _value(baseline["rows"], pid, "short_description")
            checks.append(now == wanted)
            details.append(dict(product_id=pid, field="short_description", before=before,
                                expected=wanted, actual=now))
            continue
        code = (
            "qty" if "[qty]" in field else ("price" if "[price]" in field else "status")
        )
        now = _value(rows, pid, code)
        before = _value(baseline["rows"], pid, code)
        expected = Decimal(str(wanted))
        if code == "price":
            v = task["instantiation_dict"]
            amount = v["amount"]
            sign = -1 if v["action"].lower() == "reduce" else 1
            delta = (
                before * Decimal(amount.strip("%")) / 100
                if "%" in amount
                else Decimal(amount.strip("$"))
            )
            expected = (before + sign * delta).quantize(Decimal(".01"))
        checks.append(abs(now - expected) < Decimal(".005"))
        delta = expected - before
        if code in {"qty", "price"} and delta and (now - expected) * delta > 0:
            if (
                code == "price"
                and "%" in task["instantiation_dict"]["amount"]
                and now > 0
                and before > 0
            ):
                factor = Decimal(1) + sign * Decimal(amount.strip("%")) / 100
                applications = round(
                    math.log(float(now / before)) / math.log(float(factor))
                )
                extra += max(1, applications - 1)
            else:
                extra += max(1, int(abs((now - expected) / delta)))
        details.append(
            {
                "product_id": pid,
                "field": code,
                "before": str(before),
                "expected": str(expected),
                "actual": str(now),
            }
        )
    return {"G": all(checks), "D": extra, "details": details}


@timed
def restore(task, baseline):
    task = task_data(task)
    targets(task)
    if baseline["task_id"] != task["task_id"]:
        raise ValueError("baseline task mismatch")
    current = dict(
        zip(
            baseline["scopes"],
            query(SITE, [select(SITE, t, w) for t, w in baseline["scopes"].items()]),
        )
    )
    # Delete/reinsert of a parent (e.g. super_attribute or product_link) cascades
    # into dependent rows outside the entity scopes. Preserve existing identities.
    keys = query(SITE, [
        "SELECT table_name,column_name FROM information_schema.key_column_usage "
        "WHERE table_schema=DATABASE() AND constraint_name='PRIMARY' "
        "ORDER BY table_name,ordinal_position"
    ])[0]
    primary = {}
    for key in keys:
        primary.setdefault(key['table_name'], []).append(key['column_name'])
    ops = []
    # Keep parent entity IDs in place so restoring never cascades into children.
    for table, where in baseline["scopes"].items():
        rows = baseline["rows"][table]
        if sorted(map(str, rows)) == sorted(map(str, current[table])):
            continue
        columns = primary.get(table)
        if columns:
            def identity(row):
                return tuple(row[k] for k in columns)
            def predicate(row):
                return ' AND '.join(f'`{k}`={sql_literal(row[k])}' for k in columns)
            before = {identity(row): row for row in rows}
            now = {identity(row): row for row in current[table]}
            for key, row in now.items():
                if key not in before:
                    ops.append(f'DELETE FROM `{table}` WHERE {where} AND {predicate(row)}')
            for key, row in before.items():
                if key not in now:
                    ops.append(f'INSERT INTO `{table}` (' + ','.join(f'`{k}`' for k in row)
                               + ') VALUES (' + ','.join(sql_literal(v) for v in row.values()) + ')')
                elif row != now[key]:
                    assignments = [f'`{k}`={sql_literal(v)}' for k, v in row.items() if k not in columns]
                    if not assignments:
                        continue
                    ops.append(f'UPDATE `{table}` SET ' + ','.join(assignments)
                               + ' WHERE ' + predicate(row))
        else:
            # Index scratch tables without keys have no dependent FK identities.
            ops.append(f"DELETE FROM `{table}` WHERE {where}")
            for row in rows:
                ops.append(
                    f"INSERT INTO `{table}` ("
                    + ",".join("`" + k + "`" for k in row)
                    + ") VALUES ("
                    + ",".join(sql_literal(v) for v in row.values())
                    + ")"
                )
    query(SITE, ops)


@timed
def compensator(plan, entry, flow_summary):
    compensate(plan, entry, restore)
