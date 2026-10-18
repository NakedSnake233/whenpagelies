"""Small threaded shop with directly inspectable backend state; no dependencies."""
import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlsplit


class Shop(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, Handler)
        self.lock = threading.RLock()
        self.items = {}
        self.cart = []
        self.next_id = 1
        self.requests = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body=b"", content_type="text/html; charset=utf-8", headers=()):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        with self.server.lock:
            self.server.requests.append({"method": "GET", "path": self.path,
                                         "conditional": any(h in self.headers for h in ("If-None-Match", "If-Modified-Since"))})
            if path == "/api/state":
                self.reply(200, json.dumps({"items": list(self.server.items.values()), "cart": self.server.cart,
                                           "cart_count": sum(x.get("qty", 0) for x in self.server.cart),
                                           "requests": self.server.requests}), "application/json")
            elif path == "/items":
                self.reply(200, "<html><ul>" + "".join(f'<li>{escape(x["name"])} ({x["qty"]})</li>' for x in self.server.items.values()) + "</ul></html>",
                           headers=[("ETag", '"toy-items"')])
            elif path.startswith("/items/") and path[7:].isdigit() and int(path[7:]) in self.server.items:
                item = self.server.items[int(path[7:])]
                self.reply(200, f'<html><h1>{escape(item["name"])}</h1><p>{item["qty"]}</p><p>{escape(item["note"])}</p></html>')
            else:
                self.reply(404, "Not Found")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        path = urlsplit(self.path).path
        with self.server.lock:
            self.server.requests.append({"method": "POST", "path": self.path, "body_bytes": len(body)})
            try:
                if path == "/api/cart":
                    value = json.loads(body)
                    self.server.cart.append(value)
                    self.reply(200, json.dumps({"ok": True, "cart_count": sum(x.get("qty", 0) for x in self.server.cart)}), "application/json")
                elif path == "/admin/rollback-last":
                    if self.server.items:
                        del self.server.items[max(self.server.items)]
                    self.reply(200, '{"ok":true}', "application/json")
                elif path == "/items":
                    form = parse_qs(body.decode(), keep_blank_values=True)
                    item_id = self.server.next_id
                    item = {"id": item_id, "name": form.get("name", [""])[0],
                            "qty": int(form.get("qty", ["1"])[0]), "note": form.get("note", [""])[0]}
                    self.server.next_id += 1
                    self.server.items[item_id] = item
                    self.reply(302, headers=[("Location", f"/items/{item_id}"), ("Set-Cookie", "toy_session=present; Path=/"), ("Set-Cookie", "toy_notice=created; Path=/")])
                elif path.startswith("/items/") and path.endswith("/edit"):
                    item = self.server.items[int(path.split("/")[2])]
                    form = parse_qs(body.decode(), keep_blank_values=True)
                    if "name" in form:
                        item["name"] = form["name"][0]
                    if "qty" in form:
                        item["qty"] = int(form["qty"][0])
                    self.reply(302, headers=[("Location", f'/items/{item["id"]}')])
                else:
                    self.reply(404, "Not Found")
            except (ValueError, KeyError, TypeError):
                self.reply(400, "Bad Request")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    Shop(("127.0.0.1", args.port)).serve_forever()
