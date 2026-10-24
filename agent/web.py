"""Local web interface. Only reachable from this computer (127.0.0.1).

The page (web/index.html) calls a small JSON API; every action goes through core.handle().
"""
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from . import config, core, demo
from .mail import Gmail

PAGE = Path(__file__).parent / "web" / "index.html"


class App:
    """State shared by all requests: settings path, current job, last result."""

    def __init__(self, config_path, demo_mode=False):
        self.config_path = Path(config_path)
        self.demo = demo_mode
        self.token = secrets.token_urlsafe(24)  # the page must send it back: blocks other sites
        self.lock = threading.Lock()
        self.job = {"running": False, "done": 0, "total": 0}
        self.last = None

    def cfg(self):
        if self.demo:
            cfg = yaml.safe_load(config.EXAMPLE.read_text(encoding="utf-8"))
            cfg["gmail"].update(email="demo@gmail.com", app_password="demo")
            config.check(cfg)
            return cfg
        return config.load(self.config_path)

    def gmail(self):
        return demo.gmail_factory if self.demo else Gmail

    def state(self):
        try:
            cfg, error = self.cfg(), ""
        except config.ConfigError as e:
            cfg, error = None, str(e)
        g = (cfg or {}).get("gmail", {})
        m = (cfg or {}).get("model", {})
        return {
            "demo": self.demo,
            "configured": bool(g.get("email") and g.get("app_password")),
            "config_error": error if self.config_path.exists() else "",
            "email": g.get("email", ""), "has_password": bool(g.get("app_password")),
            "mailbox": g.get("mailbox", "INBOX"),
            "provider": m.get("provider", "anthropic"), "model_name": m.get("model_name", ""),
            "has_api_key": bool(m.get("api_key")),
            "themes": config.names(cfg["themes"]) if cfg else [],
            "statuses": config.names(cfg["statuses"]) if cfg else [],
            "default_models": config.DEFAULT_MODELS,
            "job": dict(self.job), "last": self.last,
            "log": core.read_log(40),
        }

    def start_preview(self, limit):
        with self.lock:
            if self.job["running"]:
                return False
            self.job = {"running": True, "done": 0, "total": limit}
        cfg = self.cfg()

        def progress(i, n, row):
            self.job.update(done=i, total=n)

        def run():
            result = core.handle({"action": "preview", "limit": limit}, cfg=cfg,
                                 gmail_factory=self.gmail(),
                                 ask=demo.ask if self.demo else None, progress=progress)
            self.last = result
            self.job["running"] = False
        threading.Thread(target=run, daemon=True).start()
        return True


def make_handler(app, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console quiet
            pass

        def send(self, code, body, kind="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def trusted(self):
            return (self.headers.get("Host") in allowed_hosts
                    and self.headers.get("X-Adagio-Token") == app.token)

        def do_GET(self):
            if self.headers.get("Host") not in allowed_hosts:
                return self.send(403, {"error": "forbidden"})
            if self.path in ("/", "/index.html"):
                html = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                return self.send(200, html.encode(), "text/html; charset=utf-8")
            if not self.trusted():
                return self.send(403, {"error": "forbidden"})
            if self.path == "/api/state":
                return self.send(200, app.state())
            if self.path == "/api/hardware":
                return self.send(200, core.handle({"action": "hardware"}))
            self.send(404, {"error": "not found"})

        def do_POST(self):
            if not self.trusted():
                return self.send(403, {"error": "forbidden"})
            try:
                size = min(int(self.headers.get("Content-Length") or 0), 100_000)
                body = json.loads(self.rfile.read(size) or b"{}")
            except ValueError:
                return self.send(400, {"ok": False, "error": "Invalid request"})
            if self.path == "/api/settings":
                if app.demo:
                    return self.send(200, {"ok": False, "error": "Settings are off in demo mode."})
                try:
                    config.save_settings(app.config_path, body.get("email", ""),
                                         body.get("provider", "anthropic"),
                                         body.get("model_name", ""), body.get("app_password", ""),
                                         body.get("api_key", ""))
                    core.log("settings saved")
                    return self.send(200, {"ok": True})
                except config.ConfigError as e:
                    return self.send(200, {"ok": False, "error": str(e)})
            if self.path == "/api/check":
                try:
                    cfg = app.cfg()
                except config.ConfigError as e:
                    return self.send(200, {"ok": False, "error": str(e)})
                return self.send(200, core.handle({"action": "check"}, cfg=cfg,
                                                  gmail_factory=app.gmail()))
            if self.path == "/api/preview":
                limit = max(1, min(int(body.get("limit") or 20), 1000))
                try:
                    started = app.start_preview(limit)
                except config.ConfigError as e:
                    return self.send(200, {"ok": False, "error": str(e)})
                return self.send(200, {"ok": started,
                                       **({} if started else {"error": "A run is already going"})})
            self.send(404, {"error": "not found"})

    return Handler


def serve(config_path=config.DEFAULT_PATH, port=8765, demo_mode=False, open_browser=True):
    app = App(config_path, demo_mode)
    for p in range(port, port + 20):  # first free port
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), make_handler(app, p))
            break
        except OSError:
            continue
    else:
        raise OSError("No free port found")
    url = f"http://127.0.0.1:{p}/"
    print(f"\n  Mail Adagio is open at {url}\n  Keep this window open. Close it to stop.\n")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
