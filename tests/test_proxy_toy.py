"""Real mitmdump integration: client acknowledgements versus direct backend truth."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import uuid

import httpx
import pytest
import yaml

from proxy.plan import Plan, write_plan

ROOT = Path(__file__).resolve().parents[1]


def eventually(check, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError("condition did not become true before timeout")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Harness:
    def __init__(self, directory):
        self.directory = directory
        self.origin = f"http://127.0.0.1:{free_port()}"
        self.proxy_port = free_port()
        self.catalog_dir = directory / "catalog"
        self.catalog_dir.mkdir()
        self.catalog = yaml.safe_load((ROOT / "catalog/toy.yaml").read_text())
        self.catalog["origin"] = self.origin
        self.catalog["entries"][0]["f5"]["template"] = str(ROOT / "tests/fixtures/toy_error.html")
        self.catalog["entries"][0]["f6"]["stale_seconds"] = 0
        self.plan_path = directory / "plan.json"
        self.runs = directory / "runs"
        self.direct = httpx.Client(base_url=self.origin, trust_env=False, timeout=3)
        self.client = httpx.Client(base_url=self.origin, proxy=f"http://127.0.0.1:{self.proxy_port}", trust_env=False, follow_redirects=False, timeout=4)
        self.processes = []
        self.files = []
        self.set_plan("C0")

    def set_plan(self, fault, target=None, params=None):
        (self.catalog_dir / "toy.yaml").write_text(yaml.safe_dump(self.catalog))
        self.episode = uuid.uuid4().hex
        write_plan(self.plan_path, Plan(self.episode, "toy", fault, 7, target or [], params or {}))
        return self.episode

    def start(self):
        env = dict(os.environ)
        for key in list(env):
            if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
                env.pop(key)
        env.update(NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
                   OSD_PLAN=str(self.plan_path), OSD_CATALOG_DIR=str(self.catalog_dir),
                   OSD_RUNS_DIR=str(self.runs), OSD_RECORD="1", TMPDIR=str(ROOT / ".tmp"),
                   XDG_CACHE_HOME=str(ROOT / ".cache"))
        commands = [
            [str(ROOT / ".venv/bin/python"), "tests/toyapp.py", "--port", self.origin.rsplit(":", 1)[1]],
            [str(ROOT / ".venv/bin/mitmdump"), "--mode", "regular", "--listen-host", "127.0.0.1",
             "--listen-port", str(self.proxy_port), "--set", f"confdir={self.directory / 'mitm-conf'}",
             "-s", "proxy/osd_faults.py"],
        ]
        for name, command in zip(("toy", "proxy"), commands):
            stream = (self.directory / f"{name}.log").open("w")
            self.files.append(stream)
            self.processes.append(subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream, stderr=stream))
        def ready():
            assert all(p.poll() is None for p in self.processes), "child exited; inspect test runtime logs"
            try:
                return self.client.get("/items").status_code == 200
            except httpx.TransportError:
                return False
        eventually(ready, 12)
        response = self.create("recording-example")
        assert response.status_code == 302
        self.recorded_location = response.headers["location"]
        self.recording = self.catalog_dir / "recordings/toy/create-item.json"
        eventually(self.recording.is_file)
        self.direct.post("/admin/rollback-last").raise_for_status()

    def close(self):
        self.client.close()
        self.direct.close()
        for process in reversed(self.processes):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in self.files:
            stream.close()

    def create(self, name="new-item"):
        return self.client.post("/items", data={"name": name, "qty": "3", "note": "note"})

    def state(self):
        return self.direct.get("/api/state").json()

    def rows(self, filename="proxy.jsonl", episode=None):
        path = self.runs / (episode or self.episode) / filename
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def event(self, name, episode=None):
        return eventually(lambda: next((x for x in self.rows("proxy_events.jsonl", episode) if x["event"] == name), None))

    def applied(self, fault):
        return eventually(lambda: [x for x in self.rows() if x["fault_applied"] == fault])


@pytest.fixture
def shop(tmp_path):
    harness = Harness(tmp_path)
    try:
        harness.start()
        yield harness
    finally:
        harness.close()


def test_c0(shop):
    shop.set_plan("C0")
    assert shop.create().status_code == 302
    assert len(shop.state()["items"]) == 1
    row = eventually(lambda: next((r for r in shop.rows() if r["method"] == "POST"), None))
    assert row["fault_applied"] is None and row["forwarded"]
    assert row["matched_entry"] == "create-item"
    assert {"ts", "method", "url", "status", "matched_entry", "fault_applied", "forwarded", "req_body_sha256", "resp_bytes"} <= row.keys()
    recording = json.loads(shop.recording.read_text())
    assert recording["status"] == 302
    assert not {"set-cookie", "date", "content-length"} & {k.lower() for k, _ in recording["headers"]}
    assert base64.b64decode(recording["body"]) == b""


def test_f1(shop):
    shop.set_plan("F1", ["create-item"])
    response = shop.create()
    assert response.status_code == 302 and response.headers["location"] == shop.recorded_location
    assert "set-cookie" not in response.headers
    assert shop.state()["items"] == []
    assert shop.create().status_code == 302
    assert len(shop.state()["items"]) == 1
    assert shop.applied("F1")[0]["forwarded"] is False
    assert len([r for r in shop.rows() if r["fault_applied"] == "F1"]) == 1


def test_f2(shop):
    location = shop.create().headers["location"]
    shop.set_plan("F2", ["edit-item"])
    response = shop.client.post(location + "/edit", data={"name": "changed", "qty": "999"})
    assert response.status_code == 302
    item = shop.state()["items"][0]
    assert item["name"] == "changed" and item["qty"] == 3
    assert shop.applied("F2")[0]["forwarded"]


def test_f3(shop):
    shop.catalog["entries"][0]["f3"]["delay_s"] = 0.8
    shop.set_plan("F3", ["create-item"])
    response = shop.create()
    assert response.status_code == 302
    assert "new-item" in shop.client.get(response.headers["location"]).text
    assert len(shop.state()["items"]) == 1
    shop.event("rollback_done")
    assert shop.state()["items"] == []
    assert shop.applied("F3")[0]["status"] == 302


def test_f4(shop):
    shop.set_plan("F4", ["create-item"])
    response = shop.create()
    assert response.status_code == 502 and "502 Bad Gateway" in response.text
    assert "set-cookie" not in response.headers
    assert len(shop.state()["items"]) == 1
    assert shop.create().status_code == 302
    assert len(shop.state()["items"]) == 2
    assert shop.applied("F4")[0]["forwarded"]


def test_f5(shop):
    shop.set_plan("F5", ["create-item"])
    response = shop.create()
    assert response.status_code == 200
    assert response.content == (ROOT / "tests/fixtures/toy_error.html").read_bytes()
    assert len(response.headers.get_list("set-cookie")) == 2
    assert len(shop.state()["items"]) == 1
    assert shop.applied("F5")[0]["status"] == 200


def test_f6(shop):
    shop.set_plan("F6", ["create-item"])
    before = shop.client.get("/items").content
    assert shop.create().status_code == 302
    for _ in range(2):
        assert shop.client.get("/items", headers={"If-None-Match": '"toy-items"'}).content == before
    assert "new-item" in shop.client.get("/items").text
    assert len(shop.state()["items"]) == 1
    assert len([r for r in shop.rows("proxy_events.jsonl") if r["event"] == "stale_served"]) == 2
    assert shop.applied("F6")


@pytest.mark.parametrize("mode", ["none", "replay"])
def test_f1_fallback(shop, mode):
    shop.recording.unlink()
    shop.catalog["entries"][0]["f1"]["mode"] = mode
    shop.set_plan("F1", ["create-item"])
    assert shop.create().status_code == 302
    assert len(shop.state()["items"]) == 1
    assert shop.event("fallback")["fault_applied"] == "F1->F3"
    assert shop.event("rollback_scheduled")["delay_s"] == 0.5
    shop.event("rollback_done")
    assert shop.state()["items"] == []
    assert shop.applied("F1->F3")


def test_f1_redirect_and_fire_count(shop):
    shop.catalog["entries"][0]["f1"] = {"mode": "redirect", "location": "/items"}
    shop.set_plan("F1", ["create-item"], {"fire_count": 2})
    for _ in range(2):
        response = shop.create()
        assert response.status_code == 302 and response.headers["location"] == "/items"
    assert shop.state()["items"] == []
    assert shop.create().status_code == 302
    assert len(shop.state()["items"]) == 1


def test_f2_json_cart(shop):
    shop.set_plan("F2", ["add-cart"])
    response = shop.client.post("/api/cart", json={"sku": "sku-1", "qty": 7})
    assert response.json() == {"ok": True, "cart_count": 0}
    assert shop.state()["cart"] == [{"sku": "sku-1"}]
    assert shop.applied("F2")


def test_f6_miss_strips_conditionals(shop):
    shop.set_plan("F6", ["create-item"])
    shop.create()
    response = shop.client.get("/items", headers={"If-None-Match": '"toy-items"', "If-Modified-Since": "yesterday"})
    assert "new-item" in response.text
    assert shop.event("stale_miss")
    assert not [r for r in shop.state()["requests"] if r["method"] == "GET" and r["path"] == "/items"][-1]["conditional"]


def test_f6_time_window(shop):
    shop.catalog["entries"][0]["f6"].update(stale_reads=0, stale_seconds=0.5)
    shop.set_plan("F6", ["create-item"])
    before = shop.client.get("/items").content
    shop.create()
    assert shop.client.get("/items").content == before
    eventually(lambda: "new-item" in shop.client.get("/items").text)


def test_new_episode_resets_fired_and_cache(shop):
    shop.set_plan("F1", ["create-item"])
    first_episode = shop.episode
    shop.create()
    shop.set_plan("F1", ["create-item"])
    shop.create()
    assert shop.state()["items"] == []
    assert shop.event("fired")["count"] == 1
    assert shop.event("fired", first_episode)["count"] == 1
    shop.set_plan("F6", ["create-item"])
    shop.client.get("/items")
    shop.set_plan("F6", ["create-item"])
    shop.create()
    assert "new-item" in shop.client.get("/items").text
    assert shop.event("stale_miss")


def test_rollback_logs_original_episode(shop):
    shop.catalog["entries"][0]["f3"]["delay_s"] = 0.5
    original = shop.set_plan("F3", ["create-item"])
    shop.create()
    shop.set_plan("C0")
    shop.client.get("/items")
    shop.event("rollback_done", original)
    assert shop.state()["items"] == []
    assert not shop.rows("proxy_events.jsonl")


def test_rollback_error_does_not_crash(shop):
    shop.catalog["entries"][0]["f3"]["compensator"] = "probes.toy:missing_function"
    shop.set_plan("F3", ["create-item"])
    assert shop.create().status_code == 302
    assert shop.event("rollback_error")["error_type"] == "AttributeError"
    assert shop.client.get("/items").status_code == 200
    assert len(shop.state()["items"]) == 1
