"""mitmdump -s proxy/osd_faults.py: deterministic per-worker HTTP faults."""
import base64
from dataclasses import dataclass, field
from email.parser import BytesHeaderParser
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import parse_qsl, urlencode

# mitmdump loads scripts by filename, without necessarily adding the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mitmproxy import http
from proxy.catalog import load_catalog, origin_key
from proxy.plan import Plan, read_plan


@dataclass
class Episode:
    plan: Plan
    catalog: object
    fired: int = 0
    cache: dict = field(default_factory=dict)
    windows: list = field(default_factory=list)


@dataclass
class FlowState:
    episode: Episode
    plan: Plan
    request_hash: str
    entry: dict | None = None
    applied: str | None = None
    forwarded: bool = True
    logged: bool = False
    rollback_delay: float | None = None
    skip: bool = False


def strip_path(value, dotted):
    parts = dotted.split(".")
    for part in parts[:-1]:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return
    if isinstance(value, dict):
        value.pop(parts[-1], None)
    elif isinstance(value, list) and parts[-1].isdigit() and int(parts[-1]) < len(value):
        del value[int(parts[-1])]


def strip_multipart(body, content_type, fields):
    """Remove whole parts, preserving filenames, headers and binary uploads verbatim."""
    header = BytesHeaderParser().parsebytes(b"Content-Type: " + content_type.encode("latin1") + b"\r\n\r\n")
    boundary = header.get_boundary()
    if not boundary:
        raise ValueError("missing multipart boundary")
    marker = b"--" + boundary.encode("latin1")
    chunks = body.split(marker)
    if len(chunks) < 3 or not chunks[-1].startswith(b"--"):
        raise ValueError("invalid multipart body")
    kept = [chunks[0]]
    for chunk in chunks[1:-1]:
        headers, separator, _ = chunk.lstrip(b"\r\n").partition(b"\r\n\r\n")
        if not separator:
            raise ValueError("invalid multipart part")
        part = BytesHeaderParser().parsebytes(headers + b"\r\n\r\n")
        if part.get_param("name", header="content-disposition") not in fields:
            kept.append(chunk)
    return marker.join([*kept, chunks[-1]])


class FaultProxy:
    def __init__(self):
        self.plan_path = Path(os.environ.get("OSD_PLAN", "plan.json"))
        self.catalog_dir = Path(os.environ.get("OSD_CATALOG_DIR", "catalog"))
        self.runs_dir = Path(os.environ.get("OSD_RUNS_DIR", "runs"))
        self.record = os.environ.get("OSD_RECORD") == "1"
        self.stamp = object()
        self.episode = None
        self.enabled = False
        self.log_lock = threading.Lock()

    def refresh(self):
        try:
            stat = self.plan_path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        except FileNotFoundError:
            self.enabled = False
            self.stamp = None
            return
        if stamp == self.stamp:
            return
        self.stamp = stamp
        try:
            plan = read_plan(self.plan_path)
            if plan is None:
                self.enabled = False
                return
            catalog = load_catalog(self.catalog_dir / f"{plan.site}.yaml")
            if catalog.site != plan.site:
                raise ValueError("catalog site mismatch")
            if set(plan.target) - {entry["id"] for entry in catalog.entries}:
                raise ValueError("unknown target entry")
            if self.episode is None or self.episode.plan.episode_id != plan.episode_id:
                self.episode = Episode(plan, catalog)
            else:
                if self.episode.plan.site != plan.site:
                    raise ValueError("site change requires new episode")
                self.episode.plan = plan
                self.episode.catalog = catalog
            self.enabled = True
        except Exception as exc:
            self.enabled = False
            # Never log request/reply bodies or exception messages that could contain secrets.
            logging.getLogger(__name__).warning(
                "OSD plan/catalog rejected (%s); passing traffic through", type(exc).__name__)

    def append(self, episode, filename, record):
        try:
            with self.log_lock:
                directory = self.runs_dir / episode.plan.episode_id
                directory.mkdir(parents=True, exist_ok=True)
                with (directory / filename).open("a") as stream:
                    stream.write(json.dumps({"ts": time.time(), **record}) + "\n")
        except OSError:
            # Logging failures must not terminate the proxy or expose response data.
            pass

    def event(self, state, event, **values):
        self.append(state.episode, "proxy_events.jsonl", {
            "event": event, "episode_id": state.plan.episode_id,
            "matched_entry": state.entry["id"] if state.entry else None,
            "fault_applied": state.applied, **values,
        })

    def eligible(self, state):
        return [e for e in state.episode.catalog.entries if not state.plan.target or e["id"] in state.plan.target]

    def same_origin(self, state, flow):
        return origin_key(flow.request.pretty_url) == origin_key(state.episode.catalog.origin)

    def matches_view(self, entry, path):
        return any(re.search(pattern, path) for pattern in entry.get("f6", {}).get("views", []))

    def request(self, flow: http.HTTPFlow):
        self.refresh()
        if not self.enabled:
            return
        state = FlowState(self.episode, self.episode.plan, hashlib.sha256(flow.request.raw_content or b"").hexdigest())
        flow.metadata["osd"] = state
        self.append(state.episode, "proxy_requests.jsonl", {
            "flow_id": flow.id, "method": flow.request.method,
            "request_ts": flow.request.timestamp_start})
        try:
            state.skip = bool(flow.request.stream or flow.request.headers.get("upgrade", "").lower() == "websocket")
            if state.skip or not self.same_origin(state, flow):
                return
            if state.plan.fault == "F6" and flow.request.method == "GET":
                if self.stale_request(state, flow):
                    return
            for entry in self.eligible(state):
                if entry["method"] != flow.request.method or not re.search(entry["url_regex"], flow.request.path):
                    continue
                if "body_regex" in entry and not re.search(entry["body_regex"], flow.request.get_text(strict=False) or ""):
                    continue
                state.entry = entry
                break
            if state.entry is None or state.plan.fault == "C0" or state.episode.fired >= state.plan.params.get("fire_count", 1):
                return
            state.applied = state.plan.fault
            if state.applied == "F1":
                self.fake_ack(state, flow)
            elif state.applied == "F2":
                self.partial_commit(state, flow)
            elif state.applied == "F3":
                state.rollback_delay = state.entry.get("f3", {}).get("delay_s", 0.5)
            state.episode.fired += 1
            self.event(state, "fired", count=state.episode.fired, url=flow.request.pretty_url)
        except Exception as exc:
            self.event(state, "fallback", reason=type(exc).__name__)
            state.applied = None

    def fake_ack(self, state, flow):
        config = state.entry.get("f1", {"mode": "none"})
        if config["mode"] == "redirect":
            flow.response = http.Response.make(302, b"", {"Location": config["location"]})
        elif config["mode"] == "replay":
            try:
                recording = self.catalog_dir / "recordings" / state.plan.site / f'{state.entry["id"]}.json'
                data = json.loads(recording.read_text())
                headers = [(k.encode("latin1"), v.encode("latin1")) for k, v in data["headers"]
                           if k.lower() not in {"set-cookie", "date", "content-length", "transfer-encoding"}]
                flow.response = http.Response.make(data["status"], base64.b64decode(data["body"], validate=True), headers)
            except (OSError, ValueError, KeyError, TypeError):
                flow.response = None
        if flow.response is not None:
            state.forwarded = False
        else:
            state.applied = "F1->F3"
            state.rollback_delay = 0.5
            self.event(state, "fallback", reason="fake_ack_unavailable")

    def partial_commit(self, state, flow):
        request = flow.request
        config = state.entry.get("f2", {})
        kind = state.entry["content_type"]
        # Native Magento/Postmill forms use multipart; scripted URL-encoded
        # forms target the same entry and must retain their original handling.
        if kind == "form" and request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            kind = "multipart"
        body = request.content or b""
        if kind == "form":
            pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True)
            body = urlencode([(k, v) for k, v in pairs if k not in config.get("strip_fields", [])]).encode()
        elif kind == "multipart":
            body = strip_multipart(body, request.headers.get("content-type", ""), config.get("strip_fields", []))
        else:
            value = json.loads(body)
            for path in config.get("strip_json_paths", []):
                # GraphQL paths may be relative to variables, or explicitly variables.*.
                target = value.get("variables", {}) if kind == "graphql" and not path.startswith("variables.") else value
                strip_path(target, path)
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        request.content = body  # mitmproxy updates Content-Length and content encoding.
        request.headers.pop("transfer-encoding", None)
        request.headers["content-length"] = str(len(request.raw_content))

    def stale_request(self, state, flow):
        for window in reversed(state.episode.windows):
            if not self.matches_view(window["entry"], flow.request.path):
                continue
            if window["remaining"] <= 0 and time.monotonic() >= window["until"]:
                continue
            state.entry = window["entry"]
            state.applied = "F6"
            window["remaining"] = max(0, window["remaining"] - 1)
            for header in ("if-none-match", "if-modified-since"):
                flow.request.headers.pop(header, None)
            cached = window["cache"].get(flow.request.pretty_url)
            if cached is None:
                self.event(state, "stale_miss")
            else:
                flow.response = cached.copy()
                state.forwarded = False
                self.event(state, "stale_served")
            return True
        return False

    def responseheaders(self, flow: http.HTTPFlow):
        # Chunk framing does not imply an indefinite body (PHP uses it for HTML).
        # mitmproxy's stream_large_bodies also handles unknown/chunked lengths.
        content_type = flow.response.headers.get("content-type", "").lower()
        length = flow.response.headers.get("content-length", "0")
        if (content_type.startswith("text/event-stream")
                or (length.isdigit() and int(length) > 20 * 1024 * 1024)):
            flow.response.stream = True

    def configure(self, updated):
        from mitmproxy import ctx
        if ctx.options.stream_large_bodies is None:
            ctx.options.update(stream_large_bodies="20m")

    def response(self, flow: http.HTTPFlow):
        state = flow.metadata.get("osd")
        if state is None:
            return
        try:
            if state.skip or flow.response.stream or flow.websocket is not None:
                if state.applied:
                    self.event(state, "fallback", reason="streaming_or_websocket")
                    state.applied = None
                return
            if self.record and state.entry and state.forwarded:
                self.record_response(state, flow)
            if state.applied in {"F3", "F1->F3"}:
                self.schedule_rollback(state, flow)
            elif state.applied == "F4":
                flow.response = http.Response.make(502, b"<html><head><title>502 Bad Gateway</title></head>\r\n<body><center><h1>502 Bad Gateway</h1></center><hr><center>nginx</center></body></html>\r\n", {"Content-Type": "text/html"})
            elif state.applied == "F5":
                config = state.entry["f5"]
                body = (state.episode.catalog.directory / config["template"]).read_bytes()
                cookies = flow.response.headers.get_all("set-cookie")
                flow.response = http.Response.make(config.get("status", 200), body, {"Content-Type": config.get("content_type", "text/html; charset=utf-8")})
                flow.response.headers.set_all("set-cookie", cookies)
            elif state.applied == "F6" and flow.request.method != "GET":
                config = state.entry.get("f6", {})
                state.episode.windows.append({"entry": state.entry, "cache": dict(state.episode.cache),
                    "remaining": config.get("stale_reads", 2), "until": time.monotonic() + config.get("stale_seconds", 30)})
            if (state.plan.fault == "F6" and flow.request.method == "GET" and flow.response.status_code == 200
                    and state.forwarded and self.same_origin(state, flow)
                    and any(self.matches_view(e, flow.request.path) for e in self.eligible(state))):
                state.episode.cache[flow.request.pretty_url] = flow.response.copy()
        except Exception as exc:
            self.event(state, "fallback", reason=type(exc).__name__)
        finally:
            self.log_flow(state, flow)

    def record_response(self, state, flow):
        response = flow.response
        path = self.catalog_dir / "recordings" / state.plan.site / f'{state.entry["id"]}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"status": response.status_code, "headers": [
            [k, v] for k, v in response.headers.items(multi=True)
            if k.lower() not in {"set-cookie", "date", "content-length"}],
            "body": base64.b64encode(response.raw_content or b"").decode("ascii")}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data) + "\n")
        temporary.replace(path)

    def schedule_rollback(self, state, flow):
        summary = {"method": flow.request.method, "url": flow.request.pretty_url,
                   "origin": state.episode.catalog.origin, "status": flow.response.status_code,
                   "location": flow.response.headers.get("location"), "req_body_sha256": state.request_hash}
        def rollback():
            try:
                module, function = state.entry["f3"]["compensator"].split(":")
                getattr(importlib.import_module(module), function)(state.plan, state.entry, summary)
                self.event(state, "rollback_done")
            except Exception as exc:
                self.event(state, "rollback_error", error_type=type(exc).__name__)
        self.event(state, "rollback_scheduled", delay_s=state.rollback_delay)
        timer = threading.Timer(state.rollback_delay, rollback)
        timer.daemon = True
        timer.start()

    def log_flow(self, state, flow):
        if state.logged:
            return
        state.logged = True
        self.append(state.episode, "proxy.jsonl", {
            "flow_id": flow.id,
            "method": flow.request.method, "url": flow.request.pretty_url,
            "request_ts": flow.request.timestamp_start,
            # Selected navigation metadata only; never log request headers.
            "document_navigation": (flow.request.headers.get('sec-fetch-dest') == 'document'
                if flow.request.headers.get('sec-fetch-dest') else
                (flow.request.headers.get('upgrade-insecure-requests') == '1'
                 and 'text/html' in flow.request.headers.get('accept', ''))),
            "redirect_url": flow.response.headers.get('location') if flow.response else None,
            "status": flow.response.status_code if flow.response else None,
            "matched_entry": state.entry["id"] if state.entry else None,
            "fault_applied": state.applied, "forwarded": state.forwarded,
            "req_body_sha256": state.request_hash,
            "resp_bytes": len(flow.response.raw_content or b"") if flow.response else 0,
        })

    def error(self, flow: http.HTTPFlow):
        state = flow.metadata.get("osd")
        if state:
            self.log_flow(state, flow)


addons = [FaultProxy()]
