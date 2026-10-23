"""Choosing reviews for the model within a fixed token budget.

- The window gets 75% of the budget, earlier history 25%; whatever the window cannot use
  (e.g. only short reviews) goes to the history.
- Each window month gets a share proportional to the square root of its review count, so a launch
  month does not eat everything and quiet months stay visible.
- Earlier history: most helpful reviews of each year (or each month if it is under a year).
- Within a period: half most helpful, half newest. The minority side (positive or negative) gets at
  least MIN_SIDE of the space; the model is told the real proportions separately.
- If everything fits, nothing is sampled.
"""

import hashlib
import math
import re

from . import steam

MIN_SIDE = 0.25
MAX_REVIEW_CHARS = 3000
MIN_REVIEW_CHARS = 30
# Measured 2026-10-22: the Crimson Desert prompt (763k chars) = 203k Gemini 3.8 Flash tokens, i.e. 3.76.
# Slightly conservative for other tokenizers; each model is calibrated after its first call.
DEFAULT_CHARS_PER_TOKEN = 3.5
EST_TOKENS_PER_REVIEW = 200  # only for planning how many pages to fetch

_BBCODE = re.compile(r"\[/?(?:h[1-6]|b|i|u|s|strike|spoiler|noparse|hr|list|olist|\*|quote|url|code|table|tr|th|td|img|previewyoutube)(?:=[^\]]*)?\]", re.I)
_UNCHECKED = re.compile(r"[☐□⬜]")
_CHECKED = re.compile(r"[☑☒✅✔✓]|\[x\]", re.I)
_REPEAT = re.compile(r"(.)\1{3,}")


def clean(text):
    lines = []
    for line in _BBCODE.sub(" ", text).splitlines():
        # "☐ Bad ☑ Good" templates: unchecked lines are noise.
        if _UNCHECKED.search(line) and not _CHECKED.search(line):
            continue
        line = _REPEAT.sub(r"\1\1\1", line)
        line = " ".join(line.split())
        if line:
            lines.append(line)
    out = " / ".join(lines)
    if len(out) > MAX_REVIEW_CHARS:
        out = out[:MAX_REVIEW_CHARS].rsplit(" ", 1)[0] + " […]"
    return out


def record_text(r, clean_text):
    hours = (r.get("playtime_at_review") or 0) / 60
    flags = "".join(f for f, on in (("K", not r["steam_purchase"]), ("F", r["free"]), ("EA", r["early_access"])) if on)
    head = f"{'+' if r['up'] else '-'} {hours:.0f}h {r['helpful']}↑"
    return f"{head}{' ' + flags if flags else ''} | {clean_text}"


class Budget:
    def __init__(self, chars_per_token):
        self.cpt = chars_per_token

    def tokens(self, s):
        return math.ceil(len(s) / self.cpt) + 12  # + record header (#id, date)


def _sqrt_alloc(total, weights, caps):
    """Split `total` proportionally to weights without exceeding `caps`; redistribute the surplus."""
    alloc = [0.0] * len(weights)
    open_ = [i for i, w in enumerate(weights) if w > 0 and caps[i] > 0]
    left = total
    while open_ and left > 1:
        wsum = sum(weights[i] for i in open_)
        nxt = []
        spent = 0.0
        for i in open_:
            give = left * weights[i] / wsum
            room = caps[i] - alloc[i]
            g = min(give, room)
            alloc[i] += g
            spent += g
            if alloc[i] < caps[i] - 1e-6:
                nxt.append(i)
        left -= spent
        if not nxt or spent < 1e-6:
            break
        open_ = nxt
    return alloc


def plan(timeline, window_start, budget_tokens, window_share=0.75):
    """Token budget per window period. timeline: rows from timeline.build()."""
    rows = []
    for t in timeline:
        n = t["positive"] + t["negative"]
        rows.append({**t, "n": n, "in_window": t["end"] > window_start, "cap": n * EST_TOKENS_PER_REVIEW})
    win = [r for r in rows if r["in_window"]]
    before = [r for r in rows if not r["in_window"]]
    before_cap = sum(r["cap"] for r in before)
    win_cap = sum(r["cap"] for r in win)
    win_budget = budget_tokens * (window_share if before_cap else 1.0)
    win_budget = min(win_budget, win_cap)
    before_budget = min(budget_tokens - win_budget, before_cap)
    # Give the history what the window cannot use, and vice versa.
    win_budget = min(budget_tokens - before_budget, win_cap)
    alloc = _sqrt_alloc(win_budget, [math.sqrt(r["n"]) for r in win], [r["cap"] for r in win])
    for r, a in zip(win, alloc):
        r["budget"] = a
    for r in before:
        r["budget"] = 0
    return rows, before_budget


def _side_targets(pos, neg, budget):
    n = pos + neg
    if n == 0:
        return 0, 0
    neg_share = neg / n
    if pos and neg:
        neg_share = min(max(neg_share, MIN_SIDE), 1 - MIN_SIDE)
    return budget * (1 - neg_share), budget * neg_share


class Sampler:
    def __init__(self, appid, languages, budget, today):
        self.appid, self.languages, self.b, self.today = appid, languages, budget, today
        self.seen_ids, self.seen_text = set(), set()

    def _take(self, candidates, caps, used, label, total=None):
        """Pick candidates while their side (positive/negative) has room in `caps`
        (and the sum fits `total`, if given). Updates `used`."""
        out = []
        for r in candidates:
            if total is not None and sum(used.values()) >= total:
                break
            side = "positive" if r["up"] else "negative"
            if caps.get(side, 0) - used[side] < 1 or r["id"] in self.seen_ids:
                continue
            c = clean(r["text"])
            if len(c) < MIN_REVIEW_CHARS:
                continue
            h = hashlib.sha1(c.lower().encode()).hexdigest()
            if h in self.seen_text:
                continue
            rec = record_text(r, c)
            t = self.b.tokens(rec)
            if used[side] + t > caps[side]:
                continue  # too long for the remaining room; a shorter one may fit
            self.seen_ids.add(r["id"])
            self.seen_text.add(h)
            out.append({**r, "period": label, "record": rec, "tokens": t})
            used[side] += t
            if all(caps[k] - used[k] < 0.05 * max(caps[k], 1) for k in caps):
                break
        return out

    def _fill(self, s, e, order, review_type, caps, used, label, available, closed, total=None):
        """Pick from a Steam list, fetching more pages until `caps` are full or the list ends."""
        want = sum(caps[k] - used[k] for k in caps)
        limit = min(available, int(want / EST_TOKENS_PER_REVIEW * 1.5) + 20)
        limit = max(100, math.ceil(limit / 100) * 100)  # whole pages
        first_limit, out = limit, []
        while True:
            cands = steam.reviews(self.appid, self.languages, s, e, order, limit, review_type, closed)
            out += self._take(cands, caps, used, label, total)
            left = sum(max(0.0, caps[k] - used[k]) for k in caps)
            if total is not None:
                left = min(left, total - sum(used.values()))
            if left < 0.1 * want or len(cands) < limit or limit >= available or limit >= 8 * first_limit:
                return out
            limit *= 2

    def period(self, s, e, label, pos, neg, budget, closed, orders=("helpful", "recent")):
        if (pos + neg) * EST_TOKENS_PER_REVIEW <= budget:
            # Everything fits: one list instead of four, no rebalancing.
            used = {"positive": 0.0, "negative": 0.0}
            cands = steam.reviews(self.appid, self.languages, s, e, "recent", pos + neg, "all", closed)
            return self._take(cands, {"positive": budget, "negative": budget}, used, label, total=budget)
        t_pos, t_neg = _side_targets(pos, neg, budget)
        target = {"positive": t_pos, "negative": t_neg}
        avail = {"positive": pos, "negative": neg}
        used = {"positive": 0.0, "negative": 0.0}
        picked = []
        # 1. Mixed lists (both sides at once), equal share from each order.
        for i, order in enumerate(orders):
            caps = {k: used[k] + (target[k] - used[k]) / (len(orders) - i) for k in target}
            want = sum(caps[k] - used[k] for k in caps)
            if want < 1:
                break
            picked += self._fill(s, e, order, "all", caps, used, label, pos + neg, closed)
        # 2. Top up the side that fell short (usually the minority) from a list of only that side.
        #    Whatever still cannot be filled goes to the other side.
        for side in sorted(target, key=lambda k: avail[k]):
            other = "negative" if side == "positive" else "positive"
            for order in orders:
                left = target[side] - used[side]
                if left < 0.1 * max(target[side], 1) or avail[side] == 0:
                    break
                picked += self._fill(s, e, order, side, {side: target[side]}, used, label, avail[side], closed)
            spare = target[side] - used[side]
            if spare > 1:
                target[side] = used[side]
                target[other] += spare
        return picked


def _before_periods(rows, window_start):
    """History before the window as months if it spans at most 12 of them, otherwise calendar years.
    Returns (start, end, label, positive, negative)."""
    import datetime as dt
    before = [r for r in rows if not r["in_window"]]
    monthly = len(before) <= 12 and not any(r.get("yearly") for r in before)
    years = {}
    for r in before:
        y = r["label"] if monthly else r["start"][:4]
        cur = years.setdefault(y, [r["start"], r["end"], 0, 0])
        cur[1] = min(r["end"], window_start)
        cur[2] += r["positive"]
        cur[3] += r["negative"]
    return [(dt.date.fromisoformat(a), dt.date.fromisoformat(b), y, p, n) for y, (a, b, p, n) in years.items()]


def sample(appid, languages, timeline, window_start, budget_tokens, chars_per_token, today):
    """Window first, then history, which gets whatever the window left unused."""
    import datetime as dt
    rows, before_budget = plan(timeline, window_start, budget_tokens)
    sm = Sampler(appid, languages, Budget(chars_per_token), today)
    window = []
    for r in (r for r in rows if r["in_window"]):
        s = max(dt.date.fromisoformat(r["start"]), dt.date.fromisoformat(window_start))
        e = dt.date.fromisoformat(r["end"])
        window += sm.period(s, e, r["label"], r["positive"], r["negative"], r["budget"], e <= today)
    before = []
    years = _before_periods(rows, window_start)
    before_budget = max(before_budget, budget_tokens - sum(p["tokens"] for p in window))
    if years and before_budget > 0:
        # 'helpful' with a date range yields at most ~100 reviews per type, so cap each period at ~200.
        caps = [min((p + n) * EST_TOKENS_PER_REVIEW, 200 * EST_TOKENS_PER_REVIEW) for *_, p, n in years]
        alloc = _sqrt_alloc(before_budget, [math.sqrt(p + n) for *_, p, n in years], caps)
        for (s, e, y, p, n), a in zip(years, alloc):
            if a >= 1:
                before += sm.period(s, e, f"przed oknem: {y}", p, n, a, True, orders=("helpful",))
    return rows, before + window
