"""steamsum: summarise a game's Steam reviews.

Usage: python3 -m steamsum <Steam link or app id> [options]
"""

import argparse
import datetime as dt
import re
import sys
import webbrowser
from pathlib import Path

from . import chart_term, config, history, llm, progress, prompt, report, sample, steam, term_md, timeline


def _err(*a):
    progress.note(" ".join(str(x) for x in a))


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    cfg = config.load()
    ap = argparse.ArgumentParser(prog="steamsum", description="Streszczenie recenzji gry ze Steama.")
    ap.add_argument("game", nargs="?", help="link do sklepu Steam, link do Steam Review Explorer albo numer gry")
    ap.add_argument("-m", "--model", default=cfg["model"],
                    help=f"profil ({', '.join(cfg['models'])}) albo dostawca:model, np. openrouter:google/gemini-3.8-flash")
    ap.add_argument("--months", type=int, default=cfg["window_months"], help="okno analizy: ostatnie N miesięcy")
    ap.add_argument("--since", help="okno analizy od daty RRRR-MM-DD (zamiast --months)")
    ap.add_argument("--budget", type=int, default=cfg["budget_tokens"], help="budżet tokenów wejścia dla recenzji")
    ap.add_argument("--languages", default=",".join(cfg["languages"]), help="języki recenzji, po przecinku (Steam: english, polish, …)")
    ap.add_argument("--dry-run", action="store_true", help="pobierz dane, pokaż wykres, próbkę i koszt; nie wołaj modelu")
    ap.add_argument("--out", default=cfg["output_dir"], help="katalog na raporty")
    ap.add_argument("--no-open", action="store_true", help="nie otwieraj raportu w przeglądarce")
    again = ap.add_mutually_exclusive_group()
    again.add_argument("--refresh", action="store_true", help="zawsze generuj nowe podsumowanie, nawet jeśli jest zapisane")
    again.add_argument("--cached", action="store_true", help="pokaż zapisane podsumowanie bez pytania (jeśli jest)")
    ap.add_argument("--set-default-model", metavar="MODEL",
                    help="zapisz model jako domyślny w config.toml (można podać bez gry)")
    a = ap.parse_args(argv)

    if a.set_default_model:
        config.resolve_model(cfg, a.set_default_model)  # fails loudly on an unknown name
        path = config.save_default_model(a.set_default_model)
        print(f"Domyślny model: {a.set_default_model} (zapisano w {path})")
        if not a.game:
            return
        args = argv if argv is not None else sys.argv[1:]
        if not any(x in ("-m", "--model") or x.startswith("--model=") for x in args):
            a.model = a.set_default_model  # no explicit --model: use the one just saved
    if not a.game:
        ap.error("podaj link do gry albo jej numer")

    today = dt.date.today()
    languages = [x.strip() for x in a.languages.split(",") if x.strip()]
    if a.since:
        window_start = dt.date.fromisoformat(a.since)
    else:
        window_start = timeline.add_months(timeline.month_start(today), -a.months)
    ws = window_start.isoformat()

    appid = steam.parse_appid(a.game)
    progress.status("Pobieram informacje o grze…")
    info = steam.app_info(appid)
    first = dt.date.fromisoformat(info["reviews_start"]) if info["reviews_start"] else today - dt.timedelta(days=365)
    _err(f"{info['name']} (app {appid}), premiera: {info['release']}, recenzje od {first}")
    rows = timeline.build(appid, languages, first, today)
    total = sum(r["positive"] + r["negative"] for r in rows)
    if not total:
        sys.exit("Brak recenzji w wybranych językach.")
    progress.status("Pobieram ogłoszenia twórców…")
    announcements = steam.news(appid, max(first, timeline.add_months(timeline.month_start(today), -(timeline.MONTHLY - 1))))
    progress.clear()
    print("\n" + chart_term.render(rows, ws, announcements) + "\n", flush=True)
    in_window = sum(r["positive"] + r["negative"] for r in rows if r["end"] > ws)
    _err(f"Recenzji w wybranych językach: {total}, w oknie od {ws}: {in_window}")
    if in_window < 30:
        _err("Uwaga: w oknie analizy jest bardzo mało recenzji — wnioski o zmianach będą słabe. "
             "Rozważ więcej języków (--languages) albo dłuższe okno (--months).")

    profile = config.resolve_model(cfg, a.model)
    snap = history.snapshot(rows, ws, announcements)
    if _offer_saved(a, profile, info, languages, ws, rows, announcements, snap, today):
        return
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
    model_slug = re.sub(r"[^\w]+", "-", profile["name"].lower()).strip("-")[:30]
    out_dir = Path(a.out).expanduser() / f"{today.isoformat()}-{slug}-{model_slug}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompt.txt").write_text(system + "\n\n=====\n\n" + user, encoding="utf-8")

    summary_md, usage, cost = "", {}, None
    if not a.dry_run:
        _err(f"Pytam model {profile['model']}…")
        progress.suffix("")
        ticker = progress.Ticker("Model czyta recenzje i myśli…")
        md = term_md.Renderer()

        def on_text(s):
            ticker.stop()  # the first token ends the wait
            md.feed(s)

        try:
            with ticker:
                summary_md, usage = llm.complete(profile, system, user, on_text=on_text)
        except llm.LLMError as e:
            sys.exit(f"\nBłąd modelu: {e}\nDane i prompt są w {out_dir}")
        md.close()
        print()
        cost = usage.get("cost")
        if cost is None and p_in is not None and usage.get("input"):
            cost = (usage["input"] * p_in + (usage.get("output") or 0) * p_out) / 1e6
        _err(f"Wejście {usage.get('input')} tok., wyjście {usage.get('output')} tok., {usage.get('seconds')} s"
             + (f", koszt ~${cost:.3f}" if cost is not None else ""))
        (out_dir / "summary.md").write_text(summary_md, encoding="utf-8")
        history.save(out_dir, info=info, languages=languages, window_start=ws, profile=profile,
                     summary_md=summary_md, ids=ids, usage=usage, cost=cost, snap=snap)

    page = report.build(info, rows, ws, announcements, summary_md, ids,
                        {"languages": languages, "today": today.isoformat(), "model": profile["model"],
                         "usage": usage, "cost": cost, "min_chars": sample.MIN_REVIEW_CHARS})
    html_path = out_dir / "report.html"
    html_path.write_text(page, encoding="utf-8")
    _err(f"Raport: {html_path}")
    if not a.no_open:
        webbrowser.open(html_path.resolve().as_uri())


def _last_run_cost(run):
    """'ostatnio ~$0,03, ok. 1 min 10 s' from the saved run, or '' if unknown."""
    parts = []
    if run.get("cost") is not None:
        parts.append(f"ostatnio ~${run['cost']:.2f}".replace(".", ","))
    secs = (run.get("usage") or {}).get("seconds")
    if secs:
        m, sec = divmod(round(secs), 60)
        parts.append(f"ok. {m} min {sec} s" if m else f"ok. {sec} s")
    return ", ".join(parts)


def _offer_saved(a, profile, info, languages, ws, rows, announcements, snap, today):
    """If this game was already summarised with this model and window, say what changed since and
    offer the saved summary. Returns True when the saved one was shown (nothing else to do)."""
    runs = history.find(a.out, info["appid"], languages, ws)
    same = [r for r in runs if r["model"] == profile["model"]]
    if not same:
        if a.cached:
            sys.exit("Nie ma zapisanego podsumowania dla tej gry, modelu, języków i okna."
                     + (f" Jest z modelu {runs[0]['model']} (--model {runs[0]['profile']} --cached)." if runs else ""))
        if runs:
            _err(f"Jest zapisane podsumowanie z innego modelu: {runs[0]['model']} "
                 f"({runs[0]['created'][:16].replace('T', ' ')}); pokażesz je przez --model {runs[0]['profile']} --cached")
        return False
    if a.refresh:
        return False
    prev = same[0]
    note = history.describe(prev, history.changes(prev["snapshot"], snap))
    for line in note:
        _err(line)
    if a.dry_run:
        return False
    if not a.cached:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False  # nobody to ask: behave as before and generate a new one
        last = _last_run_cost(prev)
        question = f"[W]yświetl zapisane / [O]dśwież{f' ({last})' if last else ''}? [W/o] "
        while True:
            try:
                answer = input(question).strip().lower()
            except EOFError:
                return False
            if answer in ("", "w"):
                break
            if answer == "o":
                return False
            _err("Wpisz w (wyświetl zapisane) albo o (odśwież); Enter = wyświetl.")
    print()
    md = term_md.Renderer()
    md.feed(prev["summary"])
    md.close()
    print()
    out_dir = Path(prev["dir"])
    page = report.build(info, rows, ws, announcements, prev["summary"], history.citations(prev),
                        {"languages": languages, "today": today.isoformat(), "model": prev["model"],
                         "usage": prev.get("usage") or {}, "cost": prev.get("cost"),
                         "min_chars": sample.MIN_REVIEW_CHARS, "summary_note": " ".join(note)})
    html_path = out_dir / "report.html"
    html_path.write_text(page, encoding="utf-8")
    _err(f"Raport (wykresy aktualne, streszczenie zapisane): {html_path}")
    if not a.no_open:
        webbrowser.open(html_path.resolve().as_uri())
    return True


if __name__ == "__main__":
    main()
