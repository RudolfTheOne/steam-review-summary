"""Settings: built-in defaults merged with the user's config.toml (overrides fields, adds profiles)."""

import copy
import json
import os
import re
import sys
import tomllib
from pathlib import Path

DEFAULTS = {
    "model": "deepseek",
    "languages": ["english", "czech", "german", "hungarian", "polish"],
    "window_months": 9,
    "budget_tokens": 350_000,
    "max_tokens": 600_000,  # hard input limit regardless of the budget
    "output_dir": str(Path.home() / "steamsum"),
    "answer_language": "Polish",
    "models": {
        "deepseek": {"api": "openai", "base_url": "https://openrouter.ai/api/v1",
                     "model": "deepseek/deepseek-v4.1-flash",
                     "key_env": "OPENROUTER_API_KEY", "key_name": "openrouter"},
        "gemini": {"api": "openai", "base_url": "https://openrouter.ai/api/v1",
                   "model": "google/gemini-3.8-flash",
                   "key_env": "OPENROUTER_API_KEY", "key_name": "openrouter"},
        "luna": {"api": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-6-luna",
                 "reasoning_effort": "low", "key_env": "OPENAI_API_KEY", "key_name": "openai",
                 "price_id": "openai/gpt-6-luna"},
        "claude": {"api": "anthropic", "model": "claude-opus-5-5", "effort": "medium",
                   "key_env": "ANTHROPIC_API_KEY", "key_name": "anthropic",
                   "price_in": 4.0, "price_out": 20.0},
    },
    # Ad-hoc model selection: --model openrouter:qwen/qwen4 or anthropic:claude-sonnet-5-5
    "providers": {
        "openrouter": {"api": "openai", "base_url": "https://openrouter.ai/api/v1",
                       "key_env": "OPENROUTER_API_KEY", "key_name": "openrouter"},
        "openai": {"api": "openai", "base_url": "https://api.openai.com/v1",
                   "key_env": "OPENAI_API_KEY", "key_name": "openai"},
        "anthropic": {"api": "anthropic", "key_env": "ANTHROPIC_API_KEY", "key_name": "anthropic"},
        "deepseek": {"api": "openai", "base_url": "https://api.deepseek.com",
                     "key_env": "DEEPSEEK_API_KEY", "key_name": "deepseek"},
        "ollama": {"api": "openai", "base_url": "http://localhost:11434/v1"},
    },
}


def config_path():
    if override := os.environ.get("STEAMSUM_CONFIG"):
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "steamsum" / "config.toml"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "steamsum" / "config.toml"


def _merge(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load():
    cfg = copy.deepcopy(DEFAULTS)
    p = config_path()
    if p.exists():
        with p.open("rb") as f:
            _merge(cfg, tomllib.load(f))
    return cfg


def save_default_model(name):
    """Set the top-level `model = "..."` in config.toml, keeping the rest of the file (comments included)."""
    p = config_path()
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    m = re.search(r"^\[", text, re.M)  # top-level keys end where the first [section] starts
    head, tail = (text[:m.start()], text[m.start():]) if m else (text, "")
    line = f"model = {json.dumps(name)}"
    pat = re.compile(r"^model\s*=\s*(\"[^\"]*\"|'[^']*')([ \t]*#.*)?$", re.M)
    if pat.search(head):
        head = pat.sub(lambda mm: line + (mm.group(2) or ""), head, count=1)
    else:
        head = (head.rstrip("\n") + "\n" if head.strip() else "") + line + "\n" + ("\n" if tail else "")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(head + tail, encoding="utf-8")
    return p


def resolve_model(cfg, name):
    """A profile name from the config, or "provider:model"."""
    if name in cfg["models"]:
        prof = dict(cfg["models"][name])
    elif ":" in name:
        provider, model = name.split(":", 1)
        if provider not in cfg["providers"]:
            raise SystemExit(f"Nieznany dostawca „{provider}”. Znane: {', '.join(cfg['providers'])}")
        prof = {**cfg["providers"][provider], "model": model}
    else:
        raise SystemExit(f"Nieznany model „{name}”. Profile: {', '.join(cfg['models'])}; "
                         f"albo dostawca:model, np. openrouter:google/gemini-3.8-flash")
    prof["name"] = name
    if "price_id" not in prof and "openrouter.ai" in prof.get("base_url", ""):
        prof["price_id"] = prof["model"]
    return prof
