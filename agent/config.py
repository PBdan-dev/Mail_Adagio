"""Read, check and fill in config.yaml."""
import json
import os
import re
import shutil
from getpass import getpass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config.example.yaml"
DEFAULT_PATH = ROOT / "config.yaml"

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
    return cfg


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
    labels = [label_of(e) for e in cfg["themes"] + cfg["statuses"]]
    if len({x.lower() for x in labels}) != len(labels):
        raise ConfigError("Two themes or statuses would get the same Gmail label")
    for label in labels:
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
    cfg.setdefault("local_models", [])


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
    "other": "Brown", "important": "Red", "to do": "Yellow", "info": "Dark Gray",
}
SPARE_COLORS = ["Dark Purple", "Dark Pink", "Dark Orange", "Dark Red", "Black"]  # for your own themes


def color_of(entry, spare=0):
    """Gmail colour name of a theme or status: its `color:` if set, else the default."""
    return entry.get("color") or DEFAULT_COLORS.get(entry["name"].lower(), SPARE_COLORS[spare % len(SPARE_COLORS)])


def colors(cfg):
    """{theme or status name: {"gmail": colour name, "hex": "#..."}}"""
    out, spare = {}, 0
    for e in cfg["themes"] + cfg["statuses"]:
        name = color_of(e, spare)
        spare += not e.get("color") and e["name"].lower() not in DEFAULT_COLORS
        out[e["name"]] = {"gmail": name, "hex": GMAIL_COLORS.get(name, "#666666")}
    return out


def label_of(entry):
    """Gmail label of a theme or status: its `label:` if set, else its name."""
    return entry.get("label") or DEFAULT_LABELS.get(entry["name"].lower(), entry["name"])


def labels(cfg):
    """{theme or status name: Gmail label}"""
    return {e["name"]: label_of(e) for e in cfg["themes"] + cfg["statuses"]}


def set_value(text, key, value):
    """Replace the first `key: ...` line in the YAML text, keeping its comment."""
    pattern = re.compile(rf"^(\s*{re.escape(key)}:\s*)(\"[^\"]*\"|[^#\n]*?)(\s*#.*)?$", re.M)
    if not pattern.search(text):
        raise ConfigError(f"Key '{key}' not found in config file")
    return pattern.sub(lambda m: m.group(1) + json.dumps(value) + (m.group(3) or ""), text, count=1)


def save_values(path, values):
    """Write a few values into config.yaml without losing the comments."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        text = set_value(text, key, value)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # only you can read the file (no effect on Windows)
    except OSError:
        pass


DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "openrouter": "anthropic/claude-haiku-5.5",
                  "ollama": "active"}


def ensure_file(path=DEFAULT_PATH):
    """Create config.yaml from the example if it does not exist yet. True if created."""
    path = Path(path)
    if path.exists():
        return False
    shutil.copy(EXAMPLE, path)
    return True


def save_behavior(path, act_alone=None, batch_size=None):
    """The two run options shown on the page."""
    ensure_file(path)
    values = {}
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
    if app_password.strip():
        values["app_password"] = app_password.replace(" ", "").strip()
    if api_key.strip():
        values["api_key"] = api_key.strip()
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
    if not cfg["gmail"].get("app_password"):
        say("Create an app password at https://myaccount.google.com/apppasswords")
        say("(2-step verification must be on, and IMAP enabled in Gmail settings).")
        values["app_password"] = ask_secret("  App password (hidden): ").replace(" ", "").strip()

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
    if provider != "ollama" and not cfg["model"].get("api_key"):
        values["api_key"] = ask_secret(f"  {provider} API key (hidden): ").strip()

    save_values(path, values)
    say(f"Saved to {path}. Edit it any time to change themes or settings.")
    return load(path)


def needs_setup(path=DEFAULT_PATH):
    path = Path(path)
    if not path.exists():
        return True
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    gmail = cfg.get("gmail", {})
    return not (gmail.get("email") and gmail.get("app_password"))
