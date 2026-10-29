"""Read, check and fill in config.yaml.

Everything Mail Adagio keeps lives in memoire/ (its own memory, as Orchestra asks): config.yaml, adagio.db,
the journal. The memory_ functions below are the only ones that write there. Secrets are never in it:
they go to the Windows vault (vault.py).
"""
import copy
import json
import os
import re
import shutil
from getpass import getpass
from pathlib import Path

import yaml

from . import vault

ROOT = Path(__file__).resolve().parent.parent
MEMOIRE = ROOT / "memoire"  # the agent's own memory: settings, learned rules, journal
EXAMPLE = ROOT / "config.example.yaml"
DEFAULT_PATH = MEMOIRE / "config.yaml"
# files kept next to the program before v0.9: moved into memoire/ at the first start
OLD_FILES = ("config.yaml", "adagio.db", "adagio.log", "adagio-window.log", "adagio-demo.db", "adagio-demo.log")

PROVIDERS = ("anthropic", "openrouter", "ollama")


class ConfigError(Exception):
    pass


def load(path=DEFAULT_PATH):
    """Load the config file and check that it makes sense."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"No config file at {path}. Run: python adagio.py setup")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    check(cfg)
    if move_secrets(path, cfg):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    gmail, model = cfg["gmail"], cfg["model"]
    gmail["app_password"] = gmail.get("app_password") or vault.secret_read(vault.GMAIL, gmail.get("email"))
    model["api_key"] = model.get("api_key") or vault.secret_read(vault.API_KEY, model.get("provider"))
    return cfg


def move_secrets(path, cfg):
    """An app password or API key still written in config.yaml (before v0.9): moved to the Windows vault,
    then removed from the file. Left as it is when there is no vault here. True if the file changed."""
    moved = {}
    pw, email = cfg["gmail"].get("app_password"), cfg["gmail"].get("email")
    if pw and email and vault.secret_save(vault.GMAIL, email, pw):
        moved["app_password"] = ""
    key, provider = cfg["model"].get("api_key"), cfg["model"].get("provider")
    if key and provider and vault.secret_save(vault.API_KEY, provider, key):
        moved["api_key"] = ""
    if moved:
        save_values(path, moved)
    return bool(moved)


def save_secrets(email=None, app_password="", provider=None, api_key=""):
    """Typed in the page or at setup: straight to the Windows vault, never into config.yaml."""
    if app_password and not vault.secret_save(vault.GMAIL, email, app_password):
        raise ConfigError("Cannot save the app password: the Windows vault is not available "
                          "(the keyring library is missing: run Mail Adagio.bat again, or pip install -r requirements.txt)")
    if api_key and not vault.secret_save(vault.API_KEY, provider, api_key):
        raise ConfigError("Cannot save the API key: the Windows vault is not available "
                          "(the keyring library is missing: pip install -r requirements.txt)")


def has_password(cfg):
    g = cfg.get("gmail") or {}
    return bool(g.get("app_password") or vault.secret_read(vault.GMAIL, g.get("email")))


def memory_store_text(path, text):
    """Write a file of the agent's memory (config.yaml): the only way config.yaml is written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # only you can read the file (no effect on Windows)
    except OSError:
        pass


def memory_move_old_files(root=ROOT, memoire=None):
    """Before v0.9 the settings, the database and the journal were next to the program: move them into
    memoire/ (once; a file already in memoire/ is never replaced). Returns the names moved."""
    memoire = Path(memoire or root / "memoire")
    moved = []
    for name in OLD_FILES:
        old, new = Path(root) / name, memoire / name
        if old.exists() and not new.exists():
            memoire.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
            moved.append(name)
    return moved


def check(cfg):
    for section in ("gmail", "model", "themes", "statuses", "fallback"):
        if section not in cfg:
            raise ConfigError(f"Missing section '{section}' in config.yaml")
    if cfg["model"].get("provider") not in PROVIDERS:
        raise ConfigError(f"model.provider must be one of: {', '.join(PROVIDERS)}")
    themes = names(cfg["themes"])
    statuses = names(cfg["statuses"])
    if len(set(themes)) != len(themes) or len(set(statuses)) != len(statuses):
        raise ConfigError("Theme and status names must be unique")
    if set(themes) & set(statuses):
        raise ConfigError("A theme and a status cannot share a name (both become Gmail labels)")
    if not isinstance(cfg.get("label_order") or [], list):
        raise ConfigError("label_order must be a list of theme and status names")
    chosen = cfg.get("colors") or {}
    if not isinstance(chosen, dict) or any(c not in GMAIL_COLORS for c in chosen.values()):
        raise ConfigError(f"colors must map labels to Gmail colours: {', '.join(GMAIL_COLORS)}")
    kw = cfg.get("keywords") or {}
    if not isinstance(kw, dict) or not all(isinstance(v, list) for v in kw.values()):
        raise ConfigError("keywords must map each theme to a list of words")
    all_labels = list(labels(cfg).values()) + [check_label(cfg), delete_label(cfg)]
    if len({x.lower() for x in all_labels}) != len(all_labels):
        raise ConfigError(f"Two themes or statuses would get the same Gmail label "
                          f"(\"{TO_CHECK}\" and \"{TO_DELETE}\" are taken)")
    for label in all_labels:
        if label.lower() in GMAIL_RESERVED or label.startswith("[Gmail]"):
            raise ConfigError(f"Gmail does not allow a label named '{label}'. "
                              f"Add a 'label:' line with another name under it in config.yaml")
    for e in cfg["themes"] + cfg["statuses"]:
        if e.get("color") and e["color"] not in GMAIL_COLORS:
            raise ConfigError(f"color of '{e['name']}' must be one of Gmail's: {', '.join(GMAIL_COLORS)}")
    if cfg["fallback"]["theme"] not in themes or cfg["fallback"]["status"] not in statuses:
        raise ConfigError("fallback.theme and fallback.status must exist in themes and statuses")
    cfg.setdefault("behavior", {})
    cfg["behavior"].setdefault("body_chars", 1500)
    cfg["behavior"].setdefault("act_alone", False)
    cfg["behavior"].setdefault("batch_size", 100)
    cfg["behavior"].setdefault("archive", True)
    cfg["model"].setdefault("timeout_s", 60)
    cfg["behavior"].setdefault("to_delete", True)
    cfg["behavior"].setdefault("mark_read", False)
    cfg["behavior"].setdefault("learn_gmail", True)
    cfg["behavior"].setdefault("mark_read_themes", list(LOW_INTEREST))
    if not isinstance(cfg["behavior"]["mark_read_themes"], list):
        raise ConfigError("behavior.mark_read_themes must be a list of theme names")
    cfg.setdefault("local_models", [])


# Low interest: mails of these themes with the status Info can be marked as read (behavior.mark_read).
LOW_INTEREST = ("Newsletters", "Promotions", "Social & Notifications")
LOW_STATUS = "Info"


TO_DELETE = "To delete"  # label for mails the model finds useless; only you send them to the Trash


TO_CHECK = "To check"  # label for mails the model is not sure about: have a look


def check_label(cfg):
    """Gmail label of the mails to check: "97 - To check" when labels are numbered."""
    return f"97 - {TO_CHECK}" if numbered(cfg) else TO_CHECK


def delete_label(cfg):
    """Gmail label of the mails proposed for deletion: "98 - To delete" when labels are numbered."""
    return f"98 - {TO_DELETE}" if numbered(cfg) else TO_DELETE


def low_interest(cfg, theme, status):
    b = cfg["behavior"]
    return bool(b["mark_read"]) and status == LOW_STATUS and theme in b["mark_read_themes"]


def names(entries):
    return [e["name"] for e in entries]


# Gmail's own labels: a user label cannot have these names (case does not matter).
GMAIL_RESERVED = {"inbox", "sent", "sent mail", "drafts", "draft", "spam", "trash", "bin", "starred",
                  "important", "chats", "chat", "all mail", "unread", "scheduled", "snoozed"}
DEFAULT_LABELS = {"important": "Priority"}  # Gmail already has a hidden "Important" label


# Gmail's label colours (the API accepts only these). Mail Adagio shows the same ones, so a label
# looks the same in the app and in Gmail. IMAP cannot colour a label: pick it once in Gmail.
GMAIL_COLORS = {
    "Red": "#fb4c2f", "Orange": "#ffad47", "Yellow": "#fad165", "Green": "#16a765", "Mint": "#43d692",
    "Teal": "#2da2bb", "Blue": "#4a86e8", "Purple": "#a479e2", "Pink": "#f691b2", "Brown": "#7a4706",
    "Dark Red": "#822111", "Dark Orange": "#a46a21", "Dark Green": "#076239", "Dark Blue": "#1c4587",
    "Dark Purple": "#41236d", "Dark Pink": "#83334c", "Gray": "#666666", "Light Gray": "#cccccc",
    "Dark Gray": "#434343", "Black": "#000000",
}
DEFAULT_COLORS = {
    "health": "Teal", "finance": "Green", "admin": "Dark Blue", "work & projects": "Blue",
    "job search": "Purple", "shopping & deliveries": "Orange", "travel": "Mint", "family & friends": "Pink",
    "newsletters": "Dark Green", "promotions": "Gray", "social & notifications": "Light Gray",
    "other": "Brown", "important": "Red", "to do": "Yellow", "info": "Dark Gray", "to delete": "Dark Red",
    "to check": "Dark Orange",
}
SPARE_COLORS = ["Dark Purple", "Dark Pink", "Black"]  # for your own themes


def color_of(entry, spare=0):
    """Gmail colour name of a theme or status: its `color:` if set, else the default."""
    return entry.get("color") or DEFAULT_COLORS.get(entry["name"].lower(), SPARE_COLORS[spare % len(SPARE_COLORS)])


def colors(cfg):
    """{theme or status name: {"gmail": colour name, "hex": "#..."}}"""
    out, spare, chosen = {}, 0, cfg.get("colors") or {}  # chosen: picked in the app (Color & Order)
    for e in cfg["themes"] + cfg["statuses"]:
        name = chosen.get(e["name"]) or color_of(e, spare)
        spare += not e.get("color") and e["name"].lower() not in DEFAULT_COLORS
        out[e["name"]] = {"gmail": name, "hex": GMAIL_COLORS.get(name, "#666666")}
    for fixed, default in ((TO_CHECK, "Dark Orange"), (TO_DELETE, "Dark Red")):
        name = chosen.get(fixed) or default
        out[fixed] = {"gmail": name, "hex": GMAIL_COLORS[name]}
    return out


def save_colors(path, colors_by_label):
    """Write the `colors:` line at the end of config.yaml: {label name: Gmail colour name}."""
    ensure_file(path)
    path = Path(path)
    data = {str(k): v for k, v in (colors_by_label or {}).items() if v in GMAIL_COLORS}
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^# Colours of the labels.*\n", "", text)
    text = re.sub(r"(?m)^colors:.*\n?", "", text).rstrip("\n") + "\n"
    if data:
        text += ("\n# Colours of the labels, picked in the app (Views > Color & Order). Gmail's own colour is set by hand.\n"
                 "colors: " + json.dumps(data, ensure_ascii=False) + "\n")
    memory_store_text(path, text)
    return load(path)


def label_of(entry):
    """Gmail label of a theme or status: its `label:` if set, else its name."""
    return entry.get("label") or DEFAULT_LABELS.get(entry["name"].lower(), entry["name"])


# Numbered labels ("01 - Priority"): statuses 01, 02..., themes 11, 12..., Other always 99.
# Two digits, so Gmail's alphabetical list shows them in this order.
RECOMMENDED_ORDER = ["important", "to do", "info", "health", "finance", "admin", "family & friends",
                     "work & projects", "job search", "travel", "shopping & deliveries",
                     "social & notifications", "newsletters", "promotions", "other"]


def order(cfg):
    """(statuses, themes) names in label order: your saved order, else the recommended one."""
    saved = [n for n in (cfg.get("label_order") or [])]

    def key(item):
        i, name = item
        if name in saved:
            return (0, saved.index(name))
        if name.lower() in RECOMMENDED_ORDER:
            return (1, RECOMMENDED_ORDER.index(name.lower()))
        return (2, i)

    def sort(entries):
        out = [n for _, n in sorted(enumerate(names(entries)), key=key)]
        return [n for n in out if n.lower() != "other"] + [n for n in out if n.lower() == "other"]
    return sort(cfg["statuses"]), sort(cfg["themes"])


def numbers(cfg):
    """{name: number} used in front of the Gmail labels."""
    statuses, themes = order(cfg)
    out = {n: i for i, n in enumerate(statuses, 1)}
    out.update({n: (99 if n.lower() == "other" else i) for i, n in enumerate(themes, 11)})
    return out


def numbered(cfg):
    return bool(cfg.get("label_order"))


def labels(cfg):
    """{theme or status name: Gmail label}"""
    plain = {e["name"]: label_of(e) for e in cfg["themes"] + cfg["statuses"]}
    if not numbered(cfg):
        return plain
    nums = numbers(cfg)
    return {name: f"{nums[name]:02d} - {label}" for name, label in plain.items()}


# Typical words for each theme, shown to the model as clues (not strict rules). Change them in the app:
# Views > Keywords. Saved as `keywords:` at the end of config.yaml; a theme missing there uses these.
DEFAULT_KEYWORDS = {
    "Health": ["doctor", "hospital", "clinic", "pharmacy", "prescription", "medical appointment", "lab results",
               "dentist", "health insurance", "médecin", "mutuelle", "ordonnance"],
    "Finance": ["bank", "investments", "insurance", "account statement", "credit card", "payment", "transfer",
                "invoice", "tax", "savings", "loan", "salary", "banque", "impôts", "virement"],
    "Admin": ["government", "city hall", "residence permit", "passport", "utilities", "electricity", "internet provider",
              "contract", "lease", "rent", "housing", "official letter", "mairie", "préfecture"],
    "Work & Projects": ["meeting", "project", "client", "colleague", "deadline", "report", "quote", "proposal",
                        "GitHub", "réunion", "devis"],
    "Job Search": ["job offer", "job alert", "application", "recruiter", "interview", "position", "hiring",
                   "LinkedIn Jobs", "Indeed", "candidature", "entretien", "offre d'emploi"],
    "Shopping & Deliveries": ["purchases", "buy", "credit card", "receipt", "return", "order", "refund", "shipping",
                              "delivery", "tracking number", "parcel", "commande", "livraison", "colis"],
    "Travel": ["flight", "boarding pass", "train", "hotel", "booking", "reservation", "check-in", "itinerary",
               "car rental", "Airbnb", "vol", "billet"],
    "Family & Friends": ["family", "friends", "birthday", "dinner", "weekend", "photos", "news", "famille",
                         "anniversaire"],
    "Newsletters": ["newsletter", "digest", "weekly", "monthly", "article", "blog", "podcast", "edition",
                    "Substack"],
    "Promotions": ["sale", "discount", "promo code", "special offer", "deal", "% off", "Black Friday", "coupon",
                   "free shipping", "soldes", "réduction"],
    "Social & Notifications": ["notification", "new follower", "comment", "mention", "security alert",
                               "new sign-in", "password reset", "verification code", "Facebook", "Instagram"],
    "Other": [],
}


MAX_KEYWORDS = 40  # per theme: more would drown the prompt of a small model


def clean_words(words):
    """A tidy keyword list: trimmed, no duplicates (case ignored), at most 40 words of 40 characters."""
    out, seen = [], set()
    for w in words or []:
        w = " ".join(str(w).split())[:40]
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out[:MAX_KEYWORDS]


def keywords(cfg):
    """{theme: [words]} for every theme: your list from config.yaml, else the default one."""
    saved = cfg.get("keywords") or {}
    return {t: clean_words(saved[t] if t in saved else DEFAULT_KEYWORDS.get(t, [])) for t in names(cfg["themes"])}


def save_keywords(path, words_by_theme):
    """Write the `keywords:` line at the end of config.yaml (one JSON line, readable by YAML)."""
    ensure_file(path)
    path = Path(path)
    data = {str(t): clean_words(w) for t, w in (words_by_theme or {}).items()}
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^# Typical words of each theme.*\n", "", text)
    text = re.sub(r"(?m)^keywords:.*\n?", "", text).rstrip("\n") + "\n"
    text += ("\n# Typical words of each theme, given to the model as clues. Change them in the app: Views > Keywords.\n"
             "keywords: " + json.dumps(data, ensure_ascii=False) + "\n")
    memory_store_text(path, text)
    return load(path)


RESERVED = ("to check", "to delete", "priority")


def renamed(cfg, old, new, hint=None):
    """The configuration with the theme `old` named `new` (and its new hint, if given): keywords, label
    order, colours and "mark as read" themes follow. Raises ConfigError for a name that cannot be used."""
    new = " ".join(str(new or "").split())
    themes = names(cfg["themes"])
    if old not in themes:
        raise ConfigError(f"Unknown theme '{old}'. Themes: {', '.join(themes)}")
    if old == cfg["fallback"]["theme"]:
        raise ConfigError(f"'{old}' is the theme used when nothing fits: it keeps its name")
    if not new or len(new) > 40 or any(c in new for c in '/\\"'):
        raise ConfigError("The new name must have 1 to 40 characters, without / \\ or quotes")
    taken = {n.lower() for n in themes + names(cfg["statuses"]) if n != old} | set(RESERVED)
    if new.lower() in taken:
        raise ConfigError(f"'{new}' is already the name of a theme, a status or a Mail Adagio label")
    out = copy.deepcopy(cfg)
    for e in out["themes"]:
        if e["name"] == old:
            e["name"] = new
            if e.get("label"):
                e["label"] = new
            if hint:
                e["hint"] = " ".join(str(hint).split())[:200]
    swap = lambda items: [new if x == old else x for x in items]
    if out.get("keywords") and old in out["keywords"]:
        out["keywords"] = {(new if k == old else k): v for k, v in out["keywords"].items()}
    elif old in DEFAULT_KEYWORDS:  # the default words of the theme go with it
        out["keywords"] = {**(out.get("keywords") or {}), new: DEFAULT_KEYWORDS[old]}
    if out.get("label_order"):
        out["label_order"] = swap(out["label_order"])
    if out.get("colors") and old in out["colors"]:
        out["colors"] = {(new if k == old else k): v for k, v in out["colors"].items()}
    elif not next(e for e in cfg["themes"] if e["name"] == old).get("color"):  # keep the colour it had
        out["colors"] = {**(out.get("colors") or {}), new: colors(cfg)[old]["gmail"]}
    behavior = out.get("behavior") or {}
    if behavior.get("mark_read_themes"):
        behavior["mark_read_themes"] = swap(behavior["mark_read_themes"])
    check(out)
    return out


def save_rename(path, old, new, hint=None):
    """Rename a theme in config.yaml, keeping the comments; the lines that name it follow."""
    ensure_file(path)
    path = Path(path)
    before = load(path)
    after = renamed(before, old, new, hint)
    new = next(e["name"] for e in after["themes"] if e["name"] not in names(before["themes"]))
    text = path.read_text(encoding="utf-8")
    start = re.search(r"(?m)^themes:\s*$", text)
    entry = re.compile(rf"(?m)^(\s*-\s*name:\s*)(\"{re.escape(old)}\"|'{re.escape(old)}'|{re.escape(old)})(\s*(#.*)?)$")
    m = entry.search(text, start.end() if start else 0)
    if not m:
        raise ConfigError(f"Theme '{old}' not found in {path.name}")
    text = text[:m.start()] + m.group(1) + json.dumps(new, ensure_ascii=False) + (m.group(3) or "") + text[m.end():]
    nxt = re.compile(r"(?m)^\s*-\s*name:|^\S").search(text, m.start() + 1)
    end = nxt.start() if nxt else len(text)
    block = text[m.start():end]
    if hint:
        block = re.sub(r"(?m)^(\s*hint:\s*).*$", lambda h: h.group(1) + json.dumps(after_hint(after, new), ensure_ascii=False), block, count=1)
    block = re.sub(r"(?m)^(\s*label:\s*).*$", lambda h: h.group(1) + json.dumps(new, ensure_ascii=False), block, count=1)
    memory_store_text(path, text[:m.start()] + block + text[end:])
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if raw.get("keywords") is not None or after.get("keywords") != before.get("keywords"):
        save_keywords(path, after.get("keywords") or {})
    if raw.get("label_order"):
        save_order(path, after["label_order"])
    if after.get("colors"):
        save_colors(path, after["colors"])
    if (raw.get("behavior") or {}).get("mark_read_themes"):
        save_values(path, {"mark_read_themes": after["behavior"]["mark_read_themes"]})
    return load(path)


def after_hint(cfg, name):
    return next(e.get("hint", "") for e in cfg["themes"] if e["name"] == name)


BEHAVIOR_KEYS = ("act_alone", "batch_size", "archive", "mark_read", "mark_read_themes", "to_delete")


def set_value(text, key, value):
    """Replace the first `key: ...` line in the YAML text, keeping its comment."""
    pattern = re.compile(rf"^(\s*{re.escape(key)}:\s*)(\"[^\"]*\"|[^#\n]*?)(\s*#.*)?$", re.M)
    if not pattern.search(text) and key in BEHAVIOR_KEYS and re.search(r"(?m)^behavior:\s*$", text):
        # an older config.yaml without this option: add it to the behavior section
        return re.sub(r"(?m)^behavior:\s*$", lambda m: f"behavior:\n  {key}: {json.dumps(value, ensure_ascii=False)}",
                      text, count=1)
    if not pattern.search(text):
        raise ConfigError(f"Key '{key}' not found in config file")
    return pattern.sub(lambda m: m.group(1) + json.dumps(value) + (m.group(3) or ""), text, count=1)


def save_values(path, values):
    """Write a few values into config.yaml without losing the comments."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        text = set_value(text, key, value)
    memory_store_text(path, text)


DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "openrouter": "anthropic/claude-haiku-5.5",
                  "ollama": "active"}


def ensure_file(path=DEFAULT_PATH):
    """Create config.yaml from the example if it does not exist yet. True if created."""
    path = Path(path)
    if path.exists():
        return False
    memory_store_text(path, EXAMPLE.read_text(encoding="utf-8"))
    return True


def save_order(path, names_in_order=None):
    """Write (or remove, with None) the `label_order:` line at the end of config.yaml."""
    ensure_file(path)
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^# Order and numbering of the Gmail labels.*\n", "", text)
    text = re.sub(r"(?m)^label_order:.*\n?", "", text).rstrip("\n") + "\n"
    if names_in_order:
        text += ("\n# Order and numbering of the Gmail labels (01 - ..., 11 - ...). Change it in the app: "
                 "Labels in Gmail > Order.\nlabel_order: " + json.dumps(list(names_in_order), ensure_ascii=False) + "\n")
    memory_store_text(path, text)
    return load(path)


def save_behavior(path, act_alone=None, batch_size=None, mark_read=None, mark_read_themes=None, learn_gmail=None):
    """The run options shown on the page."""
    ensure_file(path)
    values = {}
    if learn_gmail is not None:
        values["learn_gmail"] = bool(learn_gmail)
    if mark_read is not None:
        values["mark_read"] = bool(mark_read)
    if mark_read_themes is not None:
        values["mark_read_themes"] = [str(x) for x in mark_read_themes]
    if act_alone is not None:
        values["act_alone"] = bool(act_alone)
    if batch_size is not None:
        values["batch_size"] = max(50, min(int(batch_size), 1000))
    save_values(path, values)
    return load(path)


def save_settings(path, email, provider, model_name="", app_password="", api_key="", base_url=None):
    """Used by the web page. Empty password or key fields keep the saved value."""
    if provider not in PROVIDERS:
        raise ConfigError(f"Unknown provider '{provider}'")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
        raise ConfigError("This does not look like an e-mail address")
    ensure_file(path)
    values = {"email": email.strip(), "provider": provider,
              "model_name": model_name.strip().strip("'\"").strip() or DEFAULT_MODELS[provider]}
    save_secrets(values["email"], app_password.replace(" ", "").strip(), provider, api_key.strip())
    if base_url is not None:
        values["base_url"] = base_url.strip()
    save_values(path, values)
    return load(path)


def setup(path=DEFAULT_PATH, ask=input, ask_secret=getpass, say=print):
    """Interactive first run: create config.yaml and ask for what is missing."""
    path = Path(path)
    if ensure_file(path):
        say(f"Created {path.name} from the example.")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    values = {}

    email = cfg["gmail"].get("email") or ask("  Gmail address: ").strip()
    values["email"] = email
    secrets = {}
    if not has_password({"gmail": {**cfg["gmail"], "email": email}}):
        say("Create an app password at https://myaccount.google.com/apppasswords")
        say("(2-step verification must be on, and IMAP enabled in Gmail settings).")
        secrets["app_password"] = ask_secret("  App password (hidden): ").replace(" ", "").strip()

    provider = ask(f"  Model provider ({' / '.join(PROVIDERS)}) [{cfg['model']['provider']}]: ").strip()
    provider = provider or cfg["model"]["provider"]
    if provider not in PROVIDERS:
        raise ConfigError(f"Unknown provider '{provider}'")
    values["provider"] = provider
    if provider == "ollama":
        values["model_name"] = ask("  Ollama model (active / auto / a name) [active]: ").strip() or "active"
    elif provider == "openrouter" and cfg["model"]["model_name"].startswith("claude-"):
        values["model_name"] = ask("  OpenRouter model [anthropic/claude-haiku-5.5]: ").strip() \
            or "anthropic/claude-haiku-5.5"
    if provider != "ollama" and not (cfg["model"].get("api_key") or vault.secret_read(vault.API_KEY, provider)):
        secrets["api_key"] = ask_secret(f"  {provider} API key (hidden): ").strip()

    save_secrets(email, secrets.get("app_password", ""), provider, secrets.get("api_key", ""))
    save_values(path, values)
    say(f"Saved to {path}. Edit it any time to change themes or settings.")
    return load(path)


def needs_setup(path=DEFAULT_PATH):
    path = Path(path)
    if not path.exists():
        return True
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return not ((cfg.get("gmail") or {}).get("email") and has_password(cfg))
