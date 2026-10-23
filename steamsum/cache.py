"""Disk cache: one JSON file per key, in the OS-specific cache directory."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path


def cache_dir():
    if override := os.environ.get("STEAMSUM_CACHE"):
        base = Path(override)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "steamsum"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "steamsum" / "Cache"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "steamsum"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _path(key):
    h = hashlib.sha256(key.encode()).hexdigest()[:24]
    return cache_dir() / f"{h}.json"


def get(key, max_age=None):
    """Value or None. max_age=None means the entry never expires."""
    p = _path(key)
    try:
        if max_age is not None and time.time() - p.stat().st_mtime > max_age:
            return None
        with p.open(encoding="utf-8") as f:
            return json.load(f)["value"]
    except (OSError, ValueError, KeyError):
        return None


def put(key, value):
    p = _path(key)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({"key": key, "value": value}, f, ensure_ascii=False)
    os.replace(tmp, p)
