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
    if cfg["fallback"]["theme"] not in themes or cfg["fallback"]["status"] not in statuses:
        raise ConfigError("fallback.theme and fallback.status must exist in themes and statuses")
    cfg.setdefault("behavior", {})
    cfg["behavior"].setdefault("body_chars", 1500)
    cfg.setdefault("local_models", [])


def names(entries):
    return [e["name"] for e in entries]


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
                  "ollama": ""}


def ensure_file(path=DEFAULT_PATH):
    """Create config.yaml from the example if it does not exist yet. True if created."""
    path = Path(path)
    if path.exists():
        return False
    shutil.copy(EXAMPLE, path)
    return True


def save_settings(path, email, provider, model_name="", app_password="", api_key=""):
    """Used by the web page. Empty password or key fields keep the saved value."""
    if provider not in PROVIDERS:
        raise ConfigError(f"Unknown provider '{provider}'")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
        raise ConfigError("This does not look like an e-mail address")
    ensure_file(path)
    values = {"email": email.strip(), "provider": provider,
              "model_name": model_name.strip() or DEFAULT_MODELS[provider]}
    if app_password.strip():
        values["app_password"] = app_password.replace(" ", "").strip()
    if api_key.strip():
        values["api_key"] = api_key.strip()
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
        values["model_name"] = ask("  Ollama model ('' = choose from my hardware): ").strip()
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
