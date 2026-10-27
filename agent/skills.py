"""What Mail Adagio can do, written once: its skills card.

The same list is used three times, so it can never drift from the code:
- `capabilities`: the card another program (Orchestra, a chatbot) reads to know what to ask;
- agent mode (`request`): the local model reads the actions it may use and picks them itself;
- the checks: every step the model proposes is validated here before it runs.

kind: "read" reads Gmail without changing it, "local" only touches adagio.db / config.yaml / the journal,
"write" changes Gmail. window: done from the window only (refused to other programs; refused to the agent
too, unless agent is True). agent: the agent may pick it from a free-text request.
"""
from . import config

# type: int | text | bool | ids | words | theme | status | choice | scope
#   (name, type, description, extra) -- extra: {"required": True, "min": 1, "max": 9, "choices": [...]}
SKILLS = [
    # information
    {"name": "capabilities", "kind": "local", "agent": False, "summary": "This card: what Mail Adagio can do.",
     "params": []},
    {"name": "check", "kind": "read", "summary": "Test the Gmail connection and count the mails in the mailbox.",
     "params": []},
    {"name": "hardware", "kind": "local", "agent": False,
     "summary": "Graphics card, memory and the local model that fits this PC.", "params": []},
    {"name": "status", "kind": "local",
     "summary": "Where the sorting stands: progress, the plan waiting for approval, the last batch that can be undone.",
     "params": []},
    {"name": "journal", "kind": "local",
     "summary": "Read the journal: every run and every sorted mail (what, how, which labels).",
     "params": [("kind", "choice", "all, mails, check (mails to check) or learned", {"choices": ["all", "mails", "check", "learned"]}),
                ("q", "text", "words to find in the lines", {}),
                ("n", "int", "number of lines, newest first (default 50)", {"min": 1, "max": 500})]},
    # sorting
    {"name": "preview", "kind": "read",
     "summary": "Read and classify recent mails without changing anything in Gmail.",
     "params": [("limit", "int", "number of recent mails (default 20)", {"min": 1, "max": 200}),
                ("since_days", "int", "only mails from the last N days", {"min": 1, "max": 3650})]},
    {"name": "estimate", "kind": "read",
     "summary": "How many mails and batches, how long and how much, before sorting.",
     "params": [("scope", "scope", "{kind: count|days|all, value: N}", {"required": True}),
                ("batch_size", "int", "mails per batch", {"min": 50, "max": 1000})]},
    {"name": "plan", "kind": "write",
     "summary": "Analyse the next batch of the inbox (rules first, then the model). With act_alone it is applied "
                "right away (labels + out of the Inbox); without, it waits for approval.",
     "params": [("scope", "scope", "{kind: count|days|all, value: N}: which mails to sort", {"required": True}),
                ("batch_size", "int", "mails per batch", {"min": 50, "max": 1000}),
                ("act_alone", "bool", "apply the batch right away", {}),
                ("restart", "bool", "start again from the newest mail", {})]},
    {"name": "apply", "kind": "write",
     "summary": "Carry out the plan waiting for approval: theme and status labels, mails out of the Inbox.",
     "params": [("skip", "ids", "mails of the plan to leave as they are", {})]},
    {"name": "discard", "kind": "local", "summary": "Drop the plan waiting for approval; its mails come back later.",
     "params": []},
    {"name": "undo", "kind": "write",
     "summary": "Undo the last applied batch or relabel: labels removed or put back, mails back in the Inbox.",
     "params": []},
    {"name": "why", "kind": "read", "summary": "The model says in one sentence why it gave a mail its theme and status.",
     "params": [("gm_msgid", "text", "the mail's Gmail id", {"required": True})]},
    {"name": "read", "kind": "read", "window": True, "agent": False,
     "summary": "The whole mail, for the reader window (stays unread).",
     "params": [("gm_msgid", "text", "the mail's Gmail id", {"required": True})]},
    # finding and changing given mails (agent mode)
    {"name": "search", "kind": "read",
     "summary": "Find mails with Gmail's search: sender, subject, words, label, age. Returns each mail's id, "
                "date, sender, subject, theme and status. Changes nothing.",
     "params": [("sender", "text", "sender address, name or domain (from:)", {}),
                ("subject", "text", "words of the subject", {}),
                ("words", "text", "words anywhere in the mail", {}),
                ("label", "text", "a theme, a status, \"To check\" or \"To delete\": only mails with this label", {}),
                ("inbox", "bool", "only mails still in the Inbox", {}),
                ("days", "int", "only mails of the last N days", {"min": 1, "max": 3650}),
                ("limit", "int", "at most N mails, newest first (default 50)", {"min": 1, "max": 200})]},
    {"name": "relabel", "kind": "write",
     "summary": "Give mails another theme and/or status in Gmail (the other theme or status labels are removed). "
                "Counts as a correction, so keywords are learned. Undone by undo.",
     "params": [("gm_msgids", "ids", "the mails: ids from search, or \"$prev\" for the mails found by the step before",
                 {"required": True}),
                ("theme", "theme", "the new theme", {}),
                ("status", "status", "the new status", {})]},
    {"name": "keywords", "kind": "local",
     "summary": "Read the keywords of the themes, or add / remove keywords of one theme (clues for the model).",
     "params": [("theme", "theme", "the theme to change (none: read them all)", {}),
                ("add", "words", "keywords to add", {}),
                ("remove", "words", "keywords to remove", {})]},
    # learning
    {"name": "correct", "kind": "local",
     "summary": "Correct a mail of the plan waiting for approval; remember=sender|domain also makes a rule.",
     "params": [("gm_msgid", "text", "the mail's Gmail id", {"required": True}),
                ("sender", "text", "its sender", {}), ("subject", "text", "its subject", {}),
                ("theme", "theme", "the right theme", {"required": True}),
                ("status", "status", "the right status", {"required": True}),
                ("remember", "choice", "none, sender or domain", {"choices": ["none", "sender", "domain"]})]},
    {"name": "rules", "kind": "local",
     "summary": "The sender rules, the rules proposed, the keywords learned and the share of mails corrected.",
     "params": []},
    {"name": "add_rule", "kind": "local",
     "summary": "Add a rule: every mail from this sender (or domain) gets this theme and status, without the model.",
     "params": [("kind", "choice", "sender or domain", {"choices": ["sender", "domain"], "required": True}),
                ("value", "text", "the address or the domain", {"required": True}),
                ("theme", "theme", "the theme", {"required": True}),
                ("status", "status", "the status", {"required": True})]},
    {"name": "delete_rule", "kind": "local", "summary": "Delete a rule (its id comes from rules).",
     "params": [("id", "int", "the rule's id", {"required": True, "min": 1, "max": 10 ** 9})]},
    {"name": "proposals", "kind": "local", "summary": "Rules proposed after the same correction several times.",
     "params": []},
    {"name": "ignore_proposal", "kind": "local", "summary": "Never propose this rule again.",
     "params": [("sender", "text", "the sender", {"required": True}),
                ("theme", "theme", "the theme", {"required": True}),
                ("status", "status", "the status", {"required": True})]},
    {"name": "learn_gmail", "kind": "read",
     "summary": "Read the labels you changed in Gmail and learn from them; the model then learns keywords from "
                "the corrected mails.",
     "params": []},
    # senders
    {"name": "scan", "kind": "read",
     "summary": "Count the mails of each sender and collect unsubscribe links (headers only).",
     "params": [("where", "choice", "all (All Mail) or inbox", {"choices": ["all", "inbox"]}),
                ("days", "int", "only the last N days", {"min": 1, "max": 3650})]},
    {"name": "senders", "kind": "local",
     "summary": "Every sender from the last scan: count, unsubscribe link, rule, suggested rule.", "params": []},
    {"name": "sender_state", "kind": "local",
     "summary": "Remember that a sender's unsubscribe link was opened, or hide the sender.",
     "params": [("address", "text", "the sender's address", {"required": True}),
                ("state", "choice", "opened, hidden, or empty to show it again", {"choices": ["", "opened", "hidden"]})]},
    {"name": "suggest", "kind": "read",
     "summary": "The model proposes a rule for the senders who write the most, from a few of their mails.",
     "params": [("top", "int", "number of senders (default 20)", {"min": 1, "max": 100}),
                ("min_mails", "int", "only senders with at least N mails (default 5)", {"min": 1, "max": 10000})]},
    # to delete
    {"name": "to_delete", "kind": "read",
     "summary": "How many mails carry the \"To delete\" label, and the last trash that can be restored.",
     "params": []},
    {"name": "keep", "kind": "local", "summary": "Untick \"To delete\" on a mail of the plan waiting for approval.",
     "params": [("gm_msgid", "text", "the mail's Gmail id", {"required": True})]},
    {"name": "trash", "kind": "write", "window": True, "agent": False,
     "summary": "Move the mails labelled \"To delete\" to Gmail's Trash. Only on a click with the count seen.",
     "params": [("confirm", "int", "the number of mails seen", {"required": True, "min": 1, "max": 10 ** 6})]},
    {"name": "restore", "kind": "write", "window": True, "agent": False,
     "summary": "Bring back from the Trash the mails of the last \"Move to Trash\".", "params": []},
    {"name": "rename_theme", "kind": "write", "window": True,
     "summary": "Give a theme another name, and if asked a new description of what goes in it. Its Gmail label is "
                "renamed too (Gmail keeps the mails and the colour); its keywords and rules follow.",
     "params": [("theme", "theme", "the theme to rename", {"required": True}),
                ("new_name", "text", "its new name, 1 to 40 characters", {"required": True}),
                ("hint", "text", "new description of the mails that go in it (optional)", {})]},
    {"name": "label_order", "kind": "write", "window": True, "agent": False,
     "summary": "Order, number and colour the labels; with apply, rename them in Gmail.", "params": []},
    # agent mode
    {"name": "request", "kind": "write", "agent": False,
     "summary": "Ask in your own words (any language): the local model picks the actions above that do it, "
                "the code checks each step, then runs them. Never deletes or trashes mail.",
     "params": [("text", "text", "what you want, e.g. \"put the Prime Day mails in Promotions\"", {"required": True})]},
]

BY_NAME = {s["name"]: s for s in SKILLS}
WINDOW_ONLY = tuple(s["name"] for s in SKILLS if s.get("window"))
AGENT_ACTIONS = tuple(s["name"] for s in SKILLS if s.get("agent", True))
MAX_STEPS = 5
PREV = "$prev"  # in a step: the mails found by the step before


def card():
    """The skills card, for another program: every action, what it does, its parameters."""
    out = []
    for s in SKILLS:
        out.append({"name": s["name"], "summary": s["summary"], "kind": s["kind"],
                    "json_door": not s.get("window"), "agent_mode": s.get("agent", True),
                    "params": [{"name": n, "type": t, "description": d, **({"required": True} if x.get("required") else {}),
                                **({"choices": x["choices"]} if "choices" in x else {})}
                               for n, t, d, x in s["params"]]})
    return out


def prompt_lines(cfg):
    """The actions the agent may use, one line each, for the model's prompt."""
    lines = []
    for name in AGENT_ACTIONS:
        s = BY_NAME[name]
        params = ", ".join(f"{n}{'' if x.get('required') else '?'}: {d}" for n, _t, d, x in s["params"])
        effect = {"read": "reads Gmail", "local": "local", "write": "CHANGES Gmail"}[s["kind"]]
        lines.append(f"- {name}({params}) [{effect}]: {s['summary']}")
    return "\n".join(lines)


def check(name, params, cfg, prev_ids=None):
    """The parameters of one step, cleaned and checked. Raises ValueError with a short reason."""
    if name not in AGENT_ACTIONS:
        raise ValueError(f"'{name}' is not an action the agent may use")
    if not isinstance(params, dict):
        raise ValueError(f"{name}: the parameters must be an object")
    themes, statuses = config.names(cfg["themes"]), config.names(cfg["statuses"])
    out = {}
    for pname, ptype, _desc, extra in BY_NAME[name]["params"]:
        value = params.get(pname)
        if value in (None, "") and not (ptype == "choice" and value == "" and "" in extra.get("choices", [])):
            if ptype == "ids" and prev_ids is not None and name == "relabel":
                value = PREV
            elif extra.get("required"):
                raise ValueError(f"{name}: '{pname}' is missing")
            else:
                continue
        out[pname] = clean(name, pname, ptype, value, extra, themes, statuses, prev_ids)
    if name == "relabel" and not (out.get("theme") or out.get("status")):
        raise ValueError("relabel: give a theme or a status")
    if name == "search" and not any(out.get(k) for k in ("sender", "subject", "words", "label", "inbox", "days")):
        raise ValueError("search: give at least one criterion")
    return out


def clean(name, pname, ptype, value, extra, themes, statuses, prev_ids):
    where = f"{name}: '{pname}'"
    if ptype == "int":
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{where} must be a number")
        if number < extra.get("min", 0) or number > extra.get("max", 10 ** 9):
            raise ValueError(f"{where} must be between {extra.get('min', 0)} and {extra.get('max')}")
        return number
    if ptype == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "yes", "1", "oui")
        return bool(value)
    if ptype == "text":
        return " ".join(str(value).split())[:200]
    if ptype == "choice":
        if value not in extra["choices"]:
            raise ValueError(f"{where} must be one of: {', '.join(c or '(empty)' for c in extra['choices'])}")
        return value
    if ptype in ("theme", "status"):
        allowed = themes if ptype == "theme" else statuses
        match = [x for x in allowed if x.lower() == str(value).strip().lower()]
        if not match:
            raise ValueError(f"{where}: unknown {ptype} '{value}'. Known: {', '.join(allowed)}")
        return match[0]
    if ptype == "words":
        words = value if isinstance(value, list) else [value]
        return config.clean_words(str(w) for w in words)
    if ptype == "ids":
        if value == PREV:
            if prev_ids is None:
                raise ValueError(f"{where}: no mails found by a step before")
            return list(prev_ids)
        ids = value if isinstance(value, list) else [value]
        ids = [str(x).strip() for x in ids]
        if not ids or len(ids) > 500 or not all(x.isdigit() for x in ids):
            raise ValueError(f"{where} must be 1 to 500 Gmail ids")
        return ids
    if ptype == "scope":
        if not isinstance(value, dict) or value.get("kind") not in ("count", "days", "all"):
            raise ValueError(f"{where} must be {{kind: count|days|all, value: N}}")
        return {"kind": value["kind"], "value": int(value.get("value") or 0)}
    raise ValueError(f"{where}: unknown type")
