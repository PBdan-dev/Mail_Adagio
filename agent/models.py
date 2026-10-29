"""Model adapters. Each one turns (system, user, schema) into the model's text answer.

The rest of the code only calls `ask(system, user, schema)` and never knows which model answered.
Run by Orchestra (a mission), the only model is Orchestra's gate ("porte"): ORCHESTRA_MODELE_URL and
ORCHESTRA_JETON, nothing else. Every network call goes through the read_ functions below.
"""
import os
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434",
}


class ModelError(Exception):
    def __init__(self, message="", code=""):
        super().__init__(message)
        self.code = code  # ollama_down | ollama_missing | "" : lets the page show the right help


class ModelTimeout(ModelError):
    """The model did not answer in time (one mail): skip it, do not stop the batch."""


ONLY_PORTE = {"on": False}  # True during a mission: Orchestra's gate is the only model


def no_other_model(what):
    if ONLY_PORTE["on"]:
        raise ModelError(f"In a mission the only model is Orchestra's gate ({what} refused)")


def make_ask(model_cfg):
    """Return a function ask(system, user, schema) -> str for the configured provider."""
    provider = model_cfg["provider"]
    if provider != "porte":
        no_other_model(provider)
    name = model_cfg.get("model_name")
    key = model_cfg.get("api_key") or None
    url = (model_cfg.get("base_url") or DEFAULT_URLS.get(provider, "")).rstrip("/")
    if not name:
        raise ModelError("No model chosen (model.model_name is empty)")
    if provider == "anthropic":
        return _anthropic(name, key, url or None)
    if provider == "openrouter":
        return _openai_compatible(name, key, url)
    if provider == "ollama":
        return _ollama(name, url, int(model_cfg.get("timeout_s") or 60))
    if provider == "porte":
        return _porte(name, model_cfg["base_url"], model_cfg["api_key"], int(model_cfg.get("timeout_s") or 600))
    raise ModelError(f"Unknown provider '{provider}'")


def _anthropic(name, key, url):
    try:
        import anthropic
    except ImportError as e:
        raise ModelError("The 'anthropic' package is missing: pip install -r requirements.txt") from e
    client = anthropic.Anthropic(api_key=key, base_url=url, max_retries=2)  # at most 2 new tries

    def ask(system, user, schema):
        try:
            response = read_anthropic(
                client,
                model=name,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.AuthenticationError as e:
            raise ModelError("Anthropic refused the API key") from e
        except anthropic.APIStatusError as e:
            raise ModelError(f"Anthropic error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ModelError(f"Cannot reach Anthropic: {e}") from e
        if response.stop_reason == "refusal":
            raise ModelError("The model declined to classify this mail")
        return next((b.text for b in response.content if b.type == "text"), "")
    return ask


def read_anthropic(client, **request):
    """One call to Anthropic's API (the cloud model you chose in Settings; never in a mission)."""
    return client.messages.create(**request)


def _openai_compatible(name, key, url):
    """OpenRouter, and any server that speaks the OpenAI chat format."""
    def ask(system, user, schema):
        body = {
            "model": name,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "classification", "strict": True, "schema": schema}},
            "temperature": 0,
        }
        data = read_http_post(f"{url}/chat/completions", body, {"Authorization": f"Bearer {key}"})
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ModelError(f"Unexpected answer: {str(data)[:200]}") from e
    return ask


OLLAMA_DOWN = ("Ollama is not running. Start it: open the Ollama app (Windows Start menu), "
               "or run `ollama serve` in a terminal. Then try again.")
OLLAMA_MISSING = ("Ollama is not installed on this computer. Get it at https://ollama.com/download, "
                  "then run: ollama pull qwen3.5:9b")
tried_start = {"done": False}  # start Ollama at most once per session: never wait on it again and again


def action_start_ollama(url=None, wait=20):
    """Ollama is not running: start it if it is installed on this computer. True if it now answers."""
    no_other_model("starting Ollama")
    url = (url or DEFAULT_URLS["ollama"]).rstrip("/")
    host = urllib.parse.urlparse(url).hostname or ""
    exe = _ollama_exe()
    if tried_start["done"] or host not in ("localhost", "127.0.0.1") or not exe:
        return False
    tried_start["done"] = True
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS) if sys.platform == "win32" else 0
    try:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags, start_new_session=sys.platform != "win32")
    except OSError:
        return False
    for _ in range(wait * 2):
        try:
            read_http_get(f"{url}/api/tags", timeout=1)
            return True
        except ModelError:
            time.sleep(0.5)
    return False


def ollama_models(url=None, start=False):
    """What Ollama has: {'installed': [names], 'running': [names loaded in memory]}.

    start: if Ollama is not running, try to start it once, else say clearly how to."""
    no_other_model("Ollama")
    url = (url or DEFAULT_URLS["ollama"]).rstrip("/")
    try:
        read_http_get(f"{url}/api/tags", timeout=3)
    except ModelError as e:
        if not (start and action_start_ollama(url)):
            local = (urllib.parse.urlparse(url).hostname or "") in ("localhost", "127.0.0.1")
            if local and not _ollama_exe():
                raise ModelError(OLLAMA_MISSING, "ollama_missing") from e
            raise ModelError(OLLAMA_DOWN if local else f"Ollama does not answer at {url}.", "ollama_down") from e
    installed = read_http_get(f"{url}/api/tags").get("models") or []
    running = read_http_get(f"{url}/api/ps").get("models") or []
    return {"installed": [m.get("name") or m.get("model") for m in installed],
            "running": [m.get("name") or m.get("model") for m in running]}


KEEP_ALIVE = "30m"  # Ollama keeps the model in memory this long after the last mail (its default is 5 min)
LOAD_TIMEOUT = 300  # loading a model from disk can take a few minutes on a slow drive


def load_model(name, url=None):
    """Load a model into Ollama's memory now, so the first mail does not wait. Returns the seconds it took."""
    no_other_model("Ollama")
    url = (url or DEFAULT_URLS["ollama"]).rstrip("/")
    started = time.time()
    read_http_post(f"{url}/api/generate", {"model": name, "prompt": "", "stream": False, "keep_alive": KEEP_ALIVE},
          {}, timeout=LOAD_TIMEOUT)
    return round(time.time() - started, 1)


def _ollama(name, url, timeout=60):
    """Ollama's own API: the `format` field forces an answer that fits the JSON schema."""
    no_think = {"on": True}  # models without a thinking mode refuse the "think" field
    first = {"on": True}  # the first call may have to load the model first: give it more time

    def ask(system, user, schema):
        body = {
            "model": name,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            # short answer (no endless output), small context: less graphics memory, steadier speed
            "options": {"temperature": 0, "num_predict": 256, "num_ctx": 4096},
            "keep_alive": KEEP_ALIVE,
        }
        wait = max(timeout, LOAD_TIMEOUT) if first["on"] else timeout
        if no_think["on"]:
            body["think"] = False
        try:
            data = read_http_post(f"{url}/api/chat", body, {}, timeout=wait)
        except ModelTimeout:
            raise
        except ModelError as e:
            if not (no_think["on"] and "think" in str(e).lower()):
                raise
            no_think["on"] = False
            body.pop("think")
            data = read_http_post(f"{url}/api/chat", body, {}, timeout=wait)
        first["on"] = False
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as e:
            raise ModelError(f"Unexpected answer: {str(data)[:200]}") from e
    return ask


PORTE_ENV = ("ORCHESTRA_MODELE_URL", "ORCHESTRA_JETON")


def porte_config(name="inherit"):
    """The model settings of a mission: Orchestra's gate only. ModelError if Orchestra gave none."""
    url, token = (os.environ.get(k, "").strip() for k in PORTE_ENV)
    if not url or not token:
        raise ModelError("Orchestra's model gate is missing (ORCHESTRA_MODELE_URL / ORCHESTRA_JETON): "
                         "a mission never uses another model")
    return {"provider": "porte", "model_name": name or "inherit", "base_url": url.rstrip("/"), "api_key": token}


def _porte(name, url, token, timeout=600):
    """Orchestra's gate speaks Ollama's /api/chat. Orchestra picks and loads the model; Mail Adagio only
    asks. No other model if it does not answer: the mission fails with the reason."""
    def ask(system, user, schema):
        body = {"model": name, "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": user}],
                "format": schema, "stream": False, "think": False,
                "options": {"temperature": 0, "num_predict": 256, "num_ctx": 4096}}
        data = read_http_post(f"{url}/api/chat", body, {"Authorization": f"Bearer {token}"}, timeout=timeout)
        try:
            return data["message"]["content"] or ""
        except (KeyError, TypeError) as e:
            raise ModelError(f"Unexpected answer from Orchestra's gate: {str(data)[:200]}") from e
    return ask


def _ollama_exe():
    """Path of the ollama program, "" if not installed (on Windows it is not always on the PATH)."""
    import os
    default = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe") if sys.platform == "win32" else ""
    return shutil.which("ollama") or (default if default and os.path.exists(default) else "")


def read_http_get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise ModelError(f"HTTP {e.code} from {url}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ModelError(f"Ollama is not reachable at {url.rsplit('/api', 1)[0]}. Is it running?") from e


def read_http_post(url, body, headers, timeout=60):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise ModelError(f"HTTP {e.code} from {url}: {detail}") from e
    except (TimeoutError, socket.timeout) as e:
        raise ModelTimeout(f"No answer after {timeout} s") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            raise ModelTimeout(f"No answer after {timeout} s") from e
        raise ModelError(f"Cannot reach {url}: {e}") from e
