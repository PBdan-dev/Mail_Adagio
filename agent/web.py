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

from . import config, core, demo, models
from .mail import Gmail

PAGE = Path(__file__).parent / "web" / "index.html"


class App:
    """State shared by all requests: settings path, the job running in the background."""

    def __init__(self, config_path, demo_mode=False):
        self.config_path = Path(config_path)
        self.demo = demo_mode
        self.token = secrets.token_urlsafe(24)  # the page must send it back: blocks other sites
        self.lock = threading.Lock()
        self.job = {"running": False, "kind": "", "done": 0, "total": 0, "model": "", "stop": False}
        self.error = ""      # why the last background job failed
        self.notice = None   # what the last apply/undo did
        self.demo_behavior = {"act_alone": False, "batch_size": 50}

    def cfg(self):
        if self.demo:
            cfg = yaml.safe_load(config.EXAMPLE.read_text(encoding="utf-8"))
            cfg["gmail"].update(email="demo@gmail.com", app_password="demo")
            cfg["model"].update(provider="ollama", model_name="demo-model")
            cfg["behavior"].update(self.demo_behavior)
            config.check(cfg)
            return cfg
        return config.load(self.config_path)

    def gmail(self):
        return demo.gmail_factory if self.demo else Gmail

    def call(self, request, progress=None, cfg=None):
        return core.handle(request, cfg=cfg or self.cfg(), gmail_factory=self.gmail(),
                           ask=demo.ask if self.demo else None, progress=progress)

    def state(self):
        try:
            cfg, error = self.cfg(), ""
        except config.ConfigError as e:
            cfg, error = None, str(e)
        g = (cfg or {}).get("gmail", {})
        m = (cfg or {}).get("model", {})
        job, stopping, error, notice = dict(self.job), self.job["stop"], self.error, self.notice  # before reading
        sort = self.call({"action": "status"}, cfg=cfg) if cfg else None  # the database, never older than job
        job.pop("stop")
        return {
            "demo": self.demo,
            "configured": bool(g.get("email") and g.get("app_password")),
            "config_error": error if self.config_path.exists() else "",
            "email": g.get("email", ""), "has_password": bool(g.get("app_password")),
            "mailbox": g.get("mailbox", "INBOX"),
            "provider": m.get("provider", "anthropic"), "model_name": m.get("model_name", ""),
            "base_url": m.get("base_url", ""),
            "has_api_key": bool(m.get("api_key")),
            "themes": config.names(cfg["themes"]) if cfg else [],
            "statuses": config.names(cfg["statuses"]) if cfg else [],
            "default_models": config.DEFAULT_MODELS,
            "job": job, "stopping": stopping, "error": error, "notice": notice,
            "sort": sort if sort and sort.get("ok") else None,
            "log": core.read_log(40),
        }

    def model_info(self):
        """Which model will sort the mails, or why none can. Also what Ollama has installed."""
        info = {"ok": True, "note": "", "error": "", "installed": [], "running": []}
        try:
            cfg = self.cfg()
            if self.demo:
                info["note"] = "demo-model · demo"
                return info
            if cfg["model"]["provider"] == "ollama":
                try:
                    info.update(models.ollama_models(cfg["model"].get("base_url")))
                except models.ModelError:
                    pass
            info["note"] = core.resolve_model(cfg)[1]
        except (config.ConfigError, models.ModelError) as e:
            info.update(ok=False, error=str(e))
        return info

    def save_behavior(self, body):
        if self.demo:
            if "act_alone" in body:
                self.demo_behavior["act_alone"] = bool(body["act_alone"])
            if body.get("batch_size"):
                self.demo_behavior["batch_size"] = max(50, min(int(body["batch_size"]), 1000))
            return
        config.save_behavior(self.config_path, body.get("act_alone"), body.get("batch_size"))

    def start(self, kind, work):
        """Run `work` in the background; the page follows it through /api/state."""
        with self.lock:
            if self.job["running"]:
                return False
            self.job = {"running": True, "kind": kind, "done": 0, "total": 0, "model": "", "stop": False}
            self.error = ""

        def run():
            try:
                work()
            except Exception as e:  # shown on the page, the server keeps running
                self.error = f"{type(e).__name__}: {e}"
            finally:
                self.job["running"] = False
        threading.Thread(target=run, daemon=True).start()
        return True

    def progress(self, i, n, row):
        if i == 0:
            self.job["model"] = row["model"]
        else:
            self.job.update(done=i, total=n)

    def start_plan(self, body):
        """Analyse the next batch. With "act alone", apply it and go on until the end or Stop."""
        cfg = self.cfg()
        request = {"action": "plan", "scope": body.get("scope"),
                   "batch_size": body.get("batch_size") or cfg["behavior"]["batch_size"],
                   "restart": bool(body.get("restart"))}
        if self.demo:
            request["model_note"] = "demo-model · demo"
        auto = cfg["behavior"]["act_alone"]

        def work():
            while True:
                self.job.update(done=0, total=0, kind="auto" if auto else "plan")
                r = self.call(request, progress=self.progress, cfg=cfg)
                request["restart"] = False
                if not r["ok"]:
                    self.error = r["error"]
                    return
                if not auto:
                    return
                if r.get("applied") is not None:
                    self.notice = {"kind": "apply", **{k: r[k] for k in ("applied", "moved", "skipped")}}
                if self.job["stop"] or not r.get("progress") or r["progress"].get("finished"):
                    return
        return self.start("auto" if auto else "plan", work)

    def start_apply(self, body):
        cfg = self.cfg()

        def work():
            r = self.call({"action": "apply", "batch_id": body.get("batch_id"), "skip": body.get("skip") or [],
                           "batch_size": cfg["behavior"]["batch_size"]}, cfg=cfg)
            if r["ok"]:
                self.notice = {"kind": "apply", **{k: r[k] for k in ("applied", "moved", "skipped")}}
            else:
                self.error = r["error"]
        return self.start("apply", work)

    def start_simple(self, kind, request):
        """Scan or suggest: one call in the background, with progress."""
        cfg = self.cfg()
        if self.demo:
            request["model_note"] = "demo-model · demo"

        def work():
            r = self.call(request, progress=self.progress, cfg=cfg)
            if r["ok"]:
                self.notice = {"kind": kind, **{k: r[k] for k in ("mails", "senders", "with_unsubscribe",
                                                                  "suggested", "checked") if k in r}}
            else:
                self.error = r["error"]
        return self.start(kind, work)

    def start_undo(self):
        cfg = self.cfg()

        def work():
            r = self.call({"action": "undo"}, cfg=cfg)
            if r["ok"]:
                self.notice = {"kind": "undo", "undone": r["undone"], "back_in_inbox": r["back_in_inbox"]}
            else:
                self.error = r["error"]
        return self.start("undo", work)


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
            if self.path == "/api/model":
                return self.send(200, app.model_info())
            if self.path == "/api/rules":
                return self.send(200, app.call({"action": "rules"}))
            if self.path == "/api/senders":
                return self.send(200, app.call({"action": "senders"}))
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
                                         body.get("api_key", ""), body.get("base_url"))
                    core.log("settings saved")
                    return self.send(200, {"ok": True})
                except config.ConfigError as e:
                    return self.send(200, {"ok": False, "error": str(e)})
            try:
                return self.post(body)
            except config.ConfigError as e:
                return self.send(200, {"ok": False, "error": str(e)})

        def post(self, body):
            simple = {"/api/correct": "correct", "/api/rules/delete": "delete_rule",
                      "/api/estimate": "estimate", "/api/discard": "discard",
                      "/api/sender": "sender_state", "/api/rule": "add_rule"}
            if self.path in simple:
                return self.send(200, app.call({**body, "action": simple[self.path]}))
            if self.path == "/api/check":
                try:
                    cfg = app.cfg()
                except config.ConfigError as e:
                    return self.send(200, {"ok": False, "error": str(e)})
                return self.send(200, core.handle({"action": "check"}, cfg=cfg,
                                                  gmail_factory=app.gmail()))
            if self.path == "/api/behavior":
                app.save_behavior(body)
                return self.send(200, {"ok": True})
            if self.path == "/api/stop":
                app.job["stop"] = True
                return self.send(200, {"ok": True})
            starters = {"/api/plan": lambda: app.start_plan(body), "/api/apply": lambda: app.start_apply(body),
                        "/api/undo": app.start_undo,
                        "/api/scan": lambda: app.start_simple("scan", {"action": "scan"}),
                        "/api/suggest": lambda: app.start_simple("suggest", {"action": "suggest",
                                                                             "top": body.get("top") or 20})}
            if self.path in starters:
                started = starters[self.path]()
                return self.send(200, {"ok": started, **({} if started else {"error": "Busy, try again in a moment"})})
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
