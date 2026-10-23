"""Real-site browser fault proofs (no LLM). Run serially with pytest -m sites.

Long runs: nohup .venv/bin/python -m pytest -q -m sites >logs/m1b_sites.log 2>&1 &
"""

import html
import importlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import uuid
from urllib.parse import urlencode, urljoin
import pytest
from playwright.sync_api import sync_playwright
from proxy.plan import Plan, write_plan, F3_DELAYS, rollback_delay
from probes.site_common import task_data, mutations
from probes.reddit import POST_FAMILIES

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("shopping_admin", 453),
    ("shopping_admin", 458),
    ("shopping_admin", 768),
    ("shopping_admin", 781),
    ("shopping_admin", 464),
    ("reddit", 400),
    ("reddit", 404),
    ("reddit", 595),
    ("reddit", 630),
    ("reddit", 620),
    ("reddit", 625),
    ("reddit", 640),
    ("reddit", 635),
]
CONDITIONS = ["C0", "F1", "F2", "F3", "F4", "F5", "F6", "F3s2", "F3s30"]
pytestmark = pytest.mark.sites


@pytest.fixture(autouse=True)
def serialize_site(request):
    """Prevent separate pytest invocations from writing the same site together."""
    import fcntl

    site = getattr(request.node, "callspec", None)
    site = site.params.get("site", "shopping_admin") if site else "shopping_admin"
    directory = ROOT / "runs/locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"m1b-{site}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
        fcntl.flock(lock, fcntl.LOCK_UN)


def wait_until(check, timeout=6):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError("condition timed out")


class BrowserFlow:
    def __init__(self, page, task, baseline, mutation_index=0):
        self.page = page
        self.task = task
        self.b = baseline
        self.site = task["sites"][0]
        self.fam = task["intent_template_id"]
        self.mutation = mutations(task)[mutation_index]
        self.fields = dict(self.mutation.get("post_data") or {})
        self.origin = (
            "http://localhost:7780"
            if self.site == "shopping_admin"
            else "http://127.0.0.1:9999"
        )
        if self.site == "shopping_admin":
            self.pid = baseline["target_ids"][mutation_index]
            self.view = f"/admin/catalog/product/edit/id/{self.pid}/"
        elif self.fam == 6:
            self.view = "/user/MarvelsGrantMan136/edit_biography"
        elif self.fam == 22:
            self.view = (
                "/f/"
                + task["instantiation_dict"]["forum"]
                + "/"
                + str(baseline["submission_id"])
                + "/-"
            )
        elif self.fam == 4:
            self.view = "/f/" + task["instantiation_dict"]["forum"]
        else:
            self.view = (
                "/f/" + baseline["forum_name"] + "/new"
            )

    def prepare(self):
        p = self.page
        # Preserve the evaluator-declared route spelling (e.g. /submit/diy).
        # Postmill resolves that route to the canonical forum ID, while proxy
        # regexes are case-sensitive and must see the declared mutation URL.
        form = (next(u.replace("__REDDIT__", "") for u in self.mutation["url"]
                     if "/submit/" in u)
                if self.fam in POST_FAMILIES else self.view)
        p.goto(self.origin + form, wait_until="networkidle")
        if self.site == "shopping_admin":
            p.locator('input[name="product[price]"]').wait_for()
            self.payload = p.locator(
                "input[name],textarea[name],select[name]"
            ).evaluate_all(
                'els=>Object.fromEntries(els.filter(e=>!e.disabled && (!["checkbox","radio"].includes(e.type)||e.checked)).map(e=>[e.name,e.value]))'
            )
            r = next(
                r
                for r in self.b["rows"]["catalog_product_entity"]
                if int(r["entity_id"]) == int(self.pid)
            )
            self.write = f"/admin/catalog/product/save/id/{self.pid}/type/{r['type_id']}/store/0/set/{r['attribute_set_id']}/back/edit"
        else:
            if self.fam in {22, 4}:
                selector = (
                    f'form[data-vote-id-value="{self.b["submission_id"]}"]'
                    if self.fam == 22
                    else "form.subscribe-form"
                )
                self.payload = {
                    "token": p.locator(
                        selector + ' input[name="token"]'
                    ).first.input_value()
                }
            else:
                key = (
                    "user_biography[biography]"
                    if self.fam == 6
                    else "submission[title]"
                )
                native = p.locator('[name="' + key + '"]').locator(
                    "xpath=ancestor::form"
                )
                self.payload = native.locator(
                    "input[name],textarea[name],select[name]"
                ).evaluate_all("els=>Object.fromEntries(els.map(e=>[e.name,e.value]))")
            if self.fam not in POST_FAMILIES:
                self.view = p.url.removeprefix(self.origin)
            e = mutations(self.task)[0]
            u = e["url"]
            u = u[0] if isinstance(u, list) else u
            self.write = u.replace("__REDDIT__", "")
            if self.fam in POST_FAMILIES:
                self.write = form
        self.payload.update({k: str(v) for k, v in self.fields.items()})

    def read(self):
        return self.page.evaluate(
            'async url => {let r=await fetch(url,{cache:"no-store",headers:url.includes("/mui/")?{"X-Requested-With":"XMLHttpRequest","Accept":"application/json"}:{}});return await r.text()}',
            self.origin + self.view,
        )

    def write_once(self, retry=False):
        p = self.page
        if retry and self.site == "reddit":
            self.prepare()
        if retry and self.site == "shopping_admin" and self.fam in {247, 241, 742}:
            # A careful retry reads current UI and applies the relative instruction again.
            p.goto(self.origin + self.view, wait_until="networkidle")
            key = next(iter(self.fields))
            now = float(p.locator('[name="' + key + '"]').input_value())
            if self.fam in {247, 742}:
                ins = self.task["instantiation_dict"]
                amount = ins["amount"]
                delta = (
                    now * float(amount.strip("%")) / 100
                    if "%" in amount
                    else float(amount.strip("$"))
                )
                self.payload[key] = str(
                    round(now + (-delta if ins["action"] == "Reduce" else delta), 2)
                )
            else:
                before = float(
                    next(
                        r["qty"]
                        for r in self.b["rows"]["cataloginventory_stock_item"]
                        if int(r["product_id"]) == int(self.pid)
                    )
                )
                self.payload[key] = str(now + float(self.fields[key]) - before)
        # Playwright's context request shares the browser session and configured proxy.
        # The native form supplies field values and CSRF token; no direct backend write.
        response = p.context.request.post(
            self.origin + self.write, form=self.payload, max_redirects=0
        )
        self.status = response.status
        self.location = response.headers.get("location")
        self.body = response.text()
        return self.status

    def show(self, fault):
        if fault == "F6" and self.fam not in POST_FAMILIES:
            if "/mui/" in self.view:
                self.first_stale = self.read()
                self.page.set_content(
                    "<pre>" + html.escape(self.first_stale) + "</pre>"
                )
            else:
                response = self.page.goto(
                    self.origin + self.view, wait_until="networkidle"
                )
                self.first_stale = response.text()
                if self.site == "shopping_admin":
                    self.page.locator('input[name="product[price]"]').wait_for()
            return self.page.locator("body").inner_text()
        if 300 <= self.status < 400 and self.location:
            self.page.goto(
                urljoin(self.origin, self.location), wait_until="networkidle"
            )
            if self.site == "shopping_admin":
                self.page.locator('input[name="product[price]"]').wait_for()
        elif (
            self.fam in {22, 4} and self.status == 200 and not self.body.startswith("<")
        ):
            # JSON acknowledgement normally updates the current page through JS.
            if fault != "F6":
                self.page.goto(self.origin + self.view, wait_until="networkidle")
        else:
            self.page.set_content(self.body, wait_until="domcontentloaded")
        return self.page.locator("body").inner_text()


@pytest.mark.parametrize("site,tid", CASES, ids=[f"{s}-{t}" for s, t in CASES])
@pytest.mark.parametrize("fault", CONDITIONS)
def test_site_fault(site, tid, fault, readback="page"):
    task = task_data(tid)
    fam = task["intent_template_id"]
    if fault == "F2" and fam not in POST_FAMILIES:
        pytest.skip("one task-required field: F2 inapplicable")
    if site == "reddit" and not (ROOT / "logs/m2_status.md").exists():
        pytest.skip("M2 owns Reddit writes until logs/m2_status.md exists")
    probe = importlib.import_module("probes." + site)
    episode = f"m1b-{site}-{fam}-{fault}-{uuid.uuid4().hex[:8]}"
    directory = ROOT / "runs" / episode
    directory.mkdir(parents=True)
    b = probe.baseline(task, episode_dir=directory)
    assert (
        probe.evaluate(task, b)["G"] is False
    ), "task is already satisfied; select an unsatisfied instantiation"
    plan = directory / "plan.json"
    write_plan(
        plan,
        Plan(
            episode,
            site,
            fault,
            0,
            [f"fam-{fam}"],
            {"task_id": tid, "baseline_path": str(directory / "baseline.json")},
        ),
    )
    port = 8201 + int(os.environ.get("OSD_SITE_PORT_OFFSET", "0"))
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))  # Verify configured port before launching proxy.
    env = dict(
        os.environ,
        OSD_PLAN=str(plan),
        OSD_RUNS_DIR=str(ROOT / "runs"),
        OSD_CATALOG_DIR=str(ROOT / "catalog"),
        PLAYWRIGHT_BROWSERS_PATH=str(ROOT / ".cache/ms-playwright"),
        TMPDIR=str(ROOT / ".tmp"),
        XDG_CACHE_HOME=str(ROOT / ".cache"),
    )
    for k in list(env):
        if k.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            env.pop(k)
    log = (directory / "mitmdump.log").open("w")
    process = subprocess.Popen(
        [
            str(ROOT / ".venv/bin/mitmdump"),
            "--listen-host",
            "127.0.0.1",
            "--listen-port",
            str(port),
            "--set",
            f"confdir={ROOT}/.cache/mitmproxy-m1b",
            "-s",
            "proxy/osd_faults.py",
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
    )
    browser = None
    try:

        def ready():
            with socket.socket() as s:
                return s.connect_ex(("127.0.0.1", port)) == 0

        wait_until(ready)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/ms-playwright")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--proxy-bypass-list=<-loopback>"],
                proxy={"server": f"http://127.0.0.1:{port}"},
            )
            header = (
                {"X-M2-Admin-Auto-Login": "admin:admin1234"}
                if site == "shopping_admin"
                else {"X-Postmill-Auto-Login": "MarvelsGrantMan136:test1234"}
            )
            context = browser.new_context(extra_http_headers=header)
            page = context.new_page()
            page.set_default_timeout(15000)
            flow = BrowserFlow(page, task, b)
            flow.prepare()
            if readback == "grid":
                flow.view = "/admin/mui/index/render/?" + urlencode(
                    {
                        "namespace": "product_listing",
                        "paging[current]": 1,
                        "paging[pageSize]": 20,
                        "filters[entity_id][from]": flow.pid,
                        "filters[entity_id][to]": flow.pid,
                        "isAjax": "true",
                    }
                )
            before = flow.read() if fault == "F6" else None
            if readback == "grid":
                assert [int(r["entity_id"]) for r in json.loads(before)["items"]] == [
                    int(flow.pid)
                ]
            started = time.monotonic()
            status = flow.write_once()
            text = flow.show(fault)
            output = (
                ROOT
                / f'runs/{"silence" if readback=="page" else "grid_proofs"}/{site}/fam-{fam}'
            )
            output.mkdir(parents=True, exist_ok=True)
            (output / f"{fault}.txt").write_text(text)
            if fault == "F1":
                assert status in {200, 302}
                if fam == 6:
                    assert "updated" in text.lower()
                if fam in POST_FAMILIES:
                    assert flow.fields["submission[title]"] in text
                if fam == 4:
                    assert json.loads(flow.body)["subscribed"] is True
                if fam == 22:
                    assert (
                        json.loads(flow.body)["netScore"]
                        > b["rows"]["submissions"][0]["net_score"]
                    )
                if site == "shopping_admin":
                    assert "saved" in text.lower()
                wait_until(lambda: not probe.evaluate(task, b)["G"])
            elif fault == "F2":
                result = probe.evaluate(task, b)
                assert not result["G"]
                assert (
                    len(result["details"]) == 1
                    and result["details"][0]["title"]
                    == flow.fields["submission[title]"]
                )
                assert all(
                    not r["body"] for r in result["details"]
                ), "stripped body must stay absent"
            elif fault in F3_DELAYS:
                from runner.metrics import rows, wait_rollbacks
                events = directory / "proxy_events.jsonl"
                wait_until(lambda: any(e['event'] == 'rollback_scheduled' for e in rows(events)))
                scheduled = next(e for e in rows(events) if e['event'] == 'rollback_scheduled')
                delay = rollback_delay(fault, {'f3': {'delay_s': 8}})
                assert scheduled['delay_s'] == delay
                # Check the committed write shortly before the scheduled deadline.
                time.sleep(max(0, scheduled['ts'] + delay - min(1.0, delay / 2) - time.time()))
                assert time.time() < scheduled['ts'] + delay
                assert probe.evaluate(task, b)["G"]
                wait_rollbacks(directory, timeout=delay + 30)
                assert any(e['event'] == 'rollback_done' for e in rows(events))
                assert not probe.evaluate(task, b)["G"]
            else:
                assert probe.evaluate(task, b)["G"]
                if fault == "F4":
                    assert status == 502 and "502 Bad Gateway" in text
                    flow.write_once(retry=True)
                    result = probe.evaluate(task, b)
                    if (fam in POST_FAMILIES or fam in {247, 241, 742}):
                        assert result["D"] >= 1
                    else:
                        assert result["G"] and result["D"] == 0
                elif fault == "F5":
                    assert "try again" in text.lower()
                elif fault == "F6":
                    assert (flow.read() if fam in POST_FAMILIES else flow.first_stale) == before
                    assert flow.read() == before
                    fresh = flow.read()
                    assert fresh != before
                    if readback == "grid":
                        current = json.loads(fresh)["items"][0]
                        detail = probe.evaluate(task, b)["details"][0]
                        assert (
                            abs(
                                float(
                                    re.sub(
                                        r"[^0-9.\-]", "", str(current[detail["field"]])
                                    )
                                )
                                - float(detail["expected"])
                            )
                            < 0.005
                        )
            events = directory / "proxy_events.jsonl"
            if fault != "C0":
                rows = [json.loads(l) for l in events.read_text().splitlines()]
                assert any(r["event"] == "fired" for r in rows)
                assert not any(r["event"] == "rollback_error" for r in rows)
            browser.close()
            browser = None
    finally:
        # Stop our proxy before restoring so no delayed timer can race with the next test.
        process.terminate()
        process.wait(timeout=5)
        log.close()
        probe.restore(task, b)
        probe.restore(task, b)  # Prove idempotence.
        assert not probe.evaluate(task, b)["G"]
        restored = probe.baseline(task)
        assert {
            k: sorted(v, key=lambda r: json.dumps(r, sort_keys=True))
            for k, v in restored["rows"].items()
        } == {
            k: sorted(v, key=lambda r: json.dumps(r, sort_keys=True))
            for k, v in b["rows"].items()
        }


@pytest.mark.parametrize("tid", [453, 458, 768])
def test_magento_grid_readback(tid):
    test_site_fault("shopping_admin", tid, "F6", readback="grid")
