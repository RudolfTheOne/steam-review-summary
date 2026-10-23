"""Steam data: game info, review counts in date windows, reviews, official announcements.
Standard library only."""

import datetime as dt
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import cache

STORE = "https://store.steampowered.com"
UA = "Mozilla/5.0 (steamsum; +local script)"
MIN_GAP = 0.6  # seconds between requests; ~250 requests within a few minutes trigger a 1–2 min block
RATE_WAITS = (30, 45, 60, 90, 120)  # seconds to wait after consecutive 429 responses

_last_request = 0.0
requests_made = 0  # for stats


def parse_appid(text):
    """App id from a store link, a Steam Review Explorer link or a bare number."""
    text = text.strip()
    if text.isdigit():
        return int(text)
    m = re.search(r"/(?:app|game)/(\d+)", text)
    if m:
        return int(m.group(1))
    raise ValueError(f"Nie rozpoznaję numeru gry w: {text}")


def _get_json(url, tries=6):
    global _last_request, requests_made
    for attempt in range(tries):
        wait = _last_request + MIN_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        requests_made += 1
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries - 1:
                w = RATE_WAITS[min(attempt, len(RATE_WAITS) - 1)]
                print(f"  Steam ogranicza tempo (429), czekam {w} s…", file=sys.stderr, flush=True)
                time.sleep(w)
                continue
            if e.code in (500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt < tries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise


def _ts(d):
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())


def app_info(appid):
    key = f"app/{appid}"
    hit = cache.get(key, max_age=7 * 86400)
    if hit:
        return hit
    data = _get_json(f"{STORE}/api/appdetails?appids={appid}&filters=basic,release_date")
    entry = data.get(str(appid)) or {}
    if not entry.get("success"):
        raise ValueError(f"Steam nie zna gry {appid}")
    d = entry["data"]
    # The histogram gives the date of the first review in one request, however old the game is.
    hist = _get_json(f"{STORE}/appreviewhistogram/{appid}?l=english&review_score_preference=0")
    start = hist.get("results", {}).get("start_date")
    info = {
        "appid": appid,
        "name": d.get("name", str(appid)),
        "release": (d.get("release_date") or {}).get("date", ""),
        "reviews_start": dt.datetime.fromtimestamp(start, dt.timezone.utc).date().isoformat() if start else None,
    }
    cache.put(key, info)
    return info


def _review_params(languages, start, end, **extra):
    p = {"json": 1, "language": ",".join(languages), "purchase_type": "all", "filter_offtopic_activity": 0}
    if start or end:
        p.update(start_date=_ts(start), end_date=_ts(end), date_range_type="include")
    p.update(extra)
    return p


def summary(appid, languages, start, end, purchase_type="all", closed=True):
    """Positive/negative review counts in [start, end). Closed windows are cached forever."""
    key = f"sum/{appid}/{','.join(languages)}/{purchase_type}/{start}/{end}"
    hit = cache.get(key, max_age=None if closed else 6 * 3600)
    if hit:
        return hit
    p = _review_params(languages, start, end, num_per_page=0, cursor="*", filter="all", purchase_type=purchase_type)
    q = _get_json(f"{STORE}/appreviews/{appid}?" + urllib.parse.urlencode(p))["query_summary"]
    out = {"positive": q.get("total_positive", 0), "negative": q.get("total_negative", 0)}
    cache.put(key, out)
    return out


def reviews(appid, languages, start, end, order, limit, review_type="all", closed=True):
    """Up to `limit` reviews from [start, end), order 'helpful' or 'recent', review_type 'positive'/'negative'/'all'.
    The cached list grows: a larger limit only fetches the missing pages.
    Steam quirks: 'helpful' with a date range returns at most 100 reviews (page 2 repeats page 1);
    'helpful' without dates only covers the last year."""
    key = f"rev2/{appid}/{','.join(languages)}/{order}/{review_type}/{start}/{end}"
    st = cache.get(key, max_age=None if closed else 6 * 3600) or {"items": [], "cursor": "*", "done": False}
    if len(st["items"]) >= limit or st["done"]:
        return st["items"][:limit]
    seen = {r["id"] for r in st["items"]}
    while len(st["items"]) < limit:
        p = _review_params(languages, start, end, num_per_page=100, cursor=st["cursor"],
                           filter="all" if order == "helpful" else "recent", review_type=review_type)
        d = _get_json(f"{STORE}/appreviews/{appid}?" + urllib.parse.urlencode(p))
        new = [r for r in d.get("reviews") or [] if r["recommendationid"] not in seen]
        for r in new:
            seen.add(r["recommendationid"])
            st["items"].append(_slim(r))
        st["cursor"] = d.get("cursor")
        if not new or not st["cursor"]:
            st["done"] = True
            break
    cache.put(key, st)
    return st["items"][:limit]


def _slim(r):
    a = r.get("author") or {}
    return {
        "id": r["recommendationid"],
        "steamid": a.get("steamid"),
        "lang": r.get("language"),
        "created": r.get("timestamp_created"),
        "updated": r.get("timestamp_updated"),
        "up": bool(r.get("voted_up")),
        "helpful": r.get("votes_up", 0),
        "funny": r.get("votes_funny", 0),
        "steam_purchase": bool(r.get("steam_purchase")),
        "free": bool(r.get("received_for_free")),
        "early_access": bool(r.get("written_during_early_access")),
        "playtime_at_review": a.get("playtime_at_review"),  # minutes
        "playtime_forever": a.get("playtime_forever"),
        "author_reviews": a.get("num_reviews"),
        "author_games": a.get("num_games_owned"),
        "text": r.get("review") or "",
    }


def news(appid, since):
    """Official developer announcements (patch notes) since `since`."""
    key = f"news/{appid}/{since}"
    hit = cache.get(key, max_age=6 * 3600)
    if hit:
        return hit
    url = ("https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?" + urllib.parse.urlencode(
        {"appid": appid, "count": 200, "maxlength": 400, "feeds": "steam_community_announcements",
         "enddate": int(time.time())}))
    items = _get_json(url).get("appnews", {}).get("newsitems", [])
    since_ts = _ts(since)
    out = [{"date": dt.datetime.fromtimestamp(i["date"], dt.timezone.utc).date().isoformat(),
            "title": i.get("title", ""), "url": i.get("url", ""), "excerpt": i.get("contents", "")}
           for i in items if i.get("date", 0) >= since_ts]
    cache.put(key, out)
    return out
