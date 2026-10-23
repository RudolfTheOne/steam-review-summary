"""btop-style terminal chart: % positive as a braille line, review counts as bars below."""

import os
import shutil
import sys

BRAILLE = 0x2800
DOT = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]]  # [row][column]
BARS = " ▁▂▃▄▅▆▇█"

# Steam thresholds: >=80% very positive, 70-79 mostly positive, 40-69 mixed, <40 negative.
def _color(pct):
    if pct >= 80:
        return "\033[38;5;71m"
    if pct >= 70:
        return "\033[38;5;149m"
    if pct >= 40:
        return "\033[38;5;214m"
    return "\033[38;5;167m"


DIM, RESET = "\033[2m", "\033[0m"


def _use_color():
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        os.system("")  # enables ANSI sequences in the Windows console
    return True


def _can_braille():
    try:
        "⣿▁█".encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _line(grid, x0, y0, x1, y1):
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        grid.add((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def render(rows, window_start, announcements=(), height=8, width=None):
    """rows: timeline rows (label, start, end, positive, negative). Returns text."""
    history = _history([r for r in rows if r.get("yearly")])
    pts = [(r, r["positive"] + r["negative"]) for r in rows if not r.get("yearly")]
    pts = [(r, n) for r, n in pts if n > 0]
    if not pts:
        return history or "(brak recenzji w wybranych językach)"
    color = _use_color()
    if not _can_braille():
        return _ascii(pts)
    width = width or min(shutil.get_terminal_size((100, 24)).columns, 140)
    cw = max(1, min(3, (width - 8) // len(pts)))  # characters per period
    pcts = [r["positive"] / n * 100 for r, n in pts]
    lo = max(0, (min(pcts) // 5) * 5)
    hi = min(100, (max(pcts) // 5) * 5 + 5)
    if hi - lo < 10:
        hi = min(100, lo + 10)
    hdots = height * 4
    def ydot(p):
        return round((hi - p) / (hi - lo) * (hdots - 1))
    xs = [i * cw * 2 + cw for i in range(len(pts))]  # period centre in dot columns
    grid = set()
    for i in range(len(pts)):
        if i:
            _line(grid, xs[i - 1], ydot(pcts[i - 1]), xs[i], ydot(pcts[i]))
        else:
            grid.add((xs[0], ydot(pcts[0])))
    ncols = len(pts) * cw
    out = []
    for row in range(height):
        label = f"{hi - (hi - lo) * row / (height - 1):3.0f}% " if row in (0, height // 2, height - 1) else "     "
        line = [DIM + label + RESET if color else label]
        for c in range(ncols):
            code = 0
            for dy in range(4):
                for dx in range(2):
                    if (c * 2 + dx, row * 4 + dy) in grid:
                        code |= DOT[dy][dx]
            ch = chr(BRAILLE + code) if code else " "
            if color and code:
                ch = _color(pcts[c // cw]) + ch + RESET
            line.append(ch)
        out.append("".join(line))
    # Square-root scale so a launch spike does not flatten everything else.
    top = max(n for _, n in pts) ** 0.5
    bars = "".join(BARS[max(1, round((n ** 0.5) / top * 8))] * cw for _, n in pts)
    out.append(("  n  " if not color else DIM + "  n  " + RESET) + bars)
    # Markers: ┃ start of the analysis window, ◆ developer announcement in that period.
    marks = []
    adates = sorted(a["date"] for a in announcements)
    first = _first_in_window(pts, window_start)
    for r, _ in pts:
        m = " " * cw
        if any(r["start"] <= d < r["end"] for d in adates):
            m = "◆" + " " * (cw - 1)
        if r is first and r is not pts[0][0]:
            m = "┃" + m[1:] if cw == 1 or m[0] == " " else "┃◆" + m[2:]
        marks.append(m)
    out.append("     " + "".join(marks))
    out.append("     " + _xlabels(pts, cw))
    legend = ((f"┃ początek okna analizy ({window_start})   " if first is not None and first is not pts[0][0] else "")
              + ("◆ ogłoszenie twórców   " if adates else "") + "słupki: liczba recenzji (skala pierwiastkowa)")
    out.append(DIM + legend + RESET if color else legend)
    if history:
        out.append(history)
    return "\n".join(out)


def _history(rows):
    parts = []
    for r in rows:
        n = r["positive"] + r["negative"]
        if n:
            parts.append(f"{r['label'][:4]} {r['positive'] / n * 100:.0f}%")
    return ("Wcześniej, rok po roku: " + " · ".join(parts)) if parts else ""


def _first_in_window(pts, window_start):
    for r, _ in pts:
        if r["end"] > window_start:
            return r
    return None


def _xlabels(pts, cw):
    total = len(pts) * cw
    buf = [" "] * total
    last_end = -2
    for i, (r, _) in enumerate(pts):
        lab = r["label"]
        if len(lab) == 7:  # YYYY-MM: January and the first month with year, others month only
            lab = lab if lab.endswith("-01") or i == 0 else lab[5:]
        pos = i * cw
        if pos <= last_end + 1 or pos + len(lab) > total:
            continue
        buf[pos:pos + len(lab)] = lab
        last_end = pos + len(lab)
    return "".join(buf)


def _ascii(pts):
    lines = []
    for r, n in pts:
        p = r["positive"] / n * 100
        lines.append(f"{r['label']:>12} {p:5.1f}% {'#' * round(p / 2.5):<40} n={n}")
    return "\n".join(lines)
