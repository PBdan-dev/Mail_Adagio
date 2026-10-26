"""Local web interface. Only reachable from this computer (127.0.0.1).

The page (web/index.html) calls a small JSON API; every action goes through core.handle().
"""
import json
import secrets
import socket
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from . import config, core, demo, models
from .mail import Gmail

PAGE = Path(__file__).parent / "web" / "index.html"
CODE = Path(__file__).parent


def code_stamp():
    """Changes when the files of Mail Adagio are replaced by a new version."""
    return max(f.stat().st_mtime for f in [*CODE.glob("*.py"), PAGE])


class App:
    """State shared by all requests: settings path, the job running in the background."""

    def __init__(self, config_path, demo_mode=False):
        self.config_path = Path(config_path)
        self.demo = demo_mode
        self.token = secrets.token_urlsafe(24)  # the page must send it back: blocks other sites
        self.lock = threading.Lock()
        self.job = {"running": False, "kind": "", "done": 0, "total": 0, "model": "", "stop": False}
        self.error = ""      # why the last background job failed
        self.error_code = ""  # gmail_login, ollama_down...: the page shows the right help
        self.notice = None   # what the last apply/undo did
        self.demo_behavior = {"act_alone": False, "batch_size": 50}
        self.demo_order = None
        self.demo_keywords = None
        self.demo_running = []
        self.page = PAGE.read_text(encoding="utf-8")  # read once: the page always matches this code
        self.stamp = code_stamp()
        self.server = None

    def cfg(self):
        if self.demo:
            cfg = yaml.safe_load(config.EXAMPLE.read_text(encoding="utf-8"))
            cfg["gmail"].update(email="demo@gmail.com", app_password="demo")
            cfg["model"].update(provider="ollama", model_name="demo-model")
            cfg["behavior"].update(self.demo_behavior)
            cfg["label_order"] = self.demo_order
            cfg["keywords"] = self.demo_keywords
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
        error_code = self.error_code
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
            "themes": config.order(cfg)[1] if cfg else [],      # in label order
            "statuses": config.order(cfg)[0] if cfg else [],
            "colors": config.colors(cfg) if cfg else {},
            "order": dict(zip(("statuses", "themes"), config.order(cfg))) if cfg else None,
            "numbered": config.numbered(cfg) if cfg else False,
            "keywords": config.keywords(cfg) if cfg else {},
            "default_keywords": config.DEFAULT_KEYWORDS,
            "default_models": config.DEFAULT_MODELS,
            "job": job, "stopping": stopping, "error": error, "error_code": error_code, "notice": notice,
            "sort": sort if sort and sort.get("ok") else None,
            "log": core.read_log(40),
            "outdated": code_stamp() != self.stamp,  # files updated while open: a restart is needed
        }

    def model_info(self):
        """Which model will sort the mails, or why none can. For Ollama: what is installed and loaded."""
        info = {"ok": True, "note": "", "error": "", "installed": [], "running": [],
                "provider": "", "mode": "", "model": "", "loaded": True}
        try:
            cfg = self.cfg()
            info["provider"] = cfg["model"]["provider"]
            name = str(cfg["model"].get("model_name") or "").strip().strip("'\"").lower()
            info["mode"] = name if name in ("active", "auto") else "auto" if not name else "name"
            if self.demo:
                info.update(note="demo-model · demo", model="demo-model", mode="active",
                            installed=["demo-model", "qwen3.5:9b", "qwen3.5:4b"], running=list(self.demo_running),
                            loaded="demo-model" in self.demo_running)
                return info
            if info["provider"] == "ollama":
                try:
                    info.update(models.ollama_models(cfg["model"].get("base_url")))
                except models.ModelError:
                    pass
            resolved, info["note"] = core.resolve_model(cfg)
            info["model"] = resolved["model_name"]
            if info["provider"] == "ollama":
                info["loaded"] = info["model"] in info["running"]
        except (config.ConfigError, models.ModelError) as e:
            info.update(ok=False, error=str(e), loaded=False, error_code=getattr(e, "code", ""))
        return info

    def start_load(self, body):
        """Load an Ollama model into memory in the background."""
        name = str(body.get("name") or "").strip()
        if not name:
            return False
        cfg = None if self.demo else self.cfg()

        def work():
            self.job["model"] = name
            if self.demo:
                time.sleep(1.5 if demo.DELAY else 0)
                self.demo_running = [name]
                self.notice = {"kind": "load", "model": name, "seconds": 1.5}
                return
            seconds = models.load_model(name, cfg["model"].get("base_url"))
            self.notice = {"kind": "load", "model": name, "seconds": seconds}
        return self.start("load", work)

    def save_behavior(self, body):
        if self.demo:
            if "act_alone" in body:
                self.demo_behavior["act_alone"] = bool(body["act_alone"])
            if body.get("batch_size"):
                self.demo_behavior["batch_size"] = max(50, min(int(body["batch_size"]), 1000))
            if "mark_read" in body:
                self.demo_behavior["mark_read"] = bool(body["mark_read"])
            if isinstance(body.get("mark_read_themes"), list):
                self.demo_behavior["mark_read_themes"] = [str(x) for x in body["mark_read_themes"]]
            if "learn_gmail" in body:
                self.demo_behavior["learn_gmail"] = bool(body["learn_gmail"])
            return
        config.save_behavior(self.config_path, body.get("act_alone"), body.get("batch_size"),
                             body.get("mark_read"), body.get("mark_read_themes"), body.get("learn_gmail"))

    def label_order(self, body):
        """Preview, or apply, a new order and numbering of the labels."""
        with self.lock:
            if self.job["running"]:
                return {"ok": False, "error": "Wait for the current job to finish"}
        request = {"action": "label_order", "order": body.get("order"), "numbered": bool(body.get("numbered")),
                   "apply": bool(body.get("apply")), "save": not self.demo, "config": str(self.config_path)}
        r = self.call(request)
        if r["ok"] and request["apply"] and self.demo:
            self.demo_order = [*r["statuses"], *r["themes"]] if r["numbered"] else None
        return r

    def save_keywords(self, body):
        """Typical words of each theme, from the Keywords window."""
        words = body.get("keywords")
        if not isinstance(words, dict):
            return {"ok": False, "error": "Nothing to save"}
        themes = config.names(self.cfg()["themes"])
        data = {t: config.clean_words(words.get(t)) for t in themes if isinstance(words.get(t), list)}
        if self.demo:
            self.demo_keywords = {**(self.demo_keywords or {}), **data}
        else:
            current = config.load(self.config_path).get("keywords") or {}
            config.save_keywords(self.config_path, {**current, **data})
            core.log(f"keywords saved for {len(data)} theme(s)")
        return {"ok": True, "keywords": config.keywords(self.cfg())}

    def start(self, kind, work):
        """Run `work` in the background; the page follows it through /api/state."""
        with self.lock:
            if self.job["running"]:
                return False
            self.job = {"running": True, "kind": kind, "done": 0, "total": 0, "model": "", "stop": False}
            self.error, self.error_code = "", ""

        def run():
            try:
                work()
            except Exception as e:  # shown on the page, the server keeps running
                self.error = f"{type(e).__name__}: {e}"
                self.error_code = getattr(e, "code", "")
            finally:
                self.job["running"] = False
        threading.Thread(target=run, daemon=True).start()
        return True

    def fail(self, r):
        self.error, self.error_code = r["error"], r.get("error_code", "")

    def start_learn(self):
        """The "Learn from my Gmail changes" button."""
        cfg = self.cfg()

        def work():
            r = self.call({"action": "learn_gmail"}, cfg=cfg)
            if r["ok"]:
                self.notice = {"kind": "learn", **{k: r[k] for k in ("checked", "changed", "checked_off")},
                               "proposals": len(r["proposals"])}
            else:
                self.fail(r)
        return self.start("learn", work)

    def progress(self, i, n, row):
        if i == 0:
            self.job["model"] = row["model"]
        else:
            self.job.update(done=i, total=n)
        return "stop" if self.job["stop"] else None  # Stop: end after the mail in progress

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
                    self.fail(r)
                    return
                learned = r.get("learned") or {}
                if learned.get("changed") or learned.get("checked_off"):
                    self.notice = {"kind": "learn", **{k: learned[k] for k in ("checked", "changed", "checked_off")},
                                   "proposals": len(learned.get("proposals") or [])}
                if r.get("stopped"):
                    plan = r.get("plan") or {}
                    self.notice = {"kind": "stopped", "analysed": plan.get("processed", 0)}
                    return
                if not auto:
                    return
                if r.get("applied") is not None:
                    self.notice = {"kind": "apply", **{k: r[k] for k in ("applied", "moved", "skipped", "read")}}
                if self.job["stop"] or not r.get("progress") or r["progress"].get("finished"):
                    return
        return self.start("auto" if auto else "plan", work)

    def start_apply(self, body):
        cfg = self.cfg()

        def work():
            r = self.call({"action": "apply", "batch_id": body.get("batch_id"), "skip": body.get("skip") or [],
                           "batch_size": cfg["behavior"]["batch_size"]}, cfg=cfg)
            if r["ok"]:
                self.notice = {"kind": "apply", **{k: r[k] for k in ("applied", "moved", "skipped", "read")}}
            else:
                self.fail(r)
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
                self.fail(r)
        return self.start(kind, work)

    def start_undo(self):
        cfg = self.cfg()

        def work():
            r = self.call({"action": "undo"}, cfg=cfg)
            if r["ok"]:
                self.notice = {"kind": "undo", "undone": r["undone"], "back_in_inbox": r["back_in_inbox"]}
            else:
                self.fail(r)
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
                html = app.page.replace("__TOKEN__", app.token)
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
            if self.path == "/api/todelete":
                return self.send(200, app.call({"action": "to_delete"}))
            if self.path.startswith("/api/journal"):
                from urllib.parse import parse_qs, urlparse
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                return self.send(200, app.call({"action": "journal", "kind": q.get("kind"), "q": q.get("q"),
                                                "n": q.get("n") or 500}))
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
                      "/api/sender": "sender_state", "/api/rule": "add_rule", "/api/keep": "keep",
                      "/api/trash": "trash", "/api/restore": "restore", "/api/why": "why",
                      "/api/proposal/ignore": "ignore_proposal"}
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
            if self.path == "/api/keywords":
                return self.send(200, app.save_keywords(body))
            if self.path == "/api/order":
                return self.send(200, app.label_order(body))
            if self.path == "/api/stop":
                app.job["stop"] = True
                return self.send(200, {"ok": True})
            if self.path == "/api/shutdown":  # Mail Adagio was opened again: this older copy stops
                self.send(200, {"ok": True})
                print("\n  Mail Adagio was opened again in a new window. This one stops.\n")
                return threading.Thread(target=app.server.shutdown, daemon=True).start()
            starters = {"/api/plan": lambda: app.start_plan(body), "/api/apply": lambda: app.start_apply(body),
                        "/api/load": lambda: app.start_load(body),
                        "/api/undo": app.start_undo, "/api/learn": app.start_learn,
                        "/api/scan": lambda: app.start_simple("scan", {"action": "scan"}),
                        "/api/suggest": lambda: app.start_simple("suggest", {"action": "suggest",
                                                                             "top": body.get("top") or 20})}
            if self.path in starters:
                started = starters[self.path]()
                return self.send(200, {"ok": started, **({} if started else {"error": "Busy, try again in a moment"})})
            self.send(404, {"error": "not found"})

    return Handler


def running_file(config_path, demo_mode):
    return Path(config_path).resolve().parent / (".adagio-ui-demo.json" if demo_mode else ".adagio-ui.json")


def stop_previous(path):
    """Ask a Mail Adagio still open from an earlier launch (maybe an older version) to stop."""
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        req = urllib.request.Request(f"http://127.0.0.1:{old['port']}/api/shutdown", data=b"{}", method="POST",
                                     headers={"X-Adagio-Token": old["token"], "Content-Type": "application/json",
                                              "Host": f"127.0.0.1:{old['port']}"})
        urllib.request.urlopen(req, timeout=3).read()
    except (OSError, ValueError, KeyError):
        return  # not running any more
    for _ in range(50):  # wait until it has freed its port
        try:
            socket.create_connection(("127.0.0.1", old["port"]), timeout=0.2).close()
            time.sleep(0.1)
        except OSError:
            return


def serve(config_path=config.DEFAULT_PATH, port=8765, demo_mode=False, open_browser=True):
    app = App(config_path, demo_mode)
    running = running_file(config_path, demo_mode)
    stop_previous(running)
    for p in range(port, port + 20):  # first free port
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), make_handler(app, p))
            break
        except OSError:
            continue
    else:
        raise OSError("No free port found")
    app.server = server
    try:
        running.write_text(json.dumps({"port": p, "token": app.token}), encoding="utf-8")
    except OSError:
        running = None
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
        try:  # only if it is still ours (a newer launch writes its own)
            if running and json.loads(running.read_text(encoding="utf-8")).get("token") == app.token:
                running.unlink()
        except (OSError, ValueError):
            pass
