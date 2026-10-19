"""The Factory Console: a local, dependency-free web server for the factory's UI.

Security model: binds to loopback by default; rejects requests whose Host is not this server (DNS rebinding);
the only writes (messages to agents, backlog items) need a per-run token embedded in the page plus a matching
Origin; artifact reads are confined to the job folder; strict CSP; no third-party code is loaded.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import secrets
import shutil
import sys
import tempfile
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import console, projects
from .engine import Engine
from .paths import RepoPaths, plugin_path
from .store import Store
from .util import FactoryError

UI_ROOT = plugin_path("ui")
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                ".ico": "image/x-icon", ".woff2": "font/woff2", ".json": "application/json"}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
MAX_BODY = 64_000


class ConsoleState:
    def __init__(self, paths: RepoPaths | None, demo_root: Path | None = None):
        self.default_paths = paths
        self.demo_root = demo_root
        self.token = secrets.token_urlsafe(24)
        self.allowed_hosts: set[str] = set()
        self.settings_cache: tuple[float, dict] | None = None
        self.lock = threading.Lock()

    def paths_for(self, project: str | None) -> RepoPaths:
        if project:
            if self.demo_root is not None:
                raise FactoryError("demo mode serves one project")
            known = {p["path"] for p in projects.load()}
            if project not in known:
                raise FactoryError("unknown project")
            return RepoPaths(Path(project))
        if self.default_paths is None:
            raise FactoryError("no repository")
        return self.default_paths


class Handler(BaseHTTPRequestHandler):
    server_version = "FactoryConsole"
    sys_version = ""
    state: ConsoleState  # set on the subclass created in make_server

    # ---- plumbing ----------------------------------------------------------------------------
    def log_message(self, fmt, *args):  # quiet by default
        if getattr(self.server, "verbose", False):
            sys.stderr.write("console: " + (fmt % args) + "\n")

    def _headers(self, status: int, ctype: str, length: int, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, data, status: int = 200, etag: bool = False) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        extra = {"Cache-Control": "no-store"}
        if etag:
            tag = '"' + hashlib.sha1(body).hexdigest()[:20] + '"'
            extra["ETag"] = tag
            if self.headers.get("If-None-Match") == tag:
                self._headers(HTTPStatus.NOT_MODIFIED, "application/json", 0, extra)
                return
        self._headers(status, "application/json; charset=utf-8", len(body), extra)
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status=status)

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").lower() in self.state.allowed_hosts

    def _write_allowed(self) -> bool:
        if self.headers.get("X-Factory-Token") != self.state.token:
            return False
        origin = self.headers.get("Origin")
        if origin and origin.lower() not in {f"http://{h}" for h in self.state.allowed_hosts}:
            return False
        return (self.headers.get("Content-Type") or "").startswith("application/json")

    # ---- routing -----------------------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._error(HTTPStatus.MISDIRECTED_REQUEST, "unexpected Host header")
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        parts = [unquote(p) for p in url.path.split("/") if p]
        try:
            if not parts or parts == ["index.html"]:
                return self._index()
            if parts[0] == "assets":
                return self._static(parts[1:])
            if parts[0] != "api":
                return self._index()  # client-side routes
            project = (qs.get("project") or [None])[0]
            paths = None if parts[1:2] in (["catalog"],) else self.state.paths_for(project)
            route = parts[1:]
            if route == ["state"]:
                data = console.repo_state(paths)
                data["demo"] = self.state.demo_root is not None
                data["projects"] = [] if self.state.demo_root else projects.load()
                return self._json(data, etag=True)
            if route == ["catalog"]:
                return self._json(console.catalog_payload(), etag=True)
            if route == ["settings"]:
                return self._json(self._settings(paths), etag=True)
            if len(route) >= 2 and route[0] == "jobs":
                job_id = route[1]
                if len(route) == 2:
                    return self._json(console.job_detail(paths, job_id), etag=True)
                if route[2:] == ["diff"]:
                    return self._json(console.diff(paths, job_id), etag=True)
                if route[2:] == ["artifact"]:
                    job_dir, _ = console.job_dir_for(paths, job_id)
                    return self._json(console.read_artifact(job_dir, (qs.get("path") or [""])[0]), etag=True)
                if len(route) == 5 and route[2] == "agents" and route[4] == "activity":
                    after = int((qs.get("after") or ["0"])[0] or 0)
                    extra = [self.state.demo_root] if self.state.demo_root else None
                    return self._json(console.activity(paths, job_id, route[3], after=after, extra_roots=extra),
                                      etag=True)
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        except FactoryError as exc:
            return self._error(HTTPStatus.NOT_FOUND if "no " in str(exc) or "unknown" in str(exc)
                               else HTTPStatus.BAD_REQUEST, str(exc))
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "bad request")

    def do_POST(self):  # noqa: N802
        if not self._host_ok():
            return self._error(HTTPStatus.MISDIRECTED_REQUEST, "unexpected Host header")
        if not self._write_allowed():
            return self._error(HTTPStatus.FORBIDDEN, "missing or invalid console token")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body too large or empty")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "invalid JSON")
        if not isinstance(body, dict):
            return self._error(HTTPStatus.BAD_REQUEST, "invalid JSON")
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        parts = [unquote(p) for p in url.path.split("/") if p]
        try:
            paths = self.state.paths_for((qs.get("project") or [None])[0])
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "messages":
                to = body.get("to") if isinstance(body.get("to"), dict) else {}
                eng = Engine(paths, session="console")
                msg = eng.tell(parts[2], str(body.get("text", "")), step=to.get("step"), agent=to.get("agent"),
                               role=to.get("role"))
                return self._json(msg, status=HTTPStatus.CREATED)
            if parts == ["api", "backlog"]:
                text = str(body.get("text", "")).strip()
                if not text or "\n" in text or len(text) > 500:
                    raise FactoryError("a backlog item is one line of up to 500 characters")
                from .cli import BACKLOG_HEADER, _backlog_path
                from .paths import ensure_state_dir
                from .util import file_lock, write_text_atomic

                ensure_state_dir(paths)
                path = _backlog_path(paths)
                with file_lock(paths.state / ".backlog.lock"):
                    current = path.read_text(encoding="utf-8") if path.exists() else BACKLOG_HEADER
                    if not current.endswith("\n"):
                        current += "\n"
                    write_text_atomic(path, current + f"- [ ] {text}\n")
                return self._json({"added": text}, status=HTTPStatus.CREATED)
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        except FactoryError as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))

    # ---- views --------------------------------------------------------------------------------
    def _index(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        html = html.replace("__FACTORY_TOKEN__", self.state.token)
        html = html.replace("__FACTORY_MODE__", "demo" if self.state.demo_root else "live")
        body = html.encode("utf-8")
        self._headers(HTTPStatus.OK, STATIC_TYPES[".html"], len(body), {"Cache-Control": "no-store"})
        self.wfile.write(body)

    def _static(self, parts: list[str]):
        if not parts or any(p in ("", ".", "..") or p.startswith(".") for p in parts):
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        target = (UI_ROOT / Path(*parts)).resolve()
        if UI_ROOT.resolve() not in target.parents or not target.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        ctype = STATIC_TYPES.get(target.suffix) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        body = target.read_bytes()
        self._headers(HTTPStatus.OK, ctype, len(body), {"Cache-Control": "no-cache"})
        self.wfile.write(body)

    def _settings(self, paths):
        with self.state.lock:
            cached = self.state.settings_cache
            if cached and time.time() - cached[0] < 60:
                return cached[1]
            data = console.settings(paths)
            data["demo"] = self.state.demo_root is not None
            self.state.settings_cache = (time.time(), data)
            return data


def make_server(paths: RepoPaths | None, host: str = "127.0.0.1", port: int = 0,
                demo_root: Path | None = None) -> tuple[ThreadingHTTPServer, ConsoleState]:
    state = ConsoleState(paths, demo_root)
    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    real_port = httpd.server_address[1]
    hosts = {f"{host}:{real_port}", f"localhost:{real_port}", f"127.0.0.1:{real_port}", f"[::1]:{real_port}"}
    state.allowed_hosts = {h.lower() for h in hosts}
    return httpd, state


def serve(paths: RepoPaths | None, host: str = "127.0.0.1", port: int = 0, open_browser: bool = False,
          demo: bool = False) -> int:
    demo_root = None
    if demo:
        from . import demo as demo_mod

        demo_root = Path(tempfile.mkdtemp(prefix="factory-demo-"))
        paths = demo_mod.build(demo_root)
    if host not in ("127.0.0.1", "localhost", "::1"):
        sys.stderr.write(f"warning: the console is binding to {host}; anyone who can reach it can read job state\n")
    httpd, _state = make_server(paths, host, port, demo_root)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    sys.stdout.write(json.dumps({"url": url, "repo": str(paths.root) if paths else None, "demo": demo}) + "\n")
    sys.stdout.flush()
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if demo_root is not None:
            shutil.rmtree(demo_root, ignore_errors=True)
    return 0


def store_for(paths: RepoPaths) -> Store:
    return Store(paths)
