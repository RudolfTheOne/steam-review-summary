"""Builds the model prompt. Code computes the numbers; the model writes prose, not statistics."""

import datetime as dt

SYSTEM = """You analyse Steam user reviews of one game for a player deciding whether to buy it or return to it.

Write in natural {language}. Use Markdown with exactly these sections:

## W skrócie
3–5 sentences: overall verdict and the single most important recent change.

## Zalety
The most important strengths players point out, most important first. One bullet per strength, with a short explanation.

## Wady
The most important drawbacks, most important first, same form.

## Co się zmieniło
How opinions in the analysis window differ from before it, and how they moved within the window, in chronological order. Tie a change to an official announcement or patch when reviews themselves point to it; do not guess causes that reviews do not mention. If the window covers the whole life of the game, describe how the launch period compares with later months.

## Na co uważać
Only if the data supports it: signs of launch hype that later faded, praise from people with very little playtime, differences between key activations and Steam purchases, review bombs about off-topic issues, recurring complaints that the score hides. If there is nothing, say so in one sentence.

Rules:
- Base everything only on the reviews, table and announcements provided. Never use outside knowledge about the game.
- Cite 1–3 supporting reviews for every claim by their id in square brackets, e.g. [#12] or [#12, #57].
- Do not compute or state your own counts or percentages: the sample is deliberately unbalanced. Use words like "many", "some", "a few". The only numbers you may quote are from the score table.
- Quote short fragments of reviews when they say something better than a paraphrase; translate them into {language}.
- Prefer concrete details (what exactly crashes, which mode, which patch) over generic statements."""


def build(info, timeline_rows, window_start, picked, announcements, languages, today, answer_language):
    lines = [
        f"GAME: {info['name']} (Steam app {info['appid']}), release date: {info['release']}",
        f"TODAY: {today.isoformat()}",
        f"ANALYSIS WINDOW: from {window_start} to today",
        f"REVIEW LANGUAGES: {', '.join(languages)}",
        "",
        "REVIEW SCORE BY PERIOD (computed by code from ALL reviews in these languages, not from the sample):",
        "period | reviews | % positive | % from keys (not bought on Steam) | in window",
    ]
    for r in timeline_rows:
        n = r["positive"] + r["negative"]
        if not n:
            continue
        keys = (f"{(n - r['steam_positive'] - r['steam_negative']) / n * 100:.0f}%"
                if "steam_positive" in r else "n/a")
        lines.append(f"{r['label']} | {n} | {r['positive'] / n * 100:.0f}% | {keys} | "
                     f"{'yes' if r['end'] > window_start else 'no'}")
    if announcements:
        lines += ["", "OFFICIAL ANNOUNCEMENTS FROM THE DEVELOPER (date — title — beginning of text):"]
        for a in sorted(announcements, key=lambda a: a["date"]):
            excerpt = " ".join(a["excerpt"].split())[:240]
            lines.append(f"{a['date']} — {a['title']} — {excerpt}")
    lines += [
        "",
        "REVIEW RECORD FORMAT: #id date sign playtime helpful flags | text",
        "  + recommends, - does not recommend; Nh = hours played when writing; N↑ = 'helpful' votes;",
        "  K = game activated with a key (not bought on Steam); F = received for free; EA = written during early access.",
        "  Line breaks inside a review are shown as ' / '. Very long reviews are cut and end with […].",
        "SAMPLE: per period, half most-helpful and half newest reviews; the minority side (positive or negative)",
        "  is over-represented to at least a quarter so that both strengths and drawbacks are visible.",
        "  Use the score table for proportions, never the sample.",
    ]
    by_period = {}
    for p in picked:
        by_period.setdefault(p["period"], []).append(p)
    ids = {}
    n = 0
    for period, items in by_period.items():
        title = (f"{period.split(': ', 1)[1]}, before the analysis window — most helpful reviews of that period"
                 if period.startswith("przed oknem") else period)
        lines += ["", f"=== PERIOD: {title} ({len(items)} reviews) ==="]
        for p in sorted(items, key=lambda p: p["created"]):
            n += 1
            ids[n] = p
            d = dt.datetime.fromtimestamp(p["created"], dt.timezone.utc).date().isoformat()
            lines.append(f"#{n} {d} {p['record']}")
    return SYSTEM.format(language=answer_language), "\n".join(lines), ids
