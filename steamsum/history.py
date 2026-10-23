"""Saved summaries: what was summarised, with which model, and what the data looked like at the time.

Each run directory gets a small run.json (looked up on later runs) and a larger citations.json
(the reviews behind the [#n] numbers, needed to rebuild the report's links)."""

import datetime as dt
import json
from pathlib import Path

RUN = "run.json"
CITATIONS = "citations.json"
SNIPPET = 400  # characters of each cited review kept for the report's hover text


def snapshot(rows, window_start, announcements):
    """Review counts per window period and the announcements known now."""
    return {"periods": {r["label"]: [r["positive"], r["negative"]] for r in rows if r["end"] > window_start},
            "announcements": sorted([a["date"], a["title"]] for a in announcements if a["date"] >= window_start)}


def save(out_dir, *, info, languages, window_start, profile, summary_md, ids, usage, cost, snap):
    run = {"appid": info["appid"], "name": info["name"], "languages": languages, "window_start": window_start,
           "model": profile["model"], "profile": profile.get("name"),
           "created": dt.datetime.now().isoformat(timespec="seconds"),
           "summary": summary_md, "usage": usage, "cost": cost, "snapshot": snap}
    cites = {str(n): {"steamid": p.get("steamid"), "created": p["created"], "up": p["up"],
                      "period": p["period"], "record": p["record"][:SNIPPET]} for n, p in ids.items()}
    (out_dir / CITATIONS).write_text(json.dumps(cites, ensure_ascii=False), encoding="utf-8")
    (out_dir / RUN).write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")


def find(out_root, appid, languages, window_start):
    """Saved runs for this game, languages and window, newest first."""
    runs = []
    for p in Path(out_root).expanduser().glob(f"*/{RUN}"):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("appid") == appid and r.get("languages") == languages and r.get("window_start") == window_start:
            r["dir"] = str(p.parent)
            runs.append(r)
    return sorted(runs, key=lambda r: r["created"], reverse=True)


def citations(run):
    try:
        data = json.loads((Path(run["dir"]) / CITATIONS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {int(k): v for k, v in data.items()}


def changes(old, new):
    """What changed between two snapshots: new reviews, % positive then and now, new announcements."""
    def totals(s):
        pos = sum(p for p, n in s["periods"].values())
        neg = sum(n for p, n in s["periods"].values())
        return pos, pos + neg

    pos0, n0 = totals(old)
    pos1, n1 = totals(new)
    seen = {tuple(a) for a in old["announcements"]}
    return {"new_reviews": n1 - n0,
            "pct_before": pos0 / n0 * 100 if n0 else None, "pct_now": pos1 / n1 * 100 if n1 else None,
            "new_announcements": [a for a in new["announcements"] if tuple(a) not in seen]}


def _plural(n, one, few, many):
    """Polish plural: 1 recenzja, 2–4 recenzje (not 12–14), 5+ recenzji."""
    if n == 1:
        return one
    return few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many


def _pct(v):
    return f"{v:.1f}".replace(".", ",") + "%"


def describe(run, ch):
    """Human-readable lines about a saved run and what changed since (Polish UI)."""
    when = dt.datetime.fromisoformat(run["created"]).strftime("%d.%m %H:%M")
    lines = [f"Masz podsumowanie z {when} ({run['model']})."]
    parts = []
    n = ch["new_reviews"]
    if n > 0:
        part = f"{n} {_plural(n, 'nowa recenzja', 'nowe recenzje', 'nowych recenzji')}"
        if ch["pct_before"] is not None and ch["pct_now"] is not None:
            part += f" (odsetek pozytywnych w oknie {_pct(ch['pct_before'])} → {_pct(ch['pct_now'])})"
        parts.append(part)
    k = len(ch["new_announcements"])
    if k:
        titles = "; ".join(f"„{t}”" for _, t in ch["new_announcements"][:3])
        more = f" i {k - 3} więcej" if k > 3 else ""
        parts.append(f"{k} {_plural(k, 'nowe ogłoszenie', 'nowe ogłoszenia', 'nowych ogłoszeń')} twórców: {titles}{more}")
    lines.append("Od tego czasu: " + (", ".join(parts) if parts else "bez nowych recenzji i ogłoszeń") + ".")
    return lines
