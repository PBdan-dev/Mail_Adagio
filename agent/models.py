"""Model adapters. Each one turns (system, user, schema) into the model's text answer.

The rest of the code only calls `ask(system, user, schema)` and never knows which model answered.
"""
import json
import socket
import urllib.error
import urllib.request

DEFAULT_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434",
}


class ModelError(Exception):
    pass


class ModelTimeout(ModelError):
    """The model did not answer in time (one mail): skip it, do not stop the batch."""


def make_ask(model_cfg):
    """Return a function ask(system, user, schema) -> str for the configured provider."""
    provider = model_cfg["provider"]
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
    raise ModelError(f"Unknown provider '{provider}'")


def _anthropic(name, key, url):
    try:
        import anthropic
    except ImportError as e:
        raise ModelError("The 'anthropic' package is missing: pip install -r requirements.txt") from e
    client = anthropic.Anthropic(api_key=key, base_url=url, max_retries=3)

    def ask(system, user, schema):
        try:
            response = client.messages.create(
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
        data = _post(f"{url}/chat/completions", body, {"Authorization": f"Bearer {key}"})
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ModelError(f"Unexpected answer: {str(data)[:200]}") from e
    return ask


def ollama_models(url=None):
    """What Ollama has: {'installed': [names], 'running': [names loaded in memory]}."""
    url = (url or DEFAULT_URLS["ollama"]).rstrip("/")
    installed = _get(f"{url}/api/tags").get("models") or []
    running = _get(f"{url}/api/ps").get("models") or []
    return {"installed": [m.get("name") or m.get("model") for m in installed],
            "running": [m.get("name") or m.get("model") for m in running]}


def _ollama(name, url, timeout=60):
    """Ollama's own API: the `format` field forces an answer that fits the JSON schema."""
    no_think = {"on": True}  # models without a thinking mode refuse the "think" field

    def ask(system, user, schema):
        body = {
            "model": name,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            # short answer (no endless output), small context: less graphics memory, steadier speed
            "options": {"temperature": 0, "num_predict": 256, "num_ctx": 4096},
        }
        if no_think["on"]:
            body["think"] = False
        try:
            data = _post(f"{url}/api/chat", body, {}, timeout=timeout)
        except ModelTimeout:
            raise
        except ModelError as e:
            if not (no_think["on"] and "think" in str(e).lower()):
                raise
            no_think["on"] = False
            body.pop("think")
            data = _post(f"{url}/api/chat", body, {}, timeout=timeout)
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as e:
            raise ModelError(f"Unexpected answer: {str(data)[:200]}") from e
    return ask


def _get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise ModelError(f"HTTP {e.code} from {url}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ModelError(f"Ollama is not reachable at {url.rsplit('/api', 1)[0]}. Is it running?") from e


def _post(url, body, headers, timeout=60):
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
