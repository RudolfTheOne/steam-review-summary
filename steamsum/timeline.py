"""Review score timeline: recent months one by one, earlier years one by one.
Request count stays bounded however old the game is (at most ~25 years + 24 months)."""

import datetime as dt

from . import steam

MONTHLY = 24  # number of recent months shown individually


def month_start(d):
    return d.replace(day=1)


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return d.replace(year=d.year + y, month=m + 1, day=1)


def periods(first_review, today):
    """(start, end, label, closed) tuples from the first review until tomorrow."""
    end = today + dt.timedelta(days=1)
    monthly_from = add_months(month_start(today), -(MONTHLY - 1))
    out = []
    if first_review < monthly_from:
        y = first_review.year
        while dt.date(y, 1, 1) < monthly_from:
            s = max(dt.date(y, 1, 1), first_review)
            e = min(dt.date(y + 1, 1, 1), monthly_from)
            label = str(y) if e == dt.date(y + 1, 1, 1) else f"{y} (do {e:%m})"
            out.append((s, e, label, True))
            y += 1
    m = max(monthly_from, month_start(first_review))
    while m < end:
        e = min(add_months(m, 1), end)
        out.append((max(m, first_review), e, f"{m:%Y-%m}", e <= today))
        m = add_months(m, 1)
    return out


def build(appid, languages, first_review, today, split_purchase=True):
    rows = []
    for s, e, label, closed in periods(first_review, today):
        a = steam.summary(appid, languages, s, e, "all", closed)
        row = {"start": s.isoformat(), "end": e.isoformat(), "label": label,
               "yearly": len(label) <= 4 or "(" in label, **a}
        if split_purchase and not row["yearly"]:
            st = steam.summary(appid, languages, s, e, "steam", closed)
            row["steam_positive"], row["steam_negative"] = st["positive"], st["negative"]
        rows.append(row)
    return rows
