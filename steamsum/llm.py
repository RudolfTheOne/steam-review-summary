"""Model calls over two protocols:
- "openai": chat/completions (OpenAI, OpenRouter, DeepSeek, Gemini's OpenAI-compatible endpoint, Ollama, LM Studio);
- "anthropic": the Anthropic Messages API.
Raw HTTP via urllib rather than SDKs, so the tool has no dependencies beyond Python."""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import cache


class LLMError(RuntimeError):
    pass


KEYCHAIN_SERVICE = "steamsum"


def _run(cmd, shell=False):
    try:
        r = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def api_key(profile):
    """Lookup order: environment variable, `key_cmd` (any command that prints the key, e.g. `op read ...`,
    `pass show ...`, `secret-tool lookup ...`), macOS Keychain (service "steamsum", account = key_name),
    the key store of the `llm` tool if installed."""
    env = profile.get("key_env")
    if env and os.environ.get(env):
        return os.environ[env]
    if cmd := profile.get("key_cmd"):
        key = _run(cmd, shell=isinstance(cmd, str))
        if key:
            return key
        raise LLMError(f"Polecenie key_cmd nie zwróciło klucza: {cmd}")
    name = profile.get("key_name")
    if name and sys.platform == "darwin" and shutil.which("security"):
        key = _run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"])
        if key:
            return key
    if name and shutil.which("llm"):
        key = _run(["llm", "keys", "get", name])
        if key:
            return key
    if profile.get("api") == "openai" and "localhost" in profile.get("base_url", ""):
        return "local"
    hint = [f"zmienna środowiskowa {env}"] if env else []
    if name and sys.platform == "darwin":
        hint.append(f"Pęk kluczy: security add-generic-password -s {KEYCHAIN_SERVICE} -a {name} -w")
    hint.append("albo key_cmd w config.toml")
    raise LLMError("Brak klucza API. Ustaw: " + "; ".join(hint))


def _post_stream(url, headers, body, on_text):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        resp = urllib.request.urlopen(req, timeout=900)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:2000]
        raise LLMError(f"HTTP {e.code} z {url}: {detail}") from None
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue


def _openai(profile, key, system, user, on_text):
    body = {"model": profile["model"], "stream": True, "stream_options": {"include_usage": True},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if profile.get("reasoning_effort"):
        body["reasoning_effort"] = profile["reasoning_effort"]
    body.update(profile.get("extra", {}))
    url = profile["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {key}"}
    if "openrouter.ai" in url:
        headers.update({"HTTP-Referer": "https://github.com/local/steamsum", "X-Title": "steamsum"})
    text, usage, finish = [], {}, None
    for ev in _post_stream(url, headers, body, on_text):
        if ev.get("error"):
            raise LLMError(f"Błąd modelu: {ev['error']}")
        for ch in ev.get("choices") or []:
            piece = (ch.get("delta") or {}).get("content")
            if piece:
                text.append(piece)
                on_text(piece)
            finish = ch.get("finish_reason") or finish
        if ev.get("usage"):
            u = ev["usage"]
            usage = {"input": u.get("prompt_tokens"), "output": u.get("completion_tokens"),
                     "cost": u.get("cost")}  # OpenRouter reports the cost in the response
    if finish == "length":
        raise LLMError("Model przerwał odpowiedź (limit długości wyjścia).")
    return "".join(text), usage


def _anthropic(profile, key, system, user, on_text):
    body = {"model": profile["model"], "max_tokens": profile.get("max_tokens", 64000), "stream": True,
            "system": system, "messages": [{"role": "user", "content": user}]}
    if profile.get("effort"):
        body["output_config"] = {"effort": profile["effort"]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if profile.get("fallbacks", True):
        # On a safety-classifier refusal the API retries on a fallback model by itself.
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
    body.update(profile.get("extra", {}))
    url = profile.get("base_url", "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    text, usage, stop = [], {}, None
    for ev in _post_stream(url, headers, body, on_text):
        t = ev.get("type")
        if t == "message_start":
            u = ev["message"].get("usage", {})
            usage["input"] = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) \
                + (u.get("cache_creation_input_tokens") or 0)
        elif t == "content_block_delta" and ev["delta"].get("type") == "text_delta":
            text.append(ev["delta"]["text"])
            on_text(ev["delta"]["text"])
        elif t == "message_delta":
            stop = ev["delta"].get("stop_reason") or stop
            usage["output"] = ev.get("usage", {}).get("output_tokens")
        elif t == "error":
            raise LLMError(f"Błąd modelu: {ev.get('error')}")
    if stop == "refusal":
        raise LLMError("Model odmówił odpowiedzi (stop_reason: refusal).")
    if stop == "max_tokens":
        raise LLMError("Model przerwał odpowiedź (limit max_tokens).")
    return "".join(text), usage


def complete(profile, system, user, on_text=lambda s: None):
    key = api_key(profile)
    fn = {"openai": _openai, "anthropic": _anthropic}[profile["api"]]
    t = time.time()
    text, usage = fn(profile, key, system, user, on_text)
    usage["seconds"] = round(time.time() - t, 1)
    # Calibration: characters per token for this model (tokenizers differ).
    if usage.get("input"):
        cache.put(f"cpt/{profile['model']}", (len(system) + len(user)) / usage["input"])
    return text, usage


def chars_per_token(profile, default):
    return cache.get(f"cpt/{profile['model']}") or default


def openrouter_prices(model_id):
    """$/1M token prices and context length from OpenRouter's public catalogue (no key needed)."""
    data = cache.get("openrouter/models", max_age=86400)
    if data is None:
        try:
            with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=20) as r:
                data = {m["id"]: {"in": float(m["pricing"]["prompt"]) * 1e6,
                                  "out": float(m["pricing"]["completion"]) * 1e6,
                                  "context": m.get("context_length")}
                        for m in json.load(r)["data"]}
            cache.put("openrouter/models", data)
        except Exception as e:  # prices are only an estimate; failing to get them must not block the run
            print(f"  (nie pobrano cen z OpenRouter: {e})", file=sys.stderr)
            return None
    return data.get(model_id)
