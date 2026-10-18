"""Real HTTP/1 chunk framing must not disable acknowledgement faults."""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest
from test_proxy_toy import Harness


@pytest.mark.parametrize("fault", ["F4", "F5", "F6"])
def test_chunked_faults(tmp_path, fault):
    h = Harness(tmp_path)
    values = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def reply(self, body):
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")

        def do_GET(self):
            self.reply(("<html>" + ",".join(values) + "</html>").encode())

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            values.append("committed")
            self.reply(b"<html>saved</html>")

    server = ThreadingHTTPServer(
        ("127.0.0.1", int(h.origin.rsplit(":", 1)[1])), Handler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Harness normally also launches the toy; start only its proxy here.
    import os, subprocess, time

    env = dict(
        os.environ,
        OSD_PLAN=str(h.plan_path),
        OSD_CATALOG_DIR=str(h.catalog_dir),
        OSD_RUNS_DIR=str(h.runs),
        OSD_RECORD="1",
    )
    h.set_plan(fault, ["create-item"])
    log = (tmp_path / "proxy.log").open("w")
    p = subprocess.Popen(
        [
            ".venv/bin/mitmdump",
            "--listen-host",
            "127.0.0.1",
            "--listen-port",
            str(h.proxy_port),
            "--set",
            f"confdir={tmp_path}/mitm",
            "-s",
            "proxy/osd_faults.py",
        ],
        env=env,
        stdout=log,
        stderr=log,
    )
    h.processes.append(p)
    h.files.append(log)
    from test_proxy_toy import eventually

    def ready():
        try:
            return h.client.get("/items").status_code == 200
        except Exception:
            return False

    try:
        eventually(ready)
        before = h.client.get("/items").content
        r = h.create()
        assert values == ["committed"]
        if fault == "F4":
            assert r.status_code == 502
        elif fault == "F5":
            assert "could not be saved" in r.text.lower()
        else:
            assert h.client.get("/items").content == before
            assert h.client.get("/items").content == before
            assert "committed" in h.client.get("/items").text
        recording = json.loads(
            (h.catalog_dir / "recordings/toy/create-item.json").read_text()
        )
        assert base64.b64decode(recording["body"]) == b"<html>saved</html>"
    finally:
        h.close()
        server.shutdown()
        server.server_close()
        thread.join()
