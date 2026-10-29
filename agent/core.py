"""The single entry point: a request (dict) in, a structured result (dict) out.

The command line and the JSON mode are only doors to `handle()`.
"""
import json
import math
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import ask as agent_ask
from . import batches, classify, config, hardware, keywords, memory, models, senders, skills
from .mail import TABS, Gmail, MailError, chunks

AGENT = "mail-adagio"
VERSION = "0.8.1"
ACTIONS = ("capabilities", "check", "hardware", "preview", "correct", "rules", "delete_rule",
           "estimate", "plan", "apply", "discard", "undo", "status",
           "scan", "senders", "sender_state", "suggest", "add_rule", "label_order",
           "keep", "to_delete", "trash", "restore", "learn_gmail", "why", "journal",
           "proposals", "ignore_proposal", "read", "search", "relabel", "keywords", "rename_theme", "request")
UI_ONLY = ("trash", "restore", "label_order", "read", "rename_theme")  # refused from the JSON door: done from the window
# (rename_theme also from agent mode: a request you typed in the window)
MAIL_LINE = "mail · "  # journal lines about one mail: kept out of the activity list, shown in the Journal
LOG_PATH = config.MEMOIRE / "adagio.log"  # readable journal of every run; None turns it off
MEMORY_PATH = config.MEMOIRE / "adagio.db"  # rules and examples learned from your corrections


def handle(request, cfg=None, gmail_factory=Gmail, ask=None, progress=None, door="app"):
    """Run one request and always return a dict (errors included, never raised).

    door: "app" (the window, the command line) or "json" (another program: some actions are refused)."""
    action = request.get("action")
    started = time.time()
    try:
        if action not in ACTIONS:
            raise ValueError(f"Unknown action '{action}'. Expected one of: {', '.join(ACTIONS)}")
        if (door == "json" or door == "agent" and action not in skills.AGENT_ACTIONS) and action in UI_ONLY:
            raise ValueError(f"'{action}' is only available in the Mail Adagio window: a person must click it "
                             f"there. Mail Adagio never does it on a program's request.")
        if door == "agent" and action not in skills.AGENT_ACTIONS:
            raise ValueError(f"'{action}' is not an action the agent may use")
        if action == "hardware":
            result = hardware_report(cfg)
        elif action == "capabilities":
            result = capabilities()
        else:
            cfg = cfg or config.load(request.get("config") or config.DEFAULT_PATH)
            if action == "check":
                result = check(cfg, gmail_factory)
            elif action in ("correct", "rules", "delete_rule", "add_rule", "proposals", "ignore_proposal"):
                result = learn(cfg, request)
            elif action == "preview":
                result = preview(cfg, request, gmail_factory, ask, progress)
            else:  # step 3: batches that change Gmail
                result = STEP3[action](cfg, request, gmail_factory, ask, progress)
        result["ok"] = True
    except (config.ConfigError, MailError, models.ModelError, ValueError) as e:
        result = {"ok": False, "error": str(e), "error_code": getattr(e, "code", "")}
    except Exception as e:  # a bug must not crash the caller (Orchestra, a chatbot...)
        result = {"ok": False, "error": f"Unexpected error: {type(e).__name__}: {e}"}
    result.update(agent=AGENT, version=VERSION, action=action,
                  seconds=round(time.time() - started, 1))
    memory_log(summary_line(result))
    return result


def summary_line(r):
    if not r["ok"]:
        return f"{r['action']}: failed: {r['error']}"
    if r["action"] == "preview":
        return (f"preview: read {r['processed']} mails, {r['fallbacks']} not classified, "
                f"0 changes in Gmail ({r['model']})")
    if r["action"] in ("correct", "rules", "delete_rule", "estimate", "status", "discard", "senders",
                       "sender_state", "add_rule", "keep", "to_delete", "journal", "proposals", "capabilities",
                       "ignore_proposal", "read"):
        return ""
    if r["action"] in ("plan", "apply", "undo", "scan", "suggest", "label_order", "trash", "restore",
                       "learn_gmail", "why"):
        return r.get("log", "")
    if r["action"] == "check":
        return f"check: connected to {r['email']}, {r['total_in_mailbox']} mails in {r['mailbox']}"
    return f"{r['action']}: ok"


def memory_log(text):
    """Add lines to the journal (memoire/adagio.log), the readable record of every run and every mail."""
    if LOG_PATH and text:
        try:
            Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                for line in text.splitlines():
                    f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {line}\n")
        except OSError:
            pass


def memory_read_log(n=30, mails=False):
    """The last n lines of the journal, newest first. Without mails: only the run summaries."""
    try:
        lines = Path(LOG_PATH).read_text(encoding="utf-8").splitlines()
    except (OSError, AttributeError, TypeError):
        return []
    if not mails:
        lines = [x for x in lines if x[21:].lstrip()[:len(MAIL_LINE)] != MAIL_LINE]
    return lines[-n:][::-1]


def journal(cfg, request, gmail_factory=None, ask=None, progress=None):
    """The full journal: every run and every mail. kind: all | mails | check | learned; q: words to find."""
    kind, words = request.get("kind") or "all", str(request.get("q") or "").lower().split()
    n = max(1, min(int(request.get("n") or 500), 5000))
    lines = memory_read_log(10**9, mails=True)
    test = {"mails": lambda x: MAIL_LINE in x, "check": lambda x: "to check:" in x,
            "learned": lambda x: "learned from Gmail" in x or "correction" in x or "rule" in x}.get(kind)
    out = [x for x in lines if (not test or test(x)) and all(w in x.lower() for w in words)]
    return {"lines": out[:n], "total": len(out), "path": str(LOG_PATH or "")}


def capabilities():
    """What Mail Adagio can do, for another agent: the contract in agent.json, and its limits."""
    try:
        contract = json.loads((config.ROOT / "agent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        contract = {}
    return {"name": AGENT, "contract": contract, "actions": [a for a in ACTIONS if a not in UI_ONLY],
            "ui_only": list(UI_ONLY), "skills": skills.card(),
            "how_to_call": "python adagio.py call  (JSON on stdin, JSON on stdout)"}


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
    have = models.ollama_models(m.get("base_url"), start=True)  # not running: try to start it once
    if name.lower() == "active":
        if have["running"]:
            m["model_name"] = have["running"][0]
            return m, f"{m['model_name']} · Ollama, active model"
        last = batches.Store(MEMORY_PATH).last_model()  # nothing loaded: the model of the last batch
        if last in have["installed"]:
            m["model_name"] = last
            return m, f"{last} · Ollama, last used, loads at the start"
        if not have["installed"]:
            raise models.ModelError("Ollama has no model installed. Run: ollama pull qwen3.5:9b")
        name = "auto"  # never sorted yet: the best installed model for this PC
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


def learn(cfg, request):
    """Corrections and rules. correct: {sender, subject, theme, status, remember: none|sender|domain}"""
    mem = memory.Memory(MEMORY_PATH)
    try:
        if request["action"] == "correct":
            theme, status, sender = request.get("theme"), request.get("status"), request.get("sender", "")
            if theme not in config.names(cfg["themes"]) or status not in config.names(cfg["statuses"]):
                raise ValueError("Unknown theme or status")
            mem.add_example(sender, request.get("subject", ""), theme, status)
            queue_theme_change(mem, request.get("gm_msgid"), theme)
            remember = request.get("remember", "none")
            if remember in ("sender", "domain"):
                value = memory.address(sender) if remember == "sender" else memory.domain(sender)
                mem.add_rule(remember, value, theme, status)
                memory_log(f"rule learned: mails from {value} -> {theme} / {status}")
            else:
                memory_log(f"correction: '{request.get('subject', '')[:60]}' -> {theme} / {status}")
            plan = update_plan(cfg, mem, request.get("gm_msgid"), theme, status)
            return {"rules": mem.rules(), "plan": plan}
        if request["action"] == "delete_rule":
            mem.delete_rule(request["id"])
            memory_log(f"rule {request['id']} deleted")
        if request["action"] == "add_rule":  # e.g. a suggestion you accepted on the Senders page
            theme, status, kind = request.get("theme"), request.get("status"), request.get("kind", "sender")
            if theme not in config.names(cfg["themes"]) or status not in config.names(cfg["statuses"]):
                raise ValueError("Unknown theme or status")
            mem.add_rule(kind, request.get("value", ""), theme, status)
            memory_log(f"rule added: mails from {request.get('value')} -> {theme} / {status}")
            update_plan(cfg, mem, None, theme, status)
        themes, statuses = config.names(cfg["themes"]), config.names(cfg["statuses"])
        if request["action"] == "ignore_proposal":
            mem.ignore(request.get("sender", ""), request.get("theme", ""), request.get("status", ""))
            memory_log(f"rule proposal ignored: {request.get('sender')} -> {request.get('theme')} / {request.get('status')}")
        if request["action"] in ("proposals", "ignore_proposal"):
            return {"proposals": mem.proposals(themes, statuses)}
        out = {"rules": mem.rules(), "proposals": mem.proposals(themes, statuses)}
        if request["action"] == "rules":  # the Learning card
            out.update(keyword_changes=[{k: r[k] for k in ("word", "theme", "kind", "detail", "created")}
                                        for r in mem.keyword_history(8)],
                       keywords_waiting=len(mem.keyword_queue()), corrections=correction_rates(), gmail_tabs=tab_themes())
        return out
    finally:
        mem.close()


def correction_rates(n=5):
    """[{batch, mails, corrected}] of the last applied batches, oldest first: fewer corrections = better sort."""
    store = batches.Store(MEMORY_PATH)
    try:
        rows = store.all("SELECT b.id AS batch, COUNT(m.gm_msgid) AS mails, COALESCE(SUM(m.corrected), 0) AS corrected "
                         "FROM batches b JOIN mails m ON m.batch_id = b.id AND m.state = 'applied' "
                         "WHERE b.state = 'applied' GROUP BY b.id ORDER BY b.id DESC LIMIT ?", n)
        return rows[::-1]
    finally:
        store.close()


def tab_themes(top=3):
    """For each Gmail tab, the themes Mail Adagio gave its mails (after your corrections), most used first:
    [{tab, mails, themes: [{theme, share}]}]. A check of the sort against Gmail's own."""
    store = batches.Store(MEMORY_PATH)
    try:
        rows = store.all("SELECT tab, theme, COUNT(*) AS n FROM mails WHERE state = 'applied' AND tab != '' "
                         "GROUP BY tab, theme ORDER BY n DESC")
    finally:
        store.close()
    out = {}
    for r in rows:
        out.setdefault(r["tab"], []).append(r)
    order = {t: i for i, t in enumerate(TABS)}
    result = []
    for tab, items in sorted(out.items(), key=lambda kv: order.get(kv[0], 9)):
        total = sum(x["n"] for x in items)
        result.append({"tab": tab, "mails": total,
                       "themes": [{"theme": x["theme"], "share": round(100 * x["n"] / total)} for x in items[:top]]})
    return result


def queue_theme_change(mem, gm_msgid, theme):
    """A theme corrected with the pencil: the mail waits to teach keywords (see keywords.py).
    Only the agent's mistakes teach: changing your own correction again does not."""
    if not gm_msgid:
        return
    store = batches.Store(MEMORY_PATH)
    try:
        row = store.mail(gm_msgid)
    finally:
        store.close()
    if row and row["theme"] != theme and (not row["corrected"] or mem.queued(gm_msgid)):
        mem.queue_keyword(gm_msgid, row["sender"], row["subject"], row["theme"], theme)


def learn_keywords(cfg, request, gmail, ask):
    """Keywords learned from the theme corrections waiting in the queue. Saved in config.yaml
    (unless request["save_keywords"] is False: the demo keeps them in memory). Never raises."""
    mem, store = memory.Memory(MEMORY_PATH), batches.Store(MEMORY_PATH)
    try:
        if not mem.keyword_queue(1):
            return None
        history = [(r["subject"], r["theme"], bool(r["corrected"])) for r in store.all(
            "SELECT subject, theme, corrected FROM mails WHERE state IN ('applied', 'planned')")]
        opened = []

        def body(gm_msgid):
            try:
                if not opened:
                    gmail.open(gmail.all_mail(), readonly=True)
                    opened.append(True)
                row = store.mail(gm_msgid)
                uid = row["all_uid"] if row and row["all_uid"] > 0 else gmail.find([gm_msgid]).get(gm_msgid)
                mails = gmail.fetch([uid], cfg["behavior"]["body_chars"]) if uid else []
                return mails[0].body if mails and mails[0].gm_msgid == str(gm_msgid) else ""
            except Exception:  # the mail is gone or Gmail failed: the subject is enough
                return ""
        out = keywords.learn(cfg, mem, ask, history, body)
        if out["keywords"] is not None:
            cfg["keywords"] = out["keywords"]
            if request.get("save_keywords", True):
                config.save_keywords(request.get("config") or config.DEFAULT_PATH, out["keywords"])
        for line in out["lines"]:  # "nothing to learn" lines are about one mail: Journal only
            memory_log((MAIL_LINE if line.startswith("keywords: nothing") else "") + line)
        return out
    except Exception as e:  # never block the sorting for it
        memory_log(f"keywords: not learned this time ({type(e).__name__}: {e})")
        return None
    finally:
        mem.close()
        store.close()


def keywords_line(out):
    if not out or not (out["added"] or out["moved"] or out["removed"]):
        return ""
    parts = [f"{len(out[k])} {name}" for k, name in (("added", "learned"), ("moved", "moved"),
                                                      ("removed", "removed")) if out[k]]
    return "keywords: " + ", ".join(parts)


def update_plan(cfg, mem, gm_msgid, theme, status):
    """A correction also changes the plan waiting for your approval, and a new rule its other mails."""
    store = batches.Store(MEMORY_PATH)
    try:
        batch = store.pending()
        if not batch:
            return None
        if gm_msgid:
            store.correct(gm_msgid, theme, status)
        themes, statuses = config.names(cfg["themes"]), config.names(cfg["statuses"])
        for row in store.mails(batch["id"]):
            rule = mem.match(row["sender"], themes, statuses)
            if rule and not row["corrected"]:
                store.apply_rule(row["gm_msgid"], rule["theme"], rule["status"],
                                 f"{rule['kind']}: {rule['value']}")
        return plan_result(cfg, store, batch)
    finally:
        store.close()


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


def get_model(cfg, request, ask=None):
    """The function that asks the model, and a note saying which model it is."""
    if ask:  # given by the caller (tests, demo)
        return ask, request.get("model_note") or f"{cfg['model'].get('model_name')} · {cfg['model']['provider']}"
    model_cfg, note = resolve_model(cfg)
    return models.make_ask(model_cfg), note


def sort_mails(cfg, mails, ask, model_note, progress=None):
    """Theme and status of each mail: your rules first, then the model. Returns (rows, model calls)."""
    themes, statuses = config.names(cfg["themes"]), config.names(cfg["statuses"])
    mem = memory.Memory(MEMORY_PATH)
    try:
        examples = mem.examples()
        rows, model_calls, timeouts = [], 0, 0
        for i, mail in enumerate(mails, 1):
            rule = mem.match(mail.sender, themes, statuses)
            reason, check = "", False
            if rule:  # your rule wins: no model call
                r = classify.Result(rule["theme"], rule["status"], True, 0)
            else:
                r = classify.classify(mail, ask, cfg, examples=examples)
                model_calls += 1
                check = classify.to_check(r, cfg)
                if check and r.ok:  # only for the mails to check: one more short call, the reason
                    reason = classify.explain(mail, ask, r.theme, r.status)
                    model_calls += 1
                elif check:
                    reason = ("the model took too long on this mail" if r.timed_out
                              else "the model gave no valid answer") + f" ({r.error[:120]})"
                if model_calls == 1 and not r.ok and r.provider_error:
                    # the model cannot answer at all: stop now instead of filing every mail in Other
                    raise models.ModelError(f"The model {model_note.split(' · ')[0]} did not answer: {r.error}")
                timeouts = timeouts + 1 if r.timed_out else 0
                if timeouts == 3:  # too slow for this computer: say so instead of waiting on every mail
                    raise models.ModelError(
                        f"The model {model_note.split(' · ')[0]} did not answer in time 3 mails in a row "
                        f"({r.error}). Choose a smaller or faster model in Settings.")
            rows.append({
                "uid": mail.uid, "gm_msgid": mail.gm_msgid, "date": mail.date,
                "sender": mail.sender, "subject": mail.subject,
                "gmail_url": gmail_url(mail.gm_msgid),
                "existing_labels": [x for x in mail.labels if not x.startswith("\\")], "tab": mail.tab,
                "theme": r.theme, "status": r.status, "classified": r.ok, "to_delete": r.to_delete,
                "rule": f"{rule['kind']}: {rule['value']}" if rule else "",
                "confidence": "" if rule else (r.confidence if r.ok else "low"), "to_check": check,
                "reason": reason,
                **({"error": r.error} if not r.ok else {}),
            })
            if progress and progress(i, len(mails), rows[-1]) == "stop":
                break  # Stop: the mails analysed so far make the plan, the others come next time
        return rows, model_calls
    finally:
        mem.close()


def preview(cfg, request, gmail_factory, ask, progress):
    """Steps 1 and 2: read recent mails and classify them. Nothing is changed in Gmail."""
    limit = int(request.get("limit") or 20)
    since = request.get("since_days")
    mailbox = cfg["gmail"].get("mailbox", "INBOX")
    ask, model_note = get_model(cfg, request, ask)
    if progress:
        progress(0, 0, {"model": model_note})

    with connect(cfg, gmail_factory) as gmail:
        total = gmail.open(mailbox)
        uids = gmail.recent_uids(limit=limit, since_days=since)
        mails = gmail.fetch(uids, cfg["behavior"]["body_chars"])

    rows, _ = sort_mails(cfg, mails, ask, model_note, progress)
    return {"mailbox": mailbox, "total_in_mailbox": total, "processed": len(rows),
            "model": model_note, "changes_made": 0, **summarize(cfg, rows)}


def summarize(cfg, rows):
    """Counts by theme and status, and the mails sorted: Important first, newest first."""
    status_order = {name: i for i, name in enumerate(config.names(cfg["statuses"]))}
    rows.sort(key=lambda r: r["date"], reverse=True)
    rows.sort(key=lambda r: status_order.get(r["status"], 99))
    return {
        "by_theme": dict(Counter(r["theme"] for r in rows).most_common()),
        "by_status": {s: n for s, n in sorted(Counter(r["status"] for r in rows).items(),
                                              key=lambda kv: status_order.get(kv[0], 99))},
        "fallbacks": sum(not r["classified"] for r in rows),
        "to_check": sum(bool(r.get("to_check")) for r in rows),
        "by_rule": sum(bool(r["rule"]) for r in rows),
        "first_error": next((r["error"] for r in rows if not r["classified"]), ""),
        "mails": rows,
    }


# --- Step 3: sort the mailbox by batches, with your approval -----------------------------
#
# estimate -> plan (analyse the next batch, nothing changes in Gmail) -> apply (labels + out of
# the Inbox) -> plan ... and undo (puts the last applied batch back as it was).
# Only labels are added or removed. A mail is never deleted (see "To delete" below for the Trash).

INBOX = "\\Inbox"
SECONDS_PER_CALL = {"anthropic": 1.2, "openrouter": 2.0, "ollama": 1.5}  # until we have measured
PRICES = {"haiku-5-5": (0.10, 0.50), "haiku-5.5": (0.10, 0.50)}  # $ per million tokens in, out


def scope_of(request, cfg):
    s = request.get("scope") or {}
    kind = s.get("kind", "count")
    if kind not in ("count", "days", "all"):
        raise ValueError("scope.kind must be count, days or all")
    value = int(s.get("value") or 0)
    if kind != "all" and value < 1:
        raise ValueError("Give a number of mails or of days")
    size = max(50, min(int(request.get("batch_size") or cfg["behavior"]["batch_size"]), 1000))
    return kind, value, size


def scope_uids(gmail, kind, value, max_uid=None):
    uids = gmail.recent_uids(since_days=value if kind == "days" else None, max_uid=max_uid)
    return uids[:value] if kind == "count" else uids


def estimate(cfg, request, gmail_factory, ask=None, progress=None):
    """How many mails and batches, how long and how much, before starting."""
    kind, value, size = scope_of(request, cfg)
    mailbox = cfg["gmail"].get("mailbox", "INBOX")
    with connect(cfg, gmail_factory) as gmail:
        gmail.open(mailbox)
        n = len(scope_uids(gmail, kind, value))
    provider, name = cfg["model"]["provider"], cfg["model"].get("model_name", "")
    store = batches.Store(MEMORY_PATH)
    per_call = store.seconds_per_call(name) or SECONDS_PER_CALL[provider]
    store.close()
    seconds = n * (per_call + 0.15)  # + reading and labelling
    if provider == "ollama":
        cost, cost_note = 0.0, "free, runs on your computer"
    else:
        price = next((p for key, p in PRICES.items() if key in name), None)
        if price:
            tokens_in = (len(classify.system_prompt(cfg)) + cfg["behavior"]["body_chars"] + 400) / 3.5
            cost = n * (tokens_in * price[0] + 30 * price[1]) / 1e6
            cost_note = f"about ${cost:.2f} with {name}"
        else:
            cost, cost_note = None, f"unknown for {name}"
    return {"mailbox": mailbox, "mails": n, "batch_size": size, "batches": math.ceil(n / size),
            "duration_s": round(seconds), "cost_usd": cost, "cost_note": cost_note,
            "seconds_per_mail": round(per_call + 0.15, 2)}


def progress_of(store, job, batch_size):
    if not job:
        return None
    done = store.done_in_job(job["id"])
    return {"job_id": job["id"], "kind": job["kind"], "value": job["value"], "total": job["total"],
            "done": done, "batch_no": store.batches_in_job(job["id"]) + 1,
            "batches": max(1, math.ceil(job["total"] / batch_size)), "finished": done >= job["total"]}


def plan_result(cfg, store, batch):
    rows = store.mails(batch["id"])
    for r in rows:
        r["gmail_url"] = gmail_url(r["gm_msgid"])
    labels = config.labels(cfg)
    return {"batch_id": batch["id"], "state": batch["state"], "model": batch["model"],
            "created": batch["created"], "applied": batch["applied"], "processed": len(rows),
            "labelled": sum(r["state"] == "applied" for r in rows),
            "moved": sum(r["archived"] for r in rows),
            "labels": labels, "archive": cfg["behavior"]["archive"],
            "low_interest": sum(config.low_interest(cfg, r["theme"], r["status"]) for r in rows),
            "marked_read": sum(r["marked_read"] for r in rows),
            "to_delete": sum(r["to_delete"] for r in rows), "delete_label": config.delete_label(cfg),
            **summarize(cfg, rows), "check_label": config.check_label(cfg), "mails": rows}


def plan(cfg, request, gmail_factory, ask=None, progress=None):
    """Analyse the next batch and keep it as a plan. Nothing changes in Gmail.

    One plan at a time: while a plan waits for your approval, it is returned as it is.
    With act_alone (config or request) the plan is applied right away.
    """
    kind, value, size = scope_of(request, cfg)
    act_alone = request.get("act_alone", cfg["behavior"]["act_alone"])
    store = batches.Store(MEMORY_PATH)
    try:
        pending = store.pending()
        job = store.job()
        if pending and act_alone:
            store.close()
            return apply(cfg, {"batch_id": pending["id"], "batch_size": size}, gmail_factory)
        if pending:
            return {"plan": plan_result(cfg, store, pending), "progress": progress_of(store, job, size)}
        mailbox = cfg["gmail"].get("mailbox", "INBOX")
        ask, model_note = get_model(cfg, request, ask)
        if progress:
            progress(0, 0, {"model": model_note})
        with connect(cfg, gmail_factory) as gmail:
            learned = None
            if cfg["behavior"]["learn_gmail"]:  # first, what you changed in Gmail since the last batch
                try:
                    learned = gmail_changes(cfg, gmail)
                    memory_log(learned_line(learned))
                except MailError:
                    learned = None  # never block the sorting for it
            learned_words = learn_keywords(cfg, request, gmail, ask)
            gmail.open(mailbox)
            same = job and (job["kind"], job["value"]) == (kind, value) and not request.get("restart")
            if not same:  # a new sort (a finished one starts again only when asked: restart)
                uids = scope_uids(gmail, kind, value)
                job = store.new_job(kind, value, max(uids, default=0), len(uids))
                memory_log(f"new sort: {describe_scope(kind, value)}, {len(uids)} mails, batches of {size}")
            left = job["total"] - store.done_in_job(job["id"])
            picked = next_mails(gmail, store, job, min(size, left)) if left > 0 else []
            if not picked:
                return {"plan": None, "progress": {**progress_of(store, job, size), "finished": True}}
            mails = gmail.fetch(picked, cfg["behavior"]["body_chars"])
        started = time.time()
        rows, calls = sort_mails(cfg, mails, ask, model_note, progress)
        stopped = len(rows) < len(mails)
        if not rows:
            return {"plan": None, "stopped": True, "progress": progress_of(store, job, size)}
        batch_id = store.add_batch(job["id"], model_note.split(" · ")[0], time.time() - started, calls, rows)
        batch = store.one("SELECT * FROM batches WHERE id = ?", batch_id)
        result = {"plan": plan_result(cfg, store, batch), "progress": progress_of(store, job, size),
                  "learned": learned, "keywords_learned": learned_words}
        result["log"] = (f"plan: batch {result['progress']['batch_no']} of {result['progress']['batches']}, "
                         f"{len(rows)} mails analysed ({model_note}), waiting for approval"
                         + (" (stopped: the other mails come next time)" if stopped else ""))
        result["stopped"] = stopped
    finally:
        store.close()
    if act_alone and not stopped:  # stopped: the partial plan waits for you, even when acting alone
        applied = apply(cfg, {"batch_id": batch_id, "batch_size": size}, gmail_factory)
        applied["log"] = result["log"].replace(", waiting for approval", "") + "\n" + applied["log"]
        return applied
    return result


def describe_scope(kind, value):
    return {"count": f"newest {value} mails", "days": f"last {value} days",
            "all": "whole mailbox"}[kind]


def next_mails(gmail, store, job, n):
    """The next n mails of the job, newest first, skipping the ones already handled."""
    handled = store.handled()
    picked = []
    for chunk in chunks(scope_uids(gmail, job["kind"], job["value"], job["max_uid"]), 500):
        ids = gmail.meta(chunk)
        for uid in chunk:
            if uid in ids and ids[uid][0] not in handled:
                picked.append(uid)
                if len(picked) == n:
                    return picked
    return picked


def apply(cfg, request, gmail_factory, ask=None, progress=None):
    """Carry out the plan: theme + status labels, and out of the Inbox. Mails in `skip` stay as they are."""
    store = batches.Store(MEMORY_PATH)
    try:
        batch = store.pending()
        if not batch or (request.get("batch_id") and int(request["batch_id"]) != batch["id"]):
            raise ValueError("This plan is not waiting for approval any more. Refresh the page.")
        skip = {str(x) for x in request.get("skip") or []}
        rows = store.mails(batch["id"])
        todo = [r for r in rows if r["gm_msgid"] not in skip]
        names = config.labels(cfg)
        archive = cfg["behavior"]["archive"]
        done = moved = read = 0
        try:
            with connect(cfg, gmail_factory) as gmail:
                folders = gmail.folders()
                known = {f.lower() for f in folders}
                dlabel, clabel = config.delete_label(cfg), config.check_label(cfg)
                for label in all_labels(cfg):  # every label, even empty ones: the list in Gmail is complete
                    if label.lower() not in known:
                        gmail.create_label(label)
                gmail.open(gmail.all_mail(folders), readonly=False)
                found = gmail.find(r["gm_msgid"] for r in todo)
                current = gmail.meta(found.values())
                groups = {}
                for r in todo:
                    uid = found.get(r["gm_msgid"])
                    if uid is None:  # deleted or moved to Trash/Spam meanwhile
                        store.mark(r["gm_msgid"], "skipped")
                        continue
                    key = ((names[r["theme"]], names[r["status"]]) + ((clabel,) if r["to_check"] else ())
                           + ((dlabel,) if r["to_delete"] else ()))
                    groups.setdefault(key, []).append((r, uid))
                for labels, items in groups.items():
                    uids = [uid for _, uid in items]
                    gmail.change_labels(uids, add=labels)
                    in_inbox = [uid for uid in uids if INBOX in current.get(uid, ("", []))[1]]
                    if archive:
                        gmail.change_labels(in_inbox, remove=[INBOX])
                    low = [uid for r, uid in items if config.low_interest(cfg, r["theme"], r["status"])]
                    unread = gmail.unread(low) if low else set()
                    gmail.mark_read(unread)
                    for r, uid in items:
                        have = {x.lower() for x in current.get(uid, ("", []))[1]}
                        added = [x for x in labels if x.lower() not in have]
                        archived = archive and uid in in_inbox
                        store.mark(r["gm_msgid"], "applied", added, archived, uid in unread, uid)
                        journal_mail(r, added, archived, uid in unread)
                        done += 1
                        moved += archived
                        read += uid in unread
        finally:
            for r in rows:
                if r["gm_msgid"] in skip:
                    store.mark(r["gm_msgid"], "skipped")
                    journal_mail(r, None)
            if done:  # even if it stopped half way, what was done can be undone...
                store.set_state(batch["id"], "applied")
                store.release_planned(batch["id"])  # ...and what was not will be proposed again
        job = store.job()
        size = int(request.get("batch_size") or cfg["behavior"]["batch_size"])
        prog = progress_of(store, job, size)
        number = prog["batch_no"] - 1 if prog else batch["id"]
        line = (f"apply: batch {number}: {done} mails labelled, {moved} moved out of the Inbox, "
                + (f"{read} marked as read, " if read else "")
                + f"{len(skip & {r['gm_msgid'] for r in rows})} left as they were")
        if not done:  # every mail left as it was: nothing to undo
            store.set_state(batch["id"], "skipped")
        return {"applied": done, "moved": moved, "read": read, "skipped": len(rows) - done, "batch_id": batch["id"],
                "changes_made": done, "progress": prog, "log": line, "plan": None}
    finally:
        store.close()


def journal_mail(r, added, archived=False, read=False):
    """One journal line per mail: what it is, how it was sorted, what was done in Gmail."""
    how = (f"rule {r['rule']}" if r.get("rule") else "your correction" if r.get("corrected")
           else f"model ({r.get('confidence') or 'no answer'})")
    if added is None:
        done = "left as it was (you unticked it)"
    else:
        done = ", ".join([f"+{x}" for x in added] + (["out of the Inbox"] if archived else [])
                         + (["marked as read"] if read else [])) or "labels already there"
    subject = " ".join((r.get("subject") or "(no subject)").split())[:90]
    line = (f"{MAIL_LINE}{(r.get('date') or '')[:10]} · {r.get('sender', '')[:60]} · \"{subject}\" · {how} · "
            f"{r['theme']} / {r['status']} · {done}")
    if r.get("to_check"):
        line += f" · to check: {r.get('reason') or 'the model was not sure'}"
    memory_log(line)


def discard(cfg, request, gmail_factory=None, ask=None, progress=None):
    """Drop the waiting plan; its mails will be analysed again (useful after adding rules)."""
    store = batches.Store(MEMORY_PATH)
    try:
        batch = store.pending()
        if batch:
            store.discard(batch["id"])
            memory_log(f"plan of batch {batch['id']} discarded, nothing changed in Gmail")
        return {"discarded": bool(batch), "plan": None}
    finally:
        store.close()


def undo(cfg, request, gmail_factory, ask=None, progress=None):
    """Put the mails of the last applied batch back as they were: remove the labels the agent
    added and put back in the Inbox the mails it moved out."""
    store = batches.Store(MEMORY_PATH)
    try:
        batch = store.last_applied()
        if not batch:
            raise ValueError("Nothing to undo")
        rows = [r for r in store.mails(batch["id"]) if r["state"] == "applied"]
        with connect(cfg, gmail_factory) as gmail:
            gmail.open(gmail.all_mail(), readonly=False)
            found = gmail.find(r["gm_msgid"] for r in rows)
            groups = {}
            for r in rows:
                if r["gm_msgid"] in found and r["added"]:
                    groups.setdefault(tuple(r["added"]), []).append(found[r["gm_msgid"]])
            for labels, uids in groups.items():
                gmail.change_labels(uids, remove=labels)
            put_back = {}  # labels a relabel removed
            for r in rows:
                if r["gm_msgid"] in found and r["removed"]:
                    put_back.setdefault(tuple(r["removed"]), []).append(found[r["gm_msgid"]])
            for labels, uids in put_back.items():
                gmail.change_labels(uids, add=labels)
            back = [found[r["gm_msgid"]] for r in rows if r["archived"] and r["gm_msgid"] in found]
            gmail.change_labels(back, add=[INBOX])
            unread = [found[r["gm_msgid"]] for r in rows if r["marked_read"] and r["gm_msgid"] in found]
            gmail.mark_read(unread, read=False)  # unread again, as they were
        for r in rows:
            store.mark(r["gm_msgid"], "undone")
        store.set_state(batch["id"], "undone")
        prog = progress_of(store, store.job(), int(request.get("batch_size") or cfg["behavior"]["batch_size"]))
        return {"undone": len(rows), "back_in_inbox": len(back), "batch_id": batch["id"],
                "changes_made": len(rows), "progress": prog,
                "log": f"undo: batch applied at {batch['applied'][11:16]}: labels removed from {len(rows)} mails, "
                       f"{len(back)} put back in the Inbox" + (f", {len(unread)} unread again" if unread else "")
                       + (f", old labels back on {sum(len(u) for u in put_back.values())}" if put_back else "")}
    finally:
        store.close()


def status(cfg, request, gmail_factory=None, ask=None, progress=None):
    """Where the sorting stands: current job, plan waiting for approval, last batch that can be undone."""
    size = int(request.get("batch_size") or cfg["behavior"]["batch_size"])
    store = batches.Store(MEMORY_PATH)
    try:
        pending, last = store.pending(), store.last_applied()
        return {"progress": progress_of(store, store.job(), size),
                "plan": plan_result(cfg, store, pending) if pending else None,
                "last": plan_result(cfg, store, last) if last else None,
                "act_alone": cfg["behavior"]["act_alone"], "batch_size": cfg["behavior"]["batch_size"],
                "archive": cfg["behavior"]["archive"], "labels": config.labels(cfg),
                "mark_read": cfg["behavior"]["mark_read"], "mark_read_themes": cfg["behavior"]["mark_read_themes"],
                "learn_gmail": cfg["behavior"]["learn_gmail"], "proposals": proposals(cfg)}
    finally:
        store.close()


def proposals(cfg):
    mem = memory.Memory(MEMORY_PATH)
    try:
        return mem.proposals(config.names(cfg["themes"]), config.names(cfg["statuses"]))
    finally:
        mem.close()


STEP3 = {"estimate": estimate, "plan": plan, "apply": apply, "discard": discard, "undo": undo,
         "status": status}


# --- Senders: subscriptions and rule suggestions --------------------------------------------
#
# scan: reads only the From, Date and List-Unsubscribe headers of every mail (fast, free, no AI).
# suggest: for the senders who write the most, classifies a few of their mails and proposes a rule.

def scan(cfg, request, gmail_factory, ask=None, progress=None):
    """Count mails per sender and collect unsubscribe links. Reads headers only, changes nothing."""
    me = cfg["gmail"]["email"].lower()
    with connect(cfg, gmail_factory) as gmail:
        where = cfg["gmail"].get("mailbox", "INBOX") if request.get("where") == "inbox" else gmail.all_mail()
        gmail.open(where)
        days = request.get("days")
        uids = gmail.recent_uids(since_days=int(days) if days else None)
        headers = []
        for i, chunk in enumerate(chunks(uids, 1000)):
            headers += gmail.headers(chunk)
            if progress:
                progress(min(len(uids), (i + 1) * 1000), len(uids), {})
    headers = [h for h in headers if memory.address(h.sender) != me]  # not your own sent mails
    headers.sort(key=lambda h: h.uid, reverse=True)
    rows = senders.aggregate(headers)
    store = senders.Senders(MEMORY_PATH)
    try:
        store.save_scan(rows, len(headers), where)
    finally:
        store.close()
    links = sum(bool(r["unsubscribe"]) for r in rows.values())
    return {"mailbox": where, "mails": len(headers), "senders": len(rows), "with_unsubscribe": links,
            "changes_made": 0, "log": f"scan of {where}: {len(headers)} mails from {len(rows)} senders, "
                   f"{links} with an unsubscribe link (headers only, nothing changed)"}


def list_senders(cfg, request, gmail_factory=None, ask=None, progress=None):
    """Every sender with its count, unsubscribe link, rule (if you have one) and suggested rule."""
    themes, statuses = config.names(cfg["themes"]), config.names(cfg["statuses"])
    store, mem = senders.Senders(MEMORY_PATH), memory.Memory(MEMORY_PATH)
    try:
        rows = store.all()
        for r in rows:
            rule = mem.match(r["address"], themes, statuses)
            r["rule"] = {k: rule[k] for k in ("kind", "value", "theme", "status")} if rule else None
            del r["samples"]
        return {"scan": store.last_scan(), "senders": rows,
                "with_unsubscribe": sum(bool(r["unsubscribe"]) for r in rows)}
    finally:
        store.close()
        mem.close()


def sender_state(cfg, request, gmail_factory=None, ask=None, progress=None):
    """Remember that you opened the unsubscribe link of a sender, or hid it from the list."""
    store = senders.Senders(MEMORY_PATH)
    try:
        store.set_state(request.get("address", ""), request.get("state", ""))
        if request.get("state") == "opened":
            memory_log(f"unsubscribe link opened for {request.get('address')}")
        return {}
    finally:
        store.close()


def suggest(cfg, request, gmail_factory, ask=None, progress=None):
    """Propose a rule for the senders who write the most, from a few of their mails."""
    top, minimum = int(request.get("top") or 20), int(request.get("min_mails") or 5)
    themes, statuses = config.names(cfg["themes"]), config.names(cfg["statuses"])
    store, mem = senders.Senders(MEMORY_PATH), memory.Memory(MEMORY_PATH)
    try:
        scan_info = store.last_scan()
        if not scan_info:
            raise ValueError("Scan your mailbox first")
        todo = [r for r in store.all() if r["mails"] >= minimum and not r["theme"] and r["samples"]
                and not mem.match(r["address"], themes, statuses)][:top]
        ask, model_note = get_model(cfg, request, ask)
        if progress:
            progress(0, 0, {"model": model_note})
        examples, done = mem.examples(), []
        with connect(cfg, gmail_factory) as gmail:
            gmail.open(scan_info["mailbox"])
            for i, r in enumerate(todo, 1):
                mails = gmail.fetch(r["samples"], cfg["behavior"]["body_chars"])
                results = [classify.classify(m, ask, cfg, examples=examples) for m in mails]
                if results and all(x.provider_error for x in results) and not done:
                    raise models.ModelError(f"The model {model_note.split(' · ')[0]} did not answer: "
                                            f"{results[0].error}")
                link = next((m.unsubscribe for m in mails if m.unsubscribe), "")
                if link:
                    store.set_link(r["address"], link)
                vote = senders.majority(results)
                if vote:
                    store.suggest(r["address"], *vote)
                    done.append(r["address"])
                if progress:
                    progress(i, len(todo), {})
        return {"suggested": len(done), "checked": len(todo), "model": model_note, "changes_made": 0,
                "log": f"suggest: rules proposed for {len(done)} senders ({model_note}), nothing changed"}
    finally:
        store.close()
        mem.close()


def all_labels(cfg):
    """Every label the agent uses, in order: statuses, themes, "To check", "To delete", Other."""
    names = config.labels(cfg)
    statuses, themes = config.order(cfg)
    out = [names[n] for n in statuses + themes]
    return out[:-1] + [config.check_label(cfg), config.delete_label(cfg)] + out[-1:] if out else out


# --- Finding and changing given mails (used by the agent mode, open to other programs) ---------------

def label_names(cfg):
    """{Gmail label (lower case): theme or status name}"""
    return {label.lower(): name for name, label in config.labels(cfg).items()}


def search(cfg, request, gmail_factory, ask=None, progress=None):
    """Find mails with Gmail's search. Reads labels and the first lines only; changes nothing."""
    p = skills.check("search", request, cfg)
    parts = []
    if p.get("sender"):
        parts.append(f'from:"{p["sender"].replace(chr(34), "")}"')
    if p.get("subject"):
        parts.append(f'subject:"{p["subject"].replace(chr(34), "")}"')
    if p.get("words"):
        parts.append(p["words"].replace('"', ""))
    if p.get("days"):
        parts.append(f"newer_than:{p['days']}d")
    folder = None
    if p.get("label"):
        names = config.labels(cfg)
        wanted = p["label"].strip().lower()
        extra = {"to check": config.check_label(cfg), "to delete": config.delete_label(cfg)}
        folder = next((label for name, label in names.items() if wanted in (name.lower(), label.lower())),
                      extra.get(wanted))
        if not folder:
            raise ValueError(f"search: unknown label '{p['label']}'")
    raw = " ".join(parts)
    limit = p.get("limit") or 50
    by_label = label_names(cfg)
    themes, statuses = config.names(cfg["themes"]), config.names(cfg["statuses"])
    with connect(cfg, gmail_factory) as gmail:
        gmail.open(folder or ("INBOX" if p.get("inbox") else gmail.all_mail()))
        uids = gmail.search(raw)
        total = len(uids)
        mails = gmail.fetch(uids[:limit], 300) if uids else []
    if folder and p.get("inbox"):
        mails = [m for m in mails if INBOX in m.labels]
    out = []
    for m in mails:
        have = [by_label.get(x.lower()) for x in m.labels]
        out.append({"gm_msgid": m.gm_msgid, "date": m.date, "sender": m.sender, "subject": m.subject,
                    "theme": next((x for x in have if x in themes), ""),
                    "status": next((x for x in have if x in statuses), ""),
                    "in_inbox": INBOX in m.labels, "url": gmail_url(m.gm_msgid)})
    where = f" in {folder}" if folder else " in the Inbox" if p.get("inbox") else ""
    return {"query": raw, "found": total, "shown": len(out), "mails": out, "changes_made": 0,
            "log": f"search: {total} mail{'s' if total != 1 else ''} found{where}"
                   + (f" ({raw})" if raw else "") + (f", {len(out)} listed" if total > len(out) else "")}


def relabel(cfg, request, gmail_factory, ask=None, progress=None):
    """Give mails another theme and/or status in Gmail. The other theme (status) labels go, "To check" too;
    out of the Inbox like a sorted mail. One batch that undo puts back; counts as your correction."""
    p = skills.check("relabel", request, cfg)
    names, by_label = config.labels(cfg), label_names(cfg)
    themes, statuses = config.names(cfg["themes"]), config.names(cfg["statuses"])
    check = config.check_label(cfg)
    archive = cfg["behavior"]["archive"]
    store, mem = batches.Store(MEMORY_PATH), memory.Memory(MEMORY_PATH)
    rows, moved = [], 0
    try:
        with connect(cfg, gmail_factory) as gmail:
            folders = gmail.folders()
            known = {f.lower() for f in folders}
            for label in [names[x] for x in (p.get("theme"), p.get("status")) if x]:
                if label.lower() not in known:
                    gmail.create_label(label)
            gmail.open(gmail.all_mail(folders), readonly=False)
            found = gmail.find(p["gm_msgids"])
            heads = {m.uid: m for m in gmail.fetch(list(found.values()), 0, tabs=False)} if found else {}
            groups = {}
            for gm, uid in found.items():
                m = heads.get(uid)
                if not m:
                    continue
                have = {x.lower(): x for x in m.labels}
                now = [by_label[x] for x in have if x in by_label]
                old_theme = next((x for x in now if x in themes), "")
                old_status = next((x for x in now if x in statuses), "")
                theme, status = p.get("theme") or old_theme, p.get("status") or old_status
                remove = [have[names[x].lower()] for x in now
                          if (p.get("theme") and x in themes and x != theme)
                          or (p.get("status") and x in statuses and x != status)]
                if p.get("theme") and check.lower() in have:
                    remove.append(have[check.lower()])
                add = [names[x] for x in (p.get("theme"), p.get("status")) if x and names[x].lower() not in have]
                archived = archive and INBOX in m.labels
                if archived:
                    remove.append(INBOX)
                if not add and not remove:
                    continue
                groups.setdefault((tuple(add), tuple(remove)), []).append(uid)
                before = store.mail(gm)
                if p.get("theme") and before and before["theme"] != theme and (not before["corrected"] or mem.queued(gm)):
                    mem.queue_keyword(gm, m.sender, m.subject, before["theme"], theme)  # the agent's mistake
                if theme and status:
                    mem.add_example(m.sender, m.subject, theme, status)
                rows.append({"gm_msgid": gm, "uid": uid, "date": m.date, "sender": m.sender, "subject": m.subject,
                             "theme": theme or "", "status": status or "", "added": add,
                             "removed": [x for x in remove if x != INBOX], "archived": archived,
                             "old": f"{old_theme or '-'} / {old_status or '-'}"})
                moved += archived
            for (add, remove), uids in groups.items():
                gmail.change_labels(uids, add=add, remove=remove)
        if rows:
            store.add_relabel(rows)
            for r in rows:
                memory_log(f"{MAIL_LINE}{(r['date'] or '')[:10]} · {r['sender'][:60]} · \"{' '.join(r['subject'].split())[:90]}\" · "
                    f"relabelled on request · {r['old']} -> {r['theme'] or '-'} / {r['status'] or '-'}")
    finally:
        store.close()
        mem.close()
    target = " / ".join(x for x in (p.get("theme"), p.get("status")) if x)
    return {"relabelled": len(rows), "not_found": len(p["gm_msgids"]) - len(found), "moved": moved,
            "changes_made": len(rows),
            "log": f"relabel: {len(rows)} mail{'s' if len(rows) != 1 else ''} -> {target}"
                   + (f", {moved} moved out of the Inbox" if moved else "") + " (undo puts them back)"}


def keywords_action(cfg, request, gmail_factory=None, ask=None, progress=None):
    """Read the keywords, or add / remove keywords of one theme. Saved in config.yaml."""
    p = skills.check("keywords", request, cfg)
    words = config.keywords(cfg)
    theme = p.get("theme")
    if not theme or not (p.get("add") or p.get("remove")):
        return {"keywords": {theme: words[theme]} if theme else words, "changes_made": 0}
    gone = {w.lower() for w in p.get("remove", [])}
    new = [w for w in words[theme] if w.lower() not in gone]
    added = [w for w in p.get("add", []) if w.lower() not in {x.lower() for x in new}]
    room = config.MAX_KEYWORDS - len(new)
    new = config.clean_words(new + added[:max(room, 0)])
    words[theme] = new
    cfg["keywords"] = words
    if request.get("save_keywords", True):
        config.save_keywords(request.get("config") or config.DEFAULT_PATH, words)
    return {"keywords": {theme: new}, "new_keywords": words, "changes_made": 0,
            "log": f"keywords of {theme}: " + ", ".join(
                x for x in ((f"+{', '.join(added[:max(room, 0)])}" if added else ""),
                            (f"-{', '.join(sorted(gone))}" if gone else "")) if x)
                   + (f" ({len(added) - room} not added: {config.MAX_KEYWORDS} at most)" if len(added) > room else "")}


def keep(cfg, request, gmail_factory=None, ask=None, progress=None):
    """Untick "To delete" on a mail of the plan waiting for approval."""
    store = batches.Store(MEMORY_PATH)
    try:
        store.keep(request.get("gm_msgid"))
        batch = store.pending()
        return {"plan": plan_result(cfg, store, batch) if batch else None}
    finally:
        store.close()


# --- "To delete": the model proposes, you check in Gmail, you send them to the Trash ----------
#
# Never automatic, even when the agent acts alone. The Trash is emptied by Gmail after 30 days;
# until then "restore" brings the mails back. Nothing is ever deleted for good (no EXPUNGE).

def to_delete(cfg, request, gmail_factory, ask=None, progress=None):
    """How many mails carry the "To delete" label, and the last trash that can be restored."""
    label = config.delete_label(cfg)
    with connect(cfg, gmail_factory) as gmail:
        count = gmail.open(label) if label.lower() in {f.lower() for f in gmail.folders()} else 0
    store = batches.Store(MEMORY_PATH)
    try:
        last = store.last_trash()
    finally:
        store.close()
    return {"label": label, "count": count,
            "last_trash": {k: last[k] for k in ("trash_id", "created", "n")} if last else None}


def trash(cfg, request, gmail_factory, ask=None, progress=None):
    """Move every mail labelled "To delete" to Gmail's Trash. Only on your click, with the count you saw."""
    label = config.delete_label(cfg)
    with connect(cfg, gmail_factory) as gmail:
        if label.lower() not in {f.lower() for f in gmail.folders()}:
            raise ValueError(f"No label '{label}' in Gmail yet")
        gmail.open(label, readonly=False)
        uids = gmail.recent_uids()
        if int(request.get("confirm", -1)) != len(uids):
            raise ValueError(f"The label now holds {len(uids)} mails. Refresh and confirm again.")
        meta = gmail.meta(uids)
        gmail.action_sensitive_trash(uids)
    store = batches.Store(MEMORY_PATH)
    try:
        trash_id = store.add_trash(m[0] for m in meta.values())
    finally:
        store.close()
    return {"trashed": len(uids), "trash_id": trash_id, "changes_made": len(uids),
            "log": f"trash: {len(uids)} mails labelled '{label}' moved to Gmail Trash "
                   f"(you asked; recoverable for 30 days)"}


def restore(cfg, request, gmail_factory, ask=None, progress=None):
    """Bring back from the Trash the mails of the last "Move to Trash", with their "To delete" label."""
    label = config.delete_label(cfg)
    store = batches.Store(MEMORY_PATH)
    try:
        last = store.last_trash()
        if not last:
            raise ValueError("Nothing to restore")
        with connect(cfg, gmail_factory) as gmail:
            folders = gmail.folders()
            gmail.open(gmail.special("\\Trash", "[Gmail]/Trash", folders), readonly=False)
            found = gmail.find(last["gm_msgids"])
            gmail.copy(found.values(), label)
            trash_folder = gmail.special("\\Trash", "[Gmail]/Trash", folders)
            gmail.open(trash_folder)
            still = gmail.find(found)  # check: Gmail should have taken them out of the Trash
        store.restored(last["trash_id"])
        missing = len(last["gm_msgids"]) - len(found)
        back = len(found) - len(still)
        result = {"restored": back, "still_in_trash": len(still), "missing": missing, "changes_made": back,
                  "log": f"restore: {back} mails back from the Trash with the label '{label}'"
                         + (f", {missing} no longer in the Trash" if missing else "")
                         + (f", {len(still)} still in the Trash (restore them in Gmail)" if still else "")}
        if still:
            result["warning"] = (f"{len(still)} mails are still in the Trash. In Gmail: open the Trash, "
                                 f"select them, then 'Move to' > Inbox.")
        return result
    finally:
        store.close()


def rename_theme(cfg, request, gmail_factory, ask=None, progress=None):
    """Give a theme another name (and hint): its Gmail label is renamed too (Gmail keeps the mails and the
    colour), and its keywords, rules, examples and colour follow. Undone by renaming it back."""
    p = skills.check("rename_theme", request, cfg)
    old = p["theme"]
    try:
        new_cfg = config.renamed(cfg, old, p["new_name"], p.get("hint"))
    except config.ConfigError as e:
        raise ValueError(str(e))
    new = next(n for n in config.names(new_cfg["themes"]) if n not in config.names(cfg["themes"]))
    old_label, new_label = config.labels(cfg)[old], config.labels(new_cfg)[new]
    in_gmail = False
    with connect(cfg, gmail_factory) as gmail:
        existing = {f.lower() for f in gmail.folders()}
        if new_label.lower() in existing and new_label.lower() != old_label.lower():
            raise ValueError(f"A label named '{new_label}' already exists in Gmail. Rename or delete it first")
        if old_label.lower() in existing:  # a label never used yet is created later with the new name
            gmail.rename_label(old_label, new_label)
            in_gmail = True
    if request.get("save_keywords", True):  # the demo keeps its configuration in memory
        config.save_rename(request.get("config") or config.DEFAULT_PATH, old, new, p.get("hint"))
    store, mem = batches.Store(MEMORY_PATH), memory.Memory(MEMORY_PATH)
    try:
        store.rename_theme(old, new, old_label, new_label)
        mem.rename_theme(old, new)
    finally:
        store.close()
        mem.close()
    lines = [f"theme renamed: {old} -> {new}" + (f" (Gmail label {old_label} -> {new_label})" if in_gmail
                                                     else " (its Gmail label will have the new name)")]
    if p.get("hint"):
        lines.append(f"  new description: {p['hint']}")
    return {"theme": old, "new_name": new, "hint": p.get("hint", ""), "old_label": old_label, "label": new_label,
            "renamed_in_gmail": in_gmail, "renamed_theme": {"old": old, "new": new, "hint": p.get("hint", "")},
            "changes_made": int(in_gmail), "log": "\n".join(lines)}


def label_order(cfg, request, gmail_factory, ask=None, progress=None):
    """Order and number the labels ("01 - Priority"); with apply, rename them in Gmail.

    Gmail keeps the mails and the colour of a renamed label. Without apply, only shows what would change.
    """
    statuses, themes = config.order(cfg)
    wanted = request.get("order") or statuses + themes
    numbered = request.get("numbered", True)
    new_cfg = {**cfg, "label_order": [n for n in wanted if n in statuses + themes] if numbered else None}
    config.check(new_cfg)
    old, new = config.labels(cfg), config.labels(new_cfg)
    old[config.TO_DELETE], new[config.TO_DELETE] = config.delete_label(cfg), config.delete_label(new_cfg)
    old[config.TO_CHECK], new[config.TO_CHECK] = config.check_label(cfg), config.check_label(new_cfg)
    renames = [{"name": n, "old": old[n], "new": new[n]} for n in old if old[n] != new[n]]
    s2, t2 = config.order(new_cfg)
    result = {"numbered": numbered, "statuses": s2, "themes": t2, "labels": new, "renames": renames,
              "renamed": 0, "changes_made": 0}
    if not request.get("apply"):
        return result
    done = []
    if renames:
        with connect(cfg, gmail_factory) as gmail:
            existing = {k.lower() for k in gmail.folders()}
            clash = [r["new"] for r in renames if r["new"].lower() in existing and r["old"].lower() in existing]
            if clash:
                raise ValueError(f"A label named '{clash[0]}' already exists in Gmail. Rename or delete it first")
            try:
                for r in renames:
                    if r["old"].lower() in existing:  # a label never used yet is created later with the new name
                        gmail.rename_label(r["old"], r["new"])
                        done.append(r)
            except MailError:
                for r in reversed(done):  # put back what was already renamed
                    gmail.rename_label(r["new"], r["old"])
                raise
    with connect(cfg, gmail_factory) as gmail:  # and the labels never used yet, so the list is complete
        existing = {k.lower() for k in gmail.folders()}
        created = [x for x in all_labels(new_cfg) if x.lower() not in existing]
        for label in created:
            gmail.create_label(label)
    if request.get("save", True):
        config.save_order(request.get("config") or config.DEFAULT_PATH, new_cfg["label_order"])
    store = batches.Store(MEMORY_PATH)
    try:
        store.rename_labels({r["old"]: r["new"] for r in renames})
    finally:
        store.close()
    lines = [f"labels: {len(done)} renamed in Gmail" + (" (numbered)" if numbered else " (numbers removed)")]
    lines += [f"  {r['old']} -> {r['new']}" for r in done]
    lines += [f"  created: {x}" for x in created]
    return {**result, "renamed": len(done), "created": created, "changes_made": len(done) + len(created),
            "log": "\n".join(lines)}


# --- Learn from your changes in Gmail ------------------------------------------------------
#
# You moved a sorted mail to another theme (or status) in Gmail: Mail Adagio reads the labels of the
# mails it sorted (labels only, no AI for this step), and takes each change as a correction: the mail
# keeps its new theme, and the sender and subject become an example shown to the model.
# The same correction twice for one sender: a rule is proposed (you accept or ignore it).
# Then the AI reads each mail whose theme you corrected and learns keywords (keywords.py).

CHECK_AT_MOST = 2000  # most recent sorted mails whose labels are read again
FIND_AT_MOST = 200    # mails sorted before v0.5 (their All Mail id is unknown): searched one by one


def gmail_changes(cfg, gmail):
    """Read the labels of the sorted mails again and learn from what you changed. Gmail is not changed."""
    store, mem = batches.Store(MEMORY_PATH), memory.Memory(MEMORY_PATH)
    try:
        rows = store.applied(CHECK_AT_MOST)
        out = {"checked": 0, "changed": 0, "checked_off": 0, "changes": []}
        if not rows:
            return {**out, "proposals": proposals(cfg)}
        gmail.open(gmail.all_mail(), readonly=True)
        known = [r for r in rows if r["all_uid"] > 0]
        meta = gmail.meta(r["all_uid"] for r in known)
        current = {}
        for r in known:
            m = meta.get(r["all_uid"])
            if m and m[0] == r["gm_msgid"]:
                current[r["gm_msgid"]] = m[1]
            else:  # the id changed (rare): search it again next time
                store.set_all_uid(r["gm_msgid"], 0)
        unknown = [r for r in rows if r["all_uid"] == 0][:FIND_AT_MOST]
        found = gmail.find(r["gm_msgid"] for r in unknown)
        meta = gmail.meta(found.values()) if found else {}
        for r in unknown:
            uid = found.get(r["gm_msgid"])
            store.set_all_uid(r["gm_msgid"], uid or -1)  # -1: no longer in Gmail (deleted), not searched again
            if uid in meta:
                current[r["gm_msgid"]] = meta[uid][1]
        names = config.labels(cfg)
        themes, statuses = config.names(cfg["themes"]), config.names(cfg["statuses"])
        by_label = {label.lower(): name for name, label in names.items()}
        check = config.check_label(cfg).lower()
        for r in rows:
            labels = current.get(r["gm_msgid"])
            if labels is None or r["theme"] not in names or r["status"] not in names:
                continue
            out["checked"] += 1
            have = {x.lower() for x in labels}
            theme = changed_to(r["theme"], have, names, by_label, themes)
            status = changed_to(r["status"], have, names, by_label, statuses)
            if (theme, status) != (r["theme"], r["status"]):
                store.changed_in_gmail(r["gm_msgid"], theme, status, True)
                mem.add_example(r["sender"], r["subject"], theme, status)
                if theme != r["theme"] and (not r["corrected"] or mem.queued(r["gm_msgid"])):  # the agent's mistake
                    mem.queue_keyword(r["gm_msgid"], r["sender"], r["subject"], r["theme"], theme)
                out["changed"] += 1
                out["changes"].append({"gm_msgid": r["gm_msgid"], "sender": r["sender"], "subject": r["subject"],
                                       "old": [r["theme"], r["status"]], "new": [theme, status]})
                memory_log(f"{MAIL_LINE}learned from Gmail · {r['sender'][:60]} · \"{' '.join(r['subject'].split())[:90]}\" · "
                    f"{r['theme']} / {r['status']} -> {theme} / {status}")
            elif r["to_check"] and check not in have:  # you removed "To check": you looked at it
                store.checked(r["gm_msgid"])
                out["checked_off"] += 1
        out["proposals"] = mem.proposals(themes, statuses)
        return out
    finally:
        store.close()
        mem.close()


def changed_to(old, have, names, by_label, allowed):
    """The theme (or status) the mail has now in Gmail: the old one if its label is still there,
    else the only other one it carries. None or several: unchanged (nothing sure to learn)."""
    if names[old].lower() in have:
        return old
    now = [by_label[x] for x in have if x in by_label and by_label[x] in allowed]
    return now[0] if len(now) == 1 else old


def learn_gmail(cfg, request, gmail_factory, ask=None, progress=None):
    """The "Learn from my Gmail changes" button (also done before each new batch)."""
    with connect(cfg, gmail_factory) as gmail:
        out = gmail_changes(cfg, gmail)
        words = None
        if memory_queue_size():
            try:
                model_ask, _note = get_model(cfg, request, ask)
                words = learn_keywords(cfg, request, gmail, model_ask)
            except models.ModelError as e:  # the corrections wait for the next time
                memory_log(f"keywords: not learned this time, the model is not available ({e})")
    out["keywords_learned"] = words
    out["log"] = "\n".join(x for x in (learned_line(out, always=True), keywords_line(words)) if x)
    out["changes_made"] = 0
    return out


def memory_queue_size():
    mem = memory.Memory(MEMORY_PATH)
    try:
        return len(mem.keyword_queue())
    finally:
        mem.close()


def learned_line(out, always=False):
    if not out or (not always and not out["changed"] and not out["checked_off"]):
        return ""
    return (f"learn: {out['checked']} sorted mails read again in Gmail (labels only), "
            f"{out['changed']} change{'s' if out['changed'] != 1 else ''} learned" + (f", {out['checked_off']} checked" if out["checked_off"] else "")
            + (f", {len(out['proposals'])} rule{'s' if len(out['proposals']) > 1 else ''} proposed"
               if out["proposals"] else ""))


def why(cfg, request, gmail_factory, ask=None, progress=None):
    """The "Why?" button: asks the model why it chose this theme and status for one mail."""
    store = batches.Store(MEMORY_PATH)
    try:
        row = store.mail(request.get("gm_msgid"))
        if not row:
            raise ValueError("Unknown mail")
        ask, model_note = get_model(cfg, request, ask)
        with connect(cfg, gmail_factory) as gmail:
            gmail.open(gmail.all_mail(), readonly=True)
            uid = row["all_uid"] if row["all_uid"] > 0 else None
            mails = gmail.fetch([uid], cfg["behavior"]["body_chars"]) if uid else []
            if not mails or mails[0].gm_msgid != row["gm_msgid"]:  # id unknown or changed: search it
                uid = gmail.find([row["gm_msgid"]]).get(row["gm_msgid"])
                mails = gmail.fetch([uid], cfg["behavior"]["body_chars"]) if uid else []
        if not mails or mails[0].gm_msgid != row["gm_msgid"]:
            raise ValueError("This mail is no longer in Gmail")
        if row["rule"]:
            reason = f"Your rule ({row['rule']}) sorts every mail of this sender this way."
        else:
            reason = classify.explain(mails[0], ask, row["theme"], row["status"])
        if not reason:
            raise models.ModelError(f"The model {model_note.split(' · ')[0]} gave no reason. Try again.")
        store.set_reason(row["gm_msgid"], reason)
        subject = " ".join(row["subject"].split())[:90]
        return {"gm_msgid": row["gm_msgid"], "reason": reason, "model": model_note,
                "log": f"{MAIL_LINE}why · {row['sender'][:60]} · \"{subject}\" · {row['theme']} / {row['status']} · "
                       f"{reason}"}
    finally:
        store.close()


def read(cfg, request, gmail_factory, ask=None, progress=None):
    """The reader window: one whole mail, read-only. Nothing changes in Gmail, the mail stays unread."""
    gm = str(request.get("gm_msgid") or "")
    if not gm.isdigit():
        raise ValueError("Unknown mail")
    store = batches.Store(MEMORY_PATH)
    try:
        row = store.mail(gm)
    finally:
        store.close()
    with connect(cfg, gmail_factory) as gmail:
        gmail.open(gmail.all_mail(), readonly=True)
        uid = row["all_uid"] if row and (row["all_uid"] or 0) > 0 else None
        msg = gmail.message(uid) if uid else None
        if not msg or msg["gm_msgid"] != gm:  # id unknown or changed: search it
            uid = gmail.find([gm]).get(gm)
            msg = gmail.message(uid) if uid else None
    if not msg or msg["gm_msgid"] != gm:
        raise ValueError("This mail is no longer in Gmail")
    msg["gmail_url"] = gmail_url(gm)
    return msg


STEP3.update(request=agent_ask.request, rename_theme=rename_theme, search=search, relabel=relabel, keywords=keywords_action, read=read, keep=keep, to_delete=to_delete, trash=trash, restore=restore, label_order=label_order, scan=scan,
             senders=list_senders, sender_state=sender_state, suggest=suggest, learn_gmail=learn_gmail, why=why,
             journal=journal)
