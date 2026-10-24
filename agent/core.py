"""The single entry point: a request (dict) in, a structured result (dict) out.

The command line and the JSON mode are only doors to `handle()`.
"""
import time
from collections import Counter
from datetime import datetime

from . import classify, config, hardware, models
from .mail import Gmail, MailError

AGENT = "mail-adagio"
VERSION = "0.1.0"
ACTIONS = ("check", "hardware", "preview")
LOG_PATH = config.ROOT / "adagio.log"  # readable journal of every run; None turns it off


def handle(request, cfg=None, gmail_factory=Gmail, ask=None, progress=None):
    """Run one request and always return a dict (errors included, never raised)."""
    action = request.get("action")
    started = time.time()
    try:
        if action not in ACTIONS:
            raise ValueError(f"Unknown action '{action}'. Expected one of: {', '.join(ACTIONS)}")
        if action == "hardware":
            result = hardware_report(cfg)
        else:
            cfg = cfg or config.load(request.get("config") or config.DEFAULT_PATH)
            if action == "check":
                result = check(cfg, gmail_factory)
            else:
                result = preview(cfg, request, gmail_factory, ask, progress)
        result["ok"] = True
    except (config.ConfigError, MailError, models.ModelError, ValueError) as e:
        result = {"ok": False, "error": str(e)}
    except Exception as e:  # a bug must not crash the caller (Orchestra, a chatbot...)
        result = {"ok": False, "error": f"Unexpected error: {type(e).__name__}: {e}"}
    result.update(agent=AGENT, version=VERSION, action=action,
                  seconds=round(time.time() - started, 1))
    log(summary_line(result))
    return result


def summary_line(r):
    if not r["ok"]:
        return f"{r['action']}: failed: {r['error']}"
    if r["action"] == "preview":
        return (f"preview: read {r['processed']} mails, {r['fallbacks']} not classified, "
                f"0 changes in Gmail ({r['model']})")
    if r["action"] == "check":
        return f"check: connected to {r['email']}, {r['total_in_mailbox']} mails in {r['mailbox']}"
    return f"{r['action']}: ok"


def log(line):
    if LOG_PATH:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {line}\n")
        except OSError:
            pass


def read_log(n=30):
    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    except (OSError, AttributeError):
        return []
    return lines[-n:][::-1]


def hardware_report(cfg=None):
    info = hardware.detect()
    table = (cfg or {}).get("local_models") or default_table()
    model, reason = hardware.pick_model(table, info)
    return {"hardware": info, "suggested_local_model": model, "reason": reason}


def default_table():
    import yaml
    return yaml.safe_load(config.EXAMPLE.read_text(encoding="utf-8"))["local_models"]


def resolve_model(cfg):
    """Model settings to use, plus a short sentence saying which model and why.

    For Ollama, model_name can be "active" (the model already loaded in Ollama),
    "auto" or "" (chosen from the graphics card), or a model name.
    """
    m = dict(cfg["model"])
    name = (m.get("model_name") or "").strip().strip("'\"").strip()
    if m["provider"] != "ollama":
        if not name:
            raise models.ModelError("No model name set. Open Settings and choose a model.")
        m["model_name"] = name
        return m, f"{name} · {m['provider']}"
    have = models.ollama_models(m.get("base_url"))
    if name.lower() == "active":
        if not have["running"]:
            raise models.ModelError("No model is loaded in Ollama right now. Load one, or pick a "
                                    "model by name in Settings.")
        m["model_name"] = have["running"][0]
        return m, f"{m['model_name']} · Ollama, active model"
    if name.lower() in ("", "auto"):
        info = hardware.detect()
        table = [r for r in cfg["local_models"] if r["use"] in have["installed"]] or cfg["local_models"]
        pick, reason = hardware.pick_model(table, info)
        if not pick:
            raise models.ModelError(reason)
        if pick not in have["installed"]:
            raise models.ModelError(f"{pick} fits your PC but is not installed. Run: ollama pull {pick}")
        m["model_name"] = pick
        return m, f"{pick} · Ollama, chosen for your PC ({reason.split(' → ')[0]})"
    if have["installed"] and name not in have["installed"]:
        raise models.ModelError(f"Ollama has no model named '{name}'. Installed: "
                                f"{', '.join(have['installed']) or 'none'}")
    m["model_name"] = name
    return m, f"{name} · Ollama"


def gmail_url(gm_msgid):
    """Link that opens the mail in Gmail (Gmail ids are the hexadecimal X-GM-MSGID)."""
    return f"https://mail.google.com/mail/u/0/#all/{int(gm_msgid):x}" if gm_msgid else ""


def connect(cfg, gmail_factory):
    g = cfg["gmail"]
    if not g.get("email") or not g.get("app_password"):
        raise config.ConfigError("Gmail address or app password missing. Run: python adagio.py setup")
    return gmail_factory(g["email"], g["app_password"], g.get("imap_host", "imap.gmail.com"))


def check(cfg, gmail_factory):
    mailbox = cfg["gmail"].get("mailbox", "INBOX")
    with connect(cfg, gmail_factory) as gmail:
        total = gmail.open(mailbox)
    return {"email": cfg["gmail"]["email"], "mailbox": mailbox, "total_in_mailbox": total}


def preview(cfg, request, gmail_factory, ask, progress):
    """Steps 1 and 2: read recent mails and classify them. Nothing is changed in Gmail."""
    limit = int(request.get("limit") or 20)
    since = request.get("since_days")
    mailbox = cfg["gmail"].get("mailbox", "INBOX")
    if ask:
        model_note = request.get("model_note") or f"{cfg['model'].get('model_name')} · {cfg['model']['provider']}"
    else:
        model_cfg, model_note = resolve_model(cfg)
        ask = models.make_ask(model_cfg)
    if progress:
        progress(0, 0, {"model": model_note})

    with connect(cfg, gmail_factory) as gmail:
        total = gmail.open(mailbox)
        uids = gmail.recent_uids(limit=limit, since_days=since)
        mails = gmail.fetch(uids, cfg["behavior"]["body_chars"])

    rows = []
    for i, mail in enumerate(mails, 1):
        r = classify.classify(mail, ask, cfg)
        if i == 1 and not r.ok and r.provider_error:
            # the model cannot answer at all: stop now instead of filing every mail in Other
            raise models.ModelError(f"The model {model_note.split(' · ')[0]} did not answer: {r.error}")
        rows.append({
            "uid": mail.uid, "gm_msgid": mail.gm_msgid, "date": mail.date,
            "sender": mail.sender, "subject": mail.subject,
            "gmail_url": gmail_url(mail.gm_msgid),
            "existing_labels": [x for x in mail.labels if not x.startswith("\\")],
            "theme": r.theme, "status": r.status, "classified": r.ok,
            **({"error": r.error} if not r.ok else {}),
        })
        if progress:
            progress(i, len(mails), rows[-1])

    status_order = {name: i for i, name in enumerate(config.names(cfg["statuses"]))}
    rows.sort(key=lambda r: r["date"], reverse=True)          # newest first...
    rows.sort(key=lambda r: status_order[r["status"]])        # ...grouped by status, Important first
    return {
        "mailbox": mailbox, "total_in_mailbox": total, "processed": len(rows),
        "model": model_note, "changes_made": 0,
        "by_theme": dict(Counter(r["theme"] for r in rows).most_common()),
        "by_status": {s: n for s, n in sorted(Counter(r["status"] for r in rows).items(),
                                              key=lambda kv: status_order[kv[0]])},
        "fallbacks": sum(not r["classified"] for r in rows),
        "first_error": next((r["error"] for r in rows if not r["classified"]), ""),
        "mails": rows,
    }
