"""steamsum: summarise a game's Steam reviews.

Usage: python3 -m steamsum <Steam link or app id> [options]
"""

import argparse
import datetime as dt
import re
import sys
import webbrowser
from pathlib import Path

from . import chart_term, config, llm, prompt, report, sample, steam, timeline


def _err(*a):
    print(*a, file=sys.stderr, flush=True)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    cfg = config.load()
    ap = argparse.ArgumentParser(prog="steamsum", description="Streszczenie recenzji gry ze Steama.")
    ap.add_argument("game", help="link do sklepu Steam, link do Steam Review Explorer albo numer gry")
    ap.add_argument("-m", "--model", default=cfg["model"],
                    help=f"profil ({', '.join(cfg['models'])}) albo dostawca:model, np. openrouter:google/gemini-3.8-flash")
    ap.add_argument("--months", type=int, default=cfg["window_months"], help="okno analizy: ostatnie N miesięcy")
    ap.add_argument("--since", help="okno analizy od daty RRRR-MM-DD (zamiast --months)")
    ap.add_argument("--budget", type=int, default=cfg["budget_tokens"], help="budżet tokenów wejścia dla recenzji")
    ap.add_argument("--languages", default=",".join(cfg["languages"]), help="języki recenzji, po przecinku (Steam: english, polish, …)")
    ap.add_argument("--dry-run", action="store_true", help="pobierz dane, pokaż wykres, próbkę i koszt; nie wołaj modelu")
    ap.add_argument("--out", default=cfg["output_dir"], help="katalog na raporty")
    ap.add_argument("--no-open", action="store_true", help="nie otwieraj raportu w przeglądarce")
    a = ap.parse_args(argv)

    today = dt.date.today()
    languages = [x.strip() for x in a.languages.split(",") if x.strip()]
    if a.since:
        window_start = dt.date.fromisoformat(a.since)
    else:
        window_start = timeline.add_months(timeline.month_start(today), -a.months)
    ws = window_start.isoformat()

    appid = steam.parse_appid(a.game)
    info = steam.app_info(appid)
    first = dt.date.fromisoformat(info["reviews_start"]) if info["reviews_start"] else today - dt.timedelta(days=365)
    _err(f"{info['name']} (app {appid}), premiera: {info['release']}, recenzje od {first}")
    _err("Pobieram liczby recenzji w czasie…")
    rows = timeline.build(appid, languages, first, today)
    total = sum(r["positive"] + r["negative"] for r in rows)
    if not total:
        sys.exit("Brak recenzji w wybranych językach.")
    announcements = steam.news(appid, max(first, timeline.add_months(timeline.month_start(today), -(timeline.MONTHLY - 1))))
    print("\n" + chart_term.render(rows, ws, announcements) + "\n", flush=True)
    in_window = sum(r["positive"] + r["negative"] for r in rows if r["end"] > ws)
    _err(f"Recenzji w wybranych językach: {total}, w oknie od {ws}: {in_window}")
    if in_window < 30:
        _err("Uwaga: w oknie analizy jest bardzo mało recenzji — wnioski o zmianach będą słabe. "
             "Rozważ więcej języków (--languages) albo dłuższe okno (--months).")

    profile = config.resolve_model(cfg, a.model)
    prices = llm.openrouter_prices(profile["price_id"]) if profile.get("price_id") else None
    p_in = profile.get("price_in", prices["in"] if prices else None)
    p_out = profile.get("price_out", prices["out"] if prices else None)
    budget = min(a.budget, cfg["max_tokens"])
    if prices and prices.get("context"):
        budget = min(budget, int(prices["context"] * 0.85) - 20_000)
    cpt = llm.chars_per_token(profile, sample.DEFAULT_CHARS_PER_TOKEN)

    _err(f"Dobieram recenzje (budżet {budget // 1000} tys. tokenów, {cpt:.2f} znaku/token)…")
    r0 = steam.requests_made
    rows2, picked = sample.sample(appid, languages, rows, ws, budget, cpt, today)
    system, user, ids = prompt.build(info, rows, ws, picked, [x for x in announcements if x["date"] >= ws],
                                     languages, today, cfg["answer_language"])
    est = round((len(system) + len(user)) / cpt)
    cost_est = (est * p_in + 3000 * p_out) / 1e6 if p_in is not None else None
    _err(f"Próbka: {len(picked)} recenzji, ~{est // 1000} tys. tokenów wejścia"
         + (f", szacowany koszt ~${cost_est:.2f}" if cost_est is not None else "")
         + f" ({steam.requests_made - r0} zapytań do Steama)")
    if est > cfg["max_tokens"]:
        sys.exit(f"Szacunek {est} tokenów przekracza limit {cfg['max_tokens']}; zmniejsz --budget.")

    slug = re.sub(r"[^\w]+", "-", info["name"].lower()).strip("-")[:40] or str(appid)
    out_dir = Path(a.out).expanduser() / f"{today.isoformat()}-{slug}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompt.txt").write_text(system + "\n\n=====\n\n" + user, encoding="utf-8")

    summary_md, usage, cost = "", {}, None
    if not a.dry_run:
        _err(f"Pytam model {profile['model']}…\n")
        try:
            summary_md, usage = llm.complete(profile, system, user, on_text=lambda s: print(s, end="", flush=True))
        except llm.LLMError as e:
            sys.exit(f"\nBłąd modelu: {e}\nDane i prompt są w {out_dir}")
        print("\n")
        cost = usage.get("cost")
        if cost is None and p_in is not None and usage.get("input"):
            cost = (usage["input"] * p_in + (usage.get("output") or 0) * p_out) / 1e6
        _err(f"Wejście {usage.get('input')} tok., wyjście {usage.get('output')} tok., {usage.get('seconds')} s"
             + (f", koszt ~${cost:.3f}" if cost is not None else ""))
        (out_dir / "summary.md").write_text(summary_md, encoding="utf-8")

    page = report.build(info, rows, ws, announcements, summary_md, ids,
                        {"languages": languages, "today": today.isoformat(), "model": profile["model"],
                         "usage": usage, "cost": cost, "min_chars": sample.MIN_REVIEW_CHARS})
    html_path = out_dir / "report.html"
    html_path.write_text(page, encoding="utf-8")
    _err(f"Raport: {html_path}")
    if not a.no_open:
        webbrowser.open(html_path.resolve().as_uri())


if __name__ == "__main__":
    main()
