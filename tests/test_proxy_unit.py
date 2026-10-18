"""Boundary cases that complement real-process backend integration tests."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import threading
import time

from mitmproxy import http
from mitmproxy.test import tflow
import pytest
import yaml

from proxy.catalog import CatalogError, load_catalog
from proxy.osd_faults import FaultProxy
from proxy.plan import Plan, read_plan, write_plan

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("OSD_PLAN", str(tmp_path / "plan.json"))
    monkeypatch.setenv("OSD_CATALOG_DIR", str(tmp_path))
    monkeypatch.setenv("OSD_RUNS_DIR", str(tmp_path / "runs"))
    catalog = yaml.safe_load((ROOT / "catalog/toy.yaml").read_text())
    catalog["entries"][0]["f5"]["template"] = str(ROOT / "tests/fixtures/toy_error.html")
    (tmp_path / "toy.yaml").write_text(yaml.safe_dump(catalog))
    return FaultProxy()


def configure(proxy, fault, target=None, **params):
    plan = Plan("unit", "toy", fault, 0, target or [], params)
    write_plan(proxy.plan_path, plan)
    return plan


def request(path="/items", method="POST", body=b"name=hello&qty=7", origin="http://127.0.0.1:18080", content_type="application/x-www-form-urlencoded"):
    return tflow.tflow(req=http.Request.make(method, origin + path, body, {"Content-Type": content_type}))


def finish(proxy, flow):
    if not flow.response:
        flow.response = http.Response.make(200, b"ok")
    proxy.response(flow)


def test_plan_roundtrip_and_atomic_readers(tmp_path):
    path = tmp_path / "nested/plan.json"
    assert read_plan(path) is None
    plan = Plan("one", "toy", "F1", 17)
    write_plan(path, plan)
    assert read_plan(path) == plan
    errors = []
    def writer():
        try:
            for i in range(30):
                write_plan(path, Plan(f"episode-{i}", "toy", "F2", i))
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=writer)
    thread.start()
    while thread.is_alive():
        assert isinstance(read_plan(path), Plan)
    thread.join()
    assert not errors
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("field,value", [("episode_id", "../outside"), ("site", "/tmp"), ("fault", "F7"), ("seed", True), ("target", "id"), ("params", {"fire_count": -1})])
def test_plan_validation(field, value):
    data = asdict(Plan("one", "toy", "C0", 0))
    data[field] = value
    with pytest.raises(ValueError):
        Plan(**data)


@pytest.mark.parametrize("value", ["{", "[]", '{"episode_id":"x"}'])
def test_malformed_plan(tmp_path, value):
    path = tmp_path / "plan"
    path.write_text(value)
    with pytest.raises(ValueError, match="plan"):
        read_plan(path)


@pytest.mark.parametrize("field,value", [("url_regex", "["), ("method", "post"), ("content_type", "xml"), ("effect", "read"), ("f1", {"mode": "redirect"}), ("f2", {"strip_fields": "qty"}), ("f3", {"delay_s": -1}), ("f3", {"compensator": "bad"}), ("f5", {"template": "missing.html"}), ("f6", {"views": ["[" ]}), ("f6", {"stale_seconds": float("inf")})])
def test_catalog_errors(proxy, field, value):
    path = proxy.catalog_dir / "toy.yaml"
    data = yaml.safe_load(path.read_text())
    data["entries"][0][field] = value
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(CatalogError, match="create-item"):
        load_catalog(path)


def test_catalog_duplicate_and_origin(proxy):
    path = proxy.catalog_dir / "toy.yaml"
    data = yaml.safe_load(path.read_text())
    data["entries"].append(data["entries"][0])
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(CatalogError, match="unique"):
        load_catalog(path)
    data["origin"] = "http://localhost:123/path"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(CatalogError, match="origin"):
        load_catalog(path)


def test_origin_method_path_body_and_target_filter(proxy):
    path = proxy.catalog_dir / "toy.yaml"
    data = yaml.safe_load(path.read_text())
    data["entries"][0]["body_regex"] = "name=eligible"
    data["entries"][0]["f1"] = {"mode": "redirect", "location": "/items"}
    path.write_text(yaml.safe_dump(data))
    configure(proxy, "F1", ["create-item"])
    for flow in [request(origin="http://127.0.0.1:18081"), request(method="GET"), request("/other"), request(body=b"name=ineligible"), request("/api/cart")]:
        proxy.request(flow)
        assert flow.response is None
        finish(proxy, flow)
    flow = request("/items?ok=1", body=b"name=eligible")
    proxy.request(flow)
    assert flow.response.status_code == 302
    assert proxy.episode.fired == 1


def test_same_episode_reload_preserves_count_new_episode_resets(proxy):
    plan = configure(proxy, "F4")
    proxy.request(request())
    write_plan(proxy.plan_path, plan)
    flow = request()
    proxy.request(flow)
    assert flow.metadata["osd"].applied is None
    plan.episode_id = "new"
    write_plan(proxy.plan_path, plan)
    proxy.request(flow := request())
    assert flow.metadata["osd"].applied == "F4"


def test_inflight_response_keeps_episode(proxy):
    configure(proxy, "F4")
    old = request()
    proxy.request(old)
    write_plan(proxy.plan_path, Plan("new", "toy", "C0", 0))
    proxy.request(new := request())
    finish(proxy, new)
    finish(proxy, old)
    assert old.response.status_code == 502 and new.response.status_code == 200
    old_row = json.loads((proxy.runs_dir / "unit/proxy.jsonl").read_text())
    new_row = json.loads((proxy.runs_dir / "new/proxy.jsonl").read_text())
    assert old_row["fault_applied"] == "F4" and new_row["fault_applied"] is None


def test_f2_multipart_preserves_binary_upload_headers(proxy):
    path = proxy.catalog_dir / "toy.yaml"
    data = yaml.safe_load(path.read_text())
    data["entries"][0].update(content_type="multipart", f2={"strip_fields": ["qty"]})
    path.write_text(yaml.safe_dump(data))
    configure(proxy, "F2")
    upload = b'--BOUND\r\nContent-Disposition: form-data; name="file"; filename="raw.bin"\r\nContent-Type: application/octet-stream\r\n\r\n\xff\x00bytes\r\n'
    field = b'--BOUND\r\nContent-Disposition: form-data; name="qty"\r\n\r\n9\r\n'
    body = upload + field + b"--BOUND--\r\n"
    flow = request(body=body, content_type="multipart/form-data; boundary=BOUND")
    proxy.request(flow)
    assert flow.request.content == upload + b"--BOUND--\r\n"
    assert int(flow.request.headers["content-length"]) == len(flow.request.raw_content)
    assert flow.metadata["osd"].request_hash == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize("kind,paths", [("json", ["variables.input.qty", "variables.items.0.note"]), ("graphql", ["input.qty", "variables.items.0.note"])])
def test_f2_nested_json_graphql(proxy, kind, paths):
    path = proxy.catalog_dir / "toy.yaml"
    data = yaml.safe_load(path.read_text())
    data["entries"][0].update(content_type=kind, f2={"strip_json_paths": paths})
    path.write_text(yaml.safe_dump(data))
    configure(proxy, "F2")
    body = {"query": "mutation Update", "variables": {"input": {"qty": 9, "name": "保留"}, "items": [{"note": "drop", "id": 4}]}}
    flow = request(body=json.dumps(body).encode(), content_type="application/json")
    proxy.request(flow)
    value = json.loads(flow.request.content)
    assert value["query"] == body["query"]
    assert value["variables"] == {"input": {"name": "保留"}, "items": [{"id": 4}]}
    assert int(flow.request.headers["content-length"]) == len(flow.request.raw_content)


def test_non_utf8_passes_without_crash(proxy):
    configure(proxy, "F2")
    flow = request(body=b"\xff\x00")
    proxy.request(flow)
    finish(proxy, flow)
    assert flow.request.content == b"\xff\x00"
    assert flow.response.content == b"ok"
    assert flow.metadata["osd"].applied is None


@pytest.mark.parametrize("mode", ["request-stream", "response-stream", "websocket"])
def test_streaming_and_websocket_passthrough(proxy, mode):
    configure(proxy, "F4")
    flow = request()
    if mode == "request-stream":
        flow.request.stream = True
    if mode == "websocket":
        flow.request.headers["upgrade"] = "websocket"
    proxy.request(flow)
    flow.response = http.Response.make(200, b"original")
    if mode == "response-stream":
        flow.response.stream = True
    proxy.response(flow)
    assert flow.response.content == b"original"


def test_no_plan_and_error_logging(proxy):
    flow = request()
    proxy.request(flow)
    assert "osd" not in flow.metadata
    configure(proxy, "C0")
    proxy.request(flow)
    proxy.error(flow)
    proxy.error(flow)
    rows = (proxy.runs_dir / "unit/proxy.jsonl").read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["status"] is None


@pytest.mark.parametrize("headers", [{"Content-Type": "text/event-stream"}, {"Content-Length": str(20 * 1024 * 1024 + 1)}])
def test_unbounded_responses_are_streamed(proxy, headers):
    configure(proxy, "F4")
    flow = request()
    proxy.request(flow)
    flow.response = http.Response.make(200, b"data: event\n\n", headers)
    flow.response.headers.update(headers)
    proxy.responseheaders(flow)
    assert flow.response.stream is True
    proxy.response(flow)
    assert flow.response.status_code == 200
    assert flow.metadata["osd"].applied is None


def test_streamed_responses_are_not_cached_or_recorded(proxy):
    configure(proxy, "F6")
    proxy.record = True
    flow = request(method="GET", body=b"")
    proxy.request(flow)
    flow.response = http.Response.make(200, b"partial")
    flow.response.stream = True
    proxy.response(flow)
    assert proxy.episode.cache == {}
    assert not (proxy.catalog_dir / "recordings").exists()


def test_bad_plan_disables_injection_until_republished(proxy):
    plan = configure(proxy, "F4")
    proxy.request(request())
    proxy.plan_path.write_text('{"broken":')
    flow = request()
    proxy.request(flow)
    assert "osd" not in flow.metadata
    write_plan(proxy.plan_path, Plan("recovered", "toy", "F4", plan.seed))
    proxy.request(flow)
    assert flow.metadata["osd"].applied == "F4"


@pytest.mark.parametrize("field,value", [("content_type", []), ("effect", {}), ("f1", {"mode": []})])
def test_catalog_wrong_container_types_have_clear_errors(proxy, field, value):
    path = proxy.catalog_dir / "toy.yaml"
    data = yaml.safe_load(path.read_text())
    data["entries"][0][field] = value
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(CatalogError, match="create-item"):
        load_catalog(path)
