"""A single, self-overwriting status line on stderr (only when stderr is a terminal)."""

import sys
import threading
import time

_lock = threading.Lock()
_current = ""
_suffix = ""
_enabled = sys.stderr.isatty()


def _line():
    return _current + (f" · {_suffix}" if _current and _suffix else "")


def status(text):
    """Replace the status line."""
    global _current
    with _lock:
        _current = text
        if _enabled:
            sys.stderr.write("\r\033[K" + _line())
            sys.stderr.flush()


def suffix(text):
    """Set a counter shown after the status text (e.g. requests made so far)."""
    global _suffix
    with _lock:
        _suffix = text
        if _enabled and _current:
            sys.stderr.write("\r\033[K" + _line())
            sys.stderr.flush()


def note(text):
    """Print a permanent message above the status line, then redraw the status line."""
    with _lock:
        if _enabled:
            sys.stderr.write("\r\033[K" + text + "\n" + _line())
        else:
            sys.stderr.write(text + "\n")
        sys.stderr.flush()


def clear():
    global _current
    with _lock:
        _current = ""
        if _enabled:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()


class Ticker:
    """Shows `label` with elapsed seconds until stopped, e.g. while waiting for a model's first token."""

    def __init__(self, label):
        self.label, self._stop = label, threading.Event()
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.wait(0.5):
            status(f"{self.label} {time.monotonic() - self._t0:.0f} s")

    def __enter__(self):
        status(f"{self.label} 0 s")
        self._thread.start()
        return self

    def stop(self):
        if not self._stop.is_set():
            self._stop.set()
            clear()

    def __exit__(self, *exc):
        self.stop()
