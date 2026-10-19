"""Loopback-only Hub UI. No subprocess/model execution from the browser."""
from __future__ import annotations

import hmac
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs, urlparse

from state_store import StateStore


STATIC = Path(__file__).parent / "hub_ui"


def make_handler(store: StateStore, secret: str, csrf: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CCGHub/0.1"

        def log_message(self, fmt, *args):
            # No request URLs or tokens in stdout logs.
            return

        def security_headers(self):
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

        def reply(self, code: int, data, *, content_type: str = "application/json; charset=utf-8", headers=None):
            body = data.encode("utf-8") if isinstance(data, str) else json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers or []:
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authed(self):
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
                key = cookies.get("CCG_SESSION")
                return key is not None and hmac.compare_digest(key.value, secret)
            except Exception:
                return False

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/login":
                supplied = parse_qs(parsed.query).get("token", [""])[0]
                if not hmac.compare_digest(supplied, secret):
                    self.reply(HTTPStatus.FORBIDDEN, {"error": "Invalid one-time access token"})
                    return
                self.send_response(HTTPStatus.SEE_OTHER)
                self.security_headers()
                self.send_header("Set-Cookie", "CCG_SESSION=" + secret + "; Path=/; HttpOnly; SameSite=Strict")
                self.send_header("Location", "/")
                self.end_headers()
                return
            if not self.authed():
                self.reply(HTTPStatus.UNAUTHORIZED, {"error": "Use one-time localhost login URL displayed by the server"})
                return
            if path in {"/", "/index.html", "/app.js", "/style.css"}:
                name = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path]
                mime = "text/html" if name.endswith("html") else "text/javascript" if name.endswith("js") else "text/css"
                self.reply(200, (STATIC / name).read_text(encoding="utf-8"), content_type=mime + "; charset=utf-8")
                return
            if path == "/api/session":
                self.reply(200, {"csrf": csrf, "project_id": store.project_id,
                                 "chat_enabled": store.chat_enabled()})
                return
            if path == "/api/state":
                self.reply(200, {"project": store.project_id, "team_chat_enabled": store.chat_enabled(),
                                 "departments": store.departments()})
                return
            if path == "/api/messages":
                q = parse_qs(parsed.query)
                department = q.get("department", ["all"])[0]
                try:
                    after = max(0, int(q.get("after", ["0"])[0]))
                    items = store.messages(department, after=after)
                except (ValueError, TypeError):
                    self.reply(400, {"error": "Invalid query"})
                    return
                self.reply(200, {"messages": items})
                return
            if path == "/api/checkpoints":
                self.reply(200, {"checkpoints": store.checkpoints(20)})
                return
            if path == "/api/stream":
                # EventSource reconnects automatically. Poll DB without any LLM calls.
                self.send_response(200)
                self.security_headers()
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Connection", "close")
                self.end_headers()
                start = time.monotonic()
                try:
                    after = parse_qs(parsed.query).get("after", [self.headers.get("Last-Event-ID", "0")])[0]
                    latest = max(0, int(after))
                except (ValueError, TypeError):
                    latest = 0
                try:
                    while time.monotonic() - start < 28 and store.chat_enabled():
                        with store.connection() as db:
                            rows = db.execute("SELECT seq,kind FROM events WHERE project_id=? AND seq>? ORDER BY seq LIMIT 40", (store.project_id, latest)).fetchall()
                        for r in rows:
                            latest = int(r[0])
                            self.wfile.write(f"id: {latest}\ndata: {json.dumps({'kind': r[1], 'seq': latest})}\n\n".encode())
                        if rows:
                            self.wfile.flush()
                        else:
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                        time.sleep(1.0)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                self.close_connection = True
                return
            self.reply(404, {"error": "Not found"})

        def do_POST(self):
            if not self.authed():
                self.reply(HTTPStatus.UNAUTHORIZED, {"error": "Authentication required"})
                return
            # Browser-originated actions cannot execute commands or claim Sol identity.
            origin = self.headers.get("Origin", "")
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if origin != expected or not hmac.compare_digest(self.headers.get("X-CCG-CSRF", ""), csrf):
                self.reply(HTTPStatus.FORBIDDEN, {"error": "Invalid Origin/CSRF"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 2 or size > 16000:
                    raise ValueError("Invalid length")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Object expected")
            except (ValueError, json.JSONDecodeError):
                self.reply(HTTPStatus.BAD_REQUEST, {"error": "Invalid body"})
                return
            if self.path == "/api/operator-message":
                if not store.chat_enabled():
                    self.reply(409, {"error": "Chat OFF"})
                    return
                try:
                    msg = store.post_message(str(data.get("department", "")), "operator", "operator",
                                             str(data.get("body", "")), provenance="browser-operator")
                except ValueError as e:
                    self.reply(400, {"error": str(e)})
                    return
                self.reply(201, {"message_id": msg, "delivery": "SAVED_NOT_SENT_TO_CODEX"})
                return
            self.reply(404, {"error": "Not found"})
    return Handler


def serve(store: StateStore, port: int = 0) -> None:
    if not store.chat_enabled():
        raise RuntimeError("Chat OFF")
    secret, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with ThreadingHTTPServer(("127.0.0.1", port), make_handler(store, secret, csrf)) as httpd:
        url = f"http://127.0.0.1:{httpd.server_port}/login?token={secret}"
        print("Continuity Hub (localhost only). Open this one-time URL:", url, flush=True)
        print("Team chat is ON; stop by Ctrl+C or project 'team off'. No CLI models are launched.", flush=True)
        httpd.timeout = 1.0
        try:
            while store.chat_enabled():
                httpd.handle_request()
        except KeyboardInterrupt:
            pass
        finally:
            # handle_request() exits once chat is OFF; other SSE threads are daemon threads.
            pass
