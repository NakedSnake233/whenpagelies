"""Postmill probes: indexed scopes, numeric watermarks, no full-table dumps."""

import re
from probes.db import ROOT, query, select, sql_literal as lit
from probes.site_common import task_data, mutations, persist, compensate, timed

SITE = "reddit"
POST_FAMILIES = {15, 12, 13, 16, 6100}
SUPPORTED = {6, 22, 4} | POST_FAMILIES
USER = "normalized_username='marvelsgrantman136'"


def _check(task):
    task = task_data(task)
    if task["intent_template_id"] not in SUPPORTED:
        raise NotImplementedError("Reddit family not implemented")
    return task


def write_guard():
    if not (ROOT / "logs/m2_status.md").exists():
        raise RuntimeError("Reddit writes blocked until logs/m2_status.md exists")


@timed
def baseline(task, episode_dir=None):
    task = _check(task)
    fam = task["intent_template_id"]
    user = query(
        SITE,
        [f"SELECT jsonb_agg(r) FROM (SELECT id,biography FROM users WHERE {USER}) r"],
    )[0][0]
    uid = user["id"]
    b = {
        "site": SITE,
        "task_id": task["task_id"],
        "user_id": uid,
        "rows": {},
        "scopes": {},
        "watermarks": {},
    }
    if fam == 6:
        b["rows"]["users"] = [user]
    elif fam == 22:
        e = mutations(task)[0]
        sid = int(re.search(r"/sv/(\d+)", e["url"])[1])
        b["submission_id"] = sid
        b["scopes"] = {
            "submission_votes": f"user_id={uid} AND submission_id={sid}",
            "submissions": f"id={sid}",
        }
    elif fam == 4:
        forum = task["instantiation_dict"]["forum"]
        # Dataset 599 spells MachineLearning as "machine learning". Resolve
        # display-name whitespace against the canonical forum slug, uniquely.
        candidates = query(SITE, [
            "SELECT COALESCE(jsonb_agg(r),'[]'::jsonb) FROM (SELECT id,name FROM forums "
            f"WHERE normalized_name={lit(forum.lower())}) r"
        ])[0]
        if not candidates:
            compact = re.sub(r"\s+", "", forum.lower())
            candidates = query(SITE, [
                "SELECT COALESCE(jsonb_agg(r),'[]'::jsonb) FROM (SELECT id,name FROM forums "
                "WHERE regexp_replace(normalized_name, '[[:space:]]+', '', 'g')="
                f"{lit(compact)}) r"
            ])[0]
        if len(candidates) != 1:
            raise ValueError('forum lookup must resolve exactly one canonical forum')
        fid = candidates[0]["id"]
        b["forum_id"] = fid
        b["forum_name"] = candidates[0].get("name", re.sub(r"\s+", "", forum))
        b["scopes"] = {"forum_subscriptions": f"user_id={uid} AND forum_id={fid}"}
    else:
        fields = mutations(task)[0]["post_data"]
        fid = int(fields["submission[forum]"])
        b["forum_id"] = fid
        b["forum_name"] = query(SITE, [
            f"SELECT jsonb_agg(r) FROM (SELECT name FROM forums WHERE id={fid}) r"
        ])[0][0]['name']
        # Snapshot existing episode-user posts in the selected forum only.
        b["scopes"] = {"submissions": f"user_id={uid} AND forum_id={fid}"}
    if b["scopes"]:
        b["rows"].update(
            zip(
                b["scopes"],
                query(SITE, [select(SITE, t, w) for t, w in b["scopes"].items()]),
            )
        )
    if fam == 22:
        b["forum_name"] = query(SITE, [
            "SELECT jsonb_agg(r) FROM (SELECT name FROM forums WHERE id="
            f"{b['rows']['submissions'][0]['forum_id']}) r"
        ])[0][0]['name']
    if fam == 22 or fam in POST_FAMILIES:
        tables = ["submission_votes"] + (["submissions"] if fam in POST_FAMILIES else [])
        marks = query(
            SITE,
            [
                f"SELECT jsonb_build_object('id',COALESCE(MAX(id),0)) FROM {t}"
                for t in tables
            ],
        )
        b["watermarks"] = {t: r["id"] for t, r in zip(tables, marks)}
    if fam == 4:
        b["watermarks"]["forum_subscriptions"] = [
            r["id"] for r in b["rows"]["forum_subscriptions"]
        ]  # UUID set, not numeric max.
    return persist(task, b, episode_dir)


@timed
def evaluate(task, baseline):
    task = _check(task)
    b = baseline
    fam = task["intent_template_id"]
    uid = b["user_id"]
    if fam == 6:
        rows = query(
            SITE,
            [
                f"SELECT jsonb_agg(r) FROM (SELECT biography FROM users WHERE id={uid}) r"
            ],
        )[0]
        good = rows[0]["biography"] == task["instantiation_dict"]["content"]
        d = 0
    elif fam == 22:
        rows = query(
            SITE, [select(SITE, "submission_votes", b["scopes"]["submission_votes"])]
        )[0]
        good = any(r["upvote"] is True for r in rows)
        d = max(0, len(rows) - 1)
    elif fam == 4:
        rows = query(
            SITE,
            [select(SITE, "forum_subscriptions", b["scopes"]["forum_subscriptions"])],
        )[0]
        good = bool(rows)
        d = max(0, len(rows) - 1)
    else:
        rows = query(SITE, [select(SITE, "submissions", _new_scope(b))])[0]
        f = mutations(task)[0]["post_data"]
        good = any(
            r["title"] == f["submission[title]"]
            and (r["body"] or "") == f["submission[body]"]
            for r in rows
        )
        d = max(0, len(rows) - 1)
    return {"G": good, "D": d, "details": rows}


def _new_scope(b):
    # The seeded Postmill sequence can lag MAX(id): newly created rows can have
    # SMALLER IDs. The actor/forum pre-image set, not MAX alone, identifies them.
    old = [str(r["id"]) for r in b["rows"]["submissions"]]
    return b["scopes"]["submissions"] + (
        " AND id NOT IN (" + ",".join(old) + ")" if old else ""
    )


def _insert(table, row):
    return (
        f"INSERT INTO {table} ("
        + ",".join('"' + k + '"' for k in row)
        + ") VALUES ("
        + ",".join(lit(v) for v in row.values())
        + ")"
    )


@timed
def restore(task, baseline):
    write_guard()
    task = _check(task)
    b = baseline
    uid = b["user_id"]
    fam = task["intent_template_id"]
    if b["task_id"] != task["task_id"]:
        raise ValueError("baseline task mismatch")
    ops = []
    if fam == 6:
        ops = [
            f"UPDATE users SET biography={lit(b['rows']['users'][0]['biography'])} WHERE id={uid}"
        ]
    elif fam == 22:
        ops = [f"DELETE FROM submission_votes WHERE {b['scopes']['submission_votes']}"]
        ops += [_insert("submission_votes", r) for r in b["rows"]["submission_votes"]]
        r = b["rows"]["submissions"][0]
        ops += [
            "UPDATE submissions SET "
            + ",".join(f"{k}={lit(r[k])}" for k in ["net_score", "ranking"])
            + f" WHERE id={r['id']}"
        ]
    elif fam == 4:
        ops = [
            f"DELETE FROM forum_subscriptions WHERE {b['scopes']['forum_subscriptions']}"
        ] + [
            _insert("forum_subscriptions", r) for r in b["rows"]["forum_subscriptions"]
        ]
    else:
        scope = _new_scope(b)
        ids = f"SELECT id FROM submissions WHERE {scope}"
        # No other writers may act on these episode-created posts during proofs.
        comments = f"SELECT id FROM comments WHERE submission_id IN ({ids})"
        ops.append(f"DELETE FROM notifications WHERE submission_id IN ({ids}) "
                   f"OR comment_id IN ({comments})")
        ops.append(f"DELETE FROM comment_votes WHERE comment_id IN ({comments})")
        # Delete the whole scoped comment tree in one statement: parent_id is
        # self-referential, so replies and their parents must disappear together.
        ops.append(f"DELETE FROM comments WHERE submission_id IN ({ids})")
        for table in ["submissions_flairs", "submission_votes"]:
            ops.append(f"DELETE FROM {table} WHERE submission_id IN ({ids})")
        ops.append(f"DELETE FROM submissions WHERE {scope}")
    query(SITE, ops)


@timed
def compensator(plan, entry, flow_summary):
    compensate(plan, entry, restore)
