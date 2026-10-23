"""Single-file HTML report: SVG charts drawn here (no external libraries), the model summary with links
to cited reviews, numbers and method notes."""

import datetime as dt
import html
import json
import math
import re

W, H_LINE, H_BARS = 760, 260, 110
PAD_L, PAD_R, PAD_T, PAD_B = 44, 12, 26, 26


def esc(s):
    return html.escape(str(s), quote=True)


def wilson(pos, n, z=1.96):
    if n == 0:
        return 0.0, 1.0
    p = pos / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


# --- Markdown (the subset models return) ------------------------------------------------

def _inline(text, cite):
    t = esc(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<em>\1</em>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return re.sub(r"\[((?:#\d+\s*,?\s*)+)\]", lambda m: cite(m.group(1)), t)


def markdown(md, cite):
    out, para, lists = [], [], []

    def flush():
        if para:
            out.append(f"<p>{_inline(' '.join(para), cite)}</p>")
            para.clear()

    def close_lists(level=0):
        while len(lists) > level:
            out.append(f"</{lists.pop()}>")

    for raw in md.splitlines():
        line = raw.rstrip()
        m_head = re.match(r"^(#{1,4})\s+(.*)", line)
        m_item = re.match(r"^(\s*)([-*•]|\d+[.)])\s+(.*)", line)
        if not line.strip():
            flush()
            continue
        if m_head:
            flush(); close_lists()
            lvl = min(len(m_head.group(1)) + 1, 4)
            out.append(f"<h{lvl}>{_inline(m_head.group(2), cite)}</h{lvl}>")
        elif m_item:
            flush()
            level = len(m_item.group(1).replace("\t", "  ")) // 2 + 1
            tag = "ol" if m_item.group(2)[0].isdigit() else "ul"
            if len(lists) < level:
                while len(lists) < level:
                    out.append(f"<{tag}>")
                    lists.append(tag)
            else:
                close_lists(level)
                if lists[-1] != tag:
                    out.append(f"</{lists.pop()}><{tag}>")
                    lists.append(tag)
            out.append(f"<li>{_inline(m_item.group(3), cite)}</li>")
        elif line.lstrip().startswith(">"):
            flush(); close_lists()
            out.append(f"<blockquote>{_inline(line.lstrip()[1:].strip(), cite)}</blockquote>")
        else:
            if lists and raw.startswith("  "):
                out[-1] = out[-1][:-5] + " " + _inline(line.strip(), cite) + "</li>"
                continue
            close_lists()
            para.append(line.strip())
    flush(); close_lists()
    return "\n".join(out)


# --- Charts --------------------------------------------------------------------------------

MIN_N = 30  # below this a group's percentage is noise, so it is not drawn
SERIES = (("steam", "s1", "Steam"), ("keys", "s2", "klucze"))  # short names for the line-end labels


def _runs(idx):
    """Split a list of indices into runs of consecutive ones (gaps break lines and bands)."""
    runs, cur = [], []
    for i in idx:
        if cur and i != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(i)
    if cur:
        runs.append(cur)
    return runs


def _line_chart(months, window_start, announcements):
    n = len(months)
    pad_r = 96  # room for direct labels at the line ends
    iw, ih = W - PAD_L - pad_r, H_LINE - PAD_T - PAD_B
    step = iw / n
    xs = [PAD_L + step * (i + 0.5) for i in range(n)]
    vals = []
    for key, _, _ in SERIES:
        for m in months:
            if m[key]["pct"] is not None:
                vals += [m[key]["lo"], m[key]["hi"]]
    if not vals:
        return ""
    lo = max(0, math.floor(min(vals) / 10) * 10)
    hi = min(100, math.ceil(max(vals) / 10) * 10)
    if hi - lo < 20:
        lo = max(0, hi - 20)

    def y(v):
        return PAD_T + (hi - v) / (hi - lo) * ih

    parts = [f'<svg viewBox="0 0 {W} {H_LINE}" role="img" class="chart" id="chart-line" '
             f'aria-label="Odsetek pozytywnych recenzji w kolejnych miesiącach, osobno dla zakupów na Steamie i kluczy">']
    wi = next((i for i, m in enumerate(months) if m["end"] > window_start), None)
    if wi:  # shade only when the window starts inside the chart
        x0 = PAD_L + step * wi
        parts.append(f'<rect x="{x0:.1f}" y="{PAD_T}" width="{PAD_L + iw - x0:.1f}" height="{ih}" class="window"/>')
        parts.append(f'<text x="{x0 + 4:.1f}" y="{PAD_T + 12}" class="note">okno analizy</text>')
    for v in range(lo, hi + 1, 10):
        parts.append(f'<line x1="{PAD_L}" x2="{PAD_L + iw}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>')
        parts.append(f'<text x="{PAD_L - 6}" y="{y(v) + 4:.1f}" class="axis" text-anchor="end">{v}%</text>')
    ends = []
    for key, cls, name in SERIES:
        idx = [i for i in range(n) if months[i][key]["pct"] is not None]
        for run in _runs(idx):
            top = " ".join(f"{xs[i]:.1f},{y(months[i][key]['hi']):.1f}" for i in run)
            bot = " ".join(f"{xs[i]:.1f},{y(months[i][key]['lo']):.1f}" for i in reversed(run))
            parts.append(f'<polygon points="{top} {bot}" class="band {cls}"/>')
        for run in _runs(idx):
            d = " ".join(f"{'M' if j == 0 else 'L'}{xs[i]:.1f},{y(months[i][key]['pct']):.1f}" for j, i in enumerate(run))
            parts.append(f'<path d="{d}" class="line {cls}"/>')
        for i in idx:
            parts.append(f'<circle cx="{xs[i]:.1f}" cy="{y(months[i][key]["pct"]):.1f}" r="4" class="dot {cls}"/>')
        if idx:
            ends.append([y(months[idx[-1]][key]["pct"]), xs[idx[-1]], f"{name} {months[idx[-1]][key]['pct']}%"])
    # Direct labels at the last point of each line, pushed apart if they would overlap.
    if len(ends) == 2 and abs(ends[0][0] - ends[1][0]) < 14:
        a, b = sorted(ends, key=lambda e: e[0])
        mid = (a[0] + b[0]) / 2
        a[0], b[0] = mid - 7, mid + 7
    for ly, lx, text in ends:
        parts.append(f'<text x="{lx + 9:.1f}" y="{ly + 4:.1f}" class="endlabel">{esc(text)}</text>')
    # Developer announcements: small neutral ticks; their titles are listed in the month tooltip.
    for a in announcements:
        for i, m in enumerate(months):
            if m["start"] <= a["date"] < m["end"]:
                span = (dt.date.fromisoformat(m["end"]) - dt.date.fromisoformat(m["start"])).days or 1
                frac = (dt.date.fromisoformat(a["date"]) - dt.date.fromisoformat(m["start"])).days / span
                x = PAD_L + step * (i + frac)
                parts.append(f'<path d="M{x - 3:.1f},{PAD_T - 12} L{x + 3:.1f},{PAD_T - 12} L{x:.1f},{PAD_T - 6} Z" class="annmark"/>')
    parts.append(_xaxis(months, xs, H_LINE - 8))
    parts.append(f'<line x1="{PAD_L}" x2="{PAD_L}" y1="{PAD_T}" y2="{PAD_T + ih}" class="hover-line" visibility="hidden"/>')
    parts.append(f'<rect x="{PAD_L}" y="{PAD_T - 14}" width="{iw}" height="{ih + 14}" fill="transparent" class="hit" '
                 f'data-step="{step:.3f}" data-left="{PAD_L}"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def _bars_chart(months):
    n = len(months)
    pad_r = 96
    iw, ih = W - PAD_L - pad_r, H_BARS - 12 - PAD_B
    step = iw / n
    top = max(m["n"] for m in months) or 1
    nice = 10 ** math.floor(math.log10(top))
    top = math.ceil(top / nice) * nice
    parts = [f'<svg viewBox="0 0 {W} {H_BARS}" role="img" class="chart" id="chart-bars" '
             f'aria-label="Liczba recenzji w kolejnych miesiącach, z podziałem na zakupy na Steamie i klucze">']
    for v in (0, top / 2, top):
        yy = 12 + ih - v / top * ih
        parts.append(f'<line x1="{PAD_L}" x2="{PAD_L + iw}" y1="{yy:.1f}" y2="{yy:.1f}" class="grid"/>')
        parts.append(f'<text x="{PAD_L - 6}" y="{yy + 4:.1f}" class="axis" text-anchor="end">{_short(v)}</text>')
    bw = max(2.0, step - 4)
    base = 12 + ih
    for i, m in enumerate(months):
        x = PAD_L + step * i + (step - bw) / 2
        h_steam = m["steam"]["n"] / top * ih
        h_keys = m["keys"]["n"] / top * ih
        gap = 2 if h_steam > 0 and h_keys > 2 else 0
        segs = [(base, h_steam, "s1"), (base - h_steam - gap, max(0.0, h_keys - gap), "s2")]
        topmost = max((k for k, (_, h, _) in enumerate(segs) if h > 0.5), default=None)
        for k, (yb, h, cls) in enumerate(segs):
            if h <= 0.5:
                continue
            r = min(4, bw / 2, h) if k == topmost else 0
            parts.append(f'<path d="M{x:.1f},{yb:.1f} V{yb - h + r:.1f} Q{x:.1f},{yb - h:.1f} {x + r:.1f},{yb - h:.1f} '
                         f'H{x + bw - r:.1f} Q{x + bw:.1f},{yb - h:.1f} {x + bw:.1f},{yb - h + r:.1f} V{yb:.1f} Z" '
                         f'class="bar {cls}"/>')
    parts.append(_xaxis(months, [PAD_L + step * (i + 0.5) for i in range(n)], H_BARS - 8))
    parts.append(f'<line x1="{PAD_L}" x2="{PAD_L}" y1="12" y2="{base}" class="hover-line" visibility="hidden"/>')
    parts.append(f'<rect x="{PAD_L}" y="12" width="{iw}" height="{ih}" fill="transparent" class="hit" '
                 f'data-step="{step:.3f}" data-left="{PAD_L}"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def _short(v):
    return f"{v / 1000:.0f} tys." if v >= 10000 else f"{v:.0f}"


def _xaxis(months, xs, ypos):
    out, last = [], -99
    for i, m in enumerate(months):
        lab = m["label"]
        if lab.endswith("-01") or i == 0:
            text = lab
        elif len(months) <= 14 or i % 2 == 0:
            text = lab[5:]
        else:
            continue
        if xs[i] - last < 34:
            continue
        last = xs[i]
        out.append(f'<text x="{xs[i]:.1f}" y="{ypos}" class="axis" text-anchor="middle">{esc(text)}</text>')
    return "\n".join(out)


# --- Page ----------------------------------------------------------------------------------

CSS = """
:root { color-scheme: light; --surface:#fcfcfb; --surface-2:#f2f1ee; --text:#0b0b0b; --text-2:#52514e; --muted:#8a8984;
  --grid:#e4e3df; --s1:#2a78d6; --s2:#eb6834; --band1:rgba(42,120,214,.16); --band2:rgba(235,104,52,.16);
  --window:rgba(82,81,78,.06); --link:#1c5cab; --accent:#9a4a12; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark; --surface:#1a1a19; --surface-2:#242423;
  --text:#f0efec; --text-2:#c3c2b7; --muted:#8f8e86; --grid:#33332f; --s1:#3987e5; --s2:#d95926;
  --band1:rgba(57,135,229,.22); --band2:rgba(217,89,38,.22); --window:rgba(195,194,183,.06); --link:#86b6ef;
  --accent:#f0b27a; } }
:root[data-theme="dark"] { color-scheme: dark; --surface:#1a1a19; --surface-2:#242423; --text:#f0efec; --text-2:#c3c2b7;
  --muted:#8f8e86; --grid:#33332f; --s1:#3987e5; --s2:#d95926; --band1:rgba(57,135,229,.22); --band2:rgba(217,89,38,.22);
  --window:rgba(195,194,183,.06); --link:#86b6ef; --accent:#f0b27a; }
* { box-sizing: border-box; }
body { margin:0; background:var(--surface); color:var(--text); font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
main { max-width:820px; margin:0 auto; padding:28px 16px 64px; }
h1 { font-size:1.7rem; margin:0 0 4px; } h2 { font-size:1.25rem; margin:2em 0 .5em; } h3 { font-size:1.05rem; }
.sub { color:var(--text-2); margin:0 0 20px; }
a { color:var(--link); }
.chart { width:100%; height:auto; display:block; overflow:visible; }
.chart .grid { stroke:var(--grid); stroke-width:1; } .chart .axis { fill:var(--muted); font-size:11px; }
.chart .note { fill:var(--text-2); font-size:11px; } .chart .window { fill:var(--window); }
.chart .endlabel { fill:var(--text-2); font-size:11px; }
.chart .band.s1 { fill:var(--band1); } .chart .band.s2 { fill:var(--band2); }
.chart .line { fill:none; stroke-width:2; stroke-linejoin:round; } .chart .line.s1 { stroke:var(--s1); } .chart .line.s2 { stroke:var(--s2); }
.chart .dot { stroke:var(--surface); stroke-width:2; } .chart .dot.s1 { fill:var(--s1); } .chart .dot.s2 { fill:var(--s2); }
.chart .bar.s1 { fill:var(--s1); } .chart .bar.s2 { fill:var(--s2); }
.chart .annmark { fill:var(--muted); } .chart .hover-line { stroke:var(--text-2); stroke-width:1; }
.chart-title { font-weight:600; margin:18px 0 2px; }
.figure { position:relative; margin:4px 0 4px; } .caption { color:var(--text-2); font-size:.85rem; margin:2px 0 18px; }
.tip { position:absolute; pointer-events:none; background:var(--surface-2); border:1px solid var(--grid); border-radius:6px;
  padding:7px 10px; font-size:.82rem; line-height:1.45; display:none; width:max-content; max-width:320px;
  box-shadow:0 2px 8px rgba(0,0,0,.12); z-index:2; }
.tip strong { font-size:.95rem; } .tip .row { display:flex; align-items:center; gap:6px; }
.tip .anns { margin-top:5px; padding-top:5px; border-top:1px solid var(--grid); color:var(--text-2); }
.legend { display:flex; gap:16px; flex-wrap:wrap; font-size:.82rem; color:var(--text-2); margin:4px 0; }
.key { display:inline-block; width:16px; height:0; border-top:2px solid var(--s1); vertical-align:middle; margin-right:5px; flex:none; }
.key.s2 { border-color:var(--s2); } .key.band { height:10px; border:0; background:var(--band1); }
.key.ann { width:0; height:0; border:0; border-left:4px solid transparent; border-right:4px solid transparent; border-top:6px solid var(--muted); }
.summary h3 { color:var(--s1); font-size:1.1rem; margin:1.6em 0 .5em; padding-bottom:4px; border-bottom:1px solid var(--grid); }
.summary strong { color:var(--accent); } .summary ul { padding-left:1.2em; } .summary li { margin:.7em 0; }
.summary li::marker { color:var(--s1); } blockquote { margin:.6em 0; padding-left:12px; border-left:3px solid var(--grid); color:var(--text-2); }
a.cite { font-size:.78em; text-decoration:none; padding:0 2px; border-radius:3px; background:var(--surface-2); white-space:nowrap; }
table { border-collapse:collapse; width:100%; font-size:.86rem; font-variant-numeric:tabular-nums; }
th, td { padding:5px 8px; border-bottom:1px solid var(--grid); text-align:right; } th:first-child, td:first-child { text-align:left; }
th { color:var(--text-2); font-weight:600; } .scroll { overflow-x:auto; }
.fine { color:var(--muted); font-size:.8rem; margin-top:40px; } .fine p { margin:.4em 0; }
details summary { cursor:pointer; color:var(--text-2); }
"""

JS = """
(function(){
  const data = JSON.parse(document.getElementById('months-data').textContent);
  const tip = document.getElementById('tip'), wrap = document.getElementById('charts');
  const fmt = n => n.toLocaleString('pl-PL');
  function el(tag, cls, text){ const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function row(cls, text){ const r = el('div', 'row'); if (cls) r.append(el('span', 'key ' + cls)); r.append(el('span', null, text)); return r; }
  function fill(d){
    tip.replaceChildren(el('strong', null, d.label));
    tip.append(el('div', null, d.n ? 'wszystkie: ' + d.all.pct + '% pozytywnych z ' + fmt(d.n) : 'brak recenzji'));
    for (const [k, cls, name] of [['steam', 's1', 'kupione na Steamie'], ['keys', 's2', 'aktywowane kluczem']]) {
      const s = d[k];
      tip.append(row(cls, s.pct != null ? name + ': ' + s.pct + '% (' + s.lo + '–' + s.hi + '%), ' + fmt(s.n)
                                       : name + ': ' + fmt(s.n) + ' – za mało, by liczyć odsetek'));
    }
    if (d.anns.length) {
      const box = el('div', 'anns', null);
      box.append(el('div', null, 'Ogłoszenia twórców (' + d.anns.length + '):'));
      for (const a of d.anns.slice(0, 6)) box.append(el('div', null, a));
      if (d.anns.length > 6) box.append(el('div', null, '… i ' + (d.anns.length - 6) + ' więcej (lista pod raportem)'));
      tip.append(box);
    }
  }
  for (const svg of document.querySelectorAll('#charts svg')) {
    const hit = svg.querySelector('.hit'); if (!hit) continue;
    const step = parseFloat(hit.dataset.step), left = parseFloat(hit.dataset.left);
    const lines = document.querySelectorAll('#charts .hover-line');
    function show(ev){
      const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const p = pt.matrixTransform(svg.getScreenCTM().inverse());
      const i = Math.max(0, Math.min(data.length - 1, Math.floor((p.x - left) / step)));
      const x = left + step * (i + 0.5);
      for (const l of lines) { l.setAttribute('x1', x); l.setAttribute('x2', x); l.setAttribute('visibility', 'visible'); }
      fill(data[i]);
      tip.style.display = 'block';
      const wr = wrap.getBoundingClientRect(), sr = svg.getBoundingClientRect();
      const sx = sr.width / svg.viewBox.baseVal.width, px = sr.left - wr.left + x * sx;
      let lx = px + 14; if (lx + tip.offsetWidth > wr.width) lx = px - tip.offsetWidth - 14;
      tip.style.left = Math.max(0, lx) + 'px';
      tip.style.top = (sr.top - wr.top + 8) + 'px';
    }
    hit.addEventListener('pointermove', show);
    hit.addEventListener('pointerleave', () => { tip.style.display = 'none'; for (const l of lines) l.setAttribute('visibility', 'hidden'); });
  }
})();
"""


def build(info, rows, window_start, announcements, summary_md, ids, meta):
    def group(pos, n, min_n=MIN_N):
        lo, hi = wilson(pos, n)
        ok = n >= min_n
        return {"n": n, "pct": round(pos / n * 100) if ok else None,
                "lo": round(lo * 100) if ok else None, "hi": round(hi * 100) if ok else None}

    anns = sorted(announcements, key=lambda a: a["date"])
    months = []
    for r in rows:
        if r.get("yearly"):
            continue
        n = r["positive"] + r["negative"]
        sp, sn = r.get("steam_positive", 0), r.get("steam_positive", 0) + r.get("steam_negative", 0)
        months.append({"label": r["label"], "start": r["start"], "end": r["end"], "n": n,
                       "all": group(r["positive"], n, 1), "steam": group(sp, sn), "keys": group(r["positive"] - sp, n - sn),
                       "anns": [f"{a['date'][8:10]}.{a['date'][5:7]} {a['title']}" for a in anns
                                if r["start"] <= a["date"] < r["end"]]})
    years = [r for r in rows if r.get("yearly") and r["positive"] + r["negative"]]

    appid = info["appid"]

    def cite(group):
        links = []
        for num in re.findall(r"\d+", group):
            p = ids.get(int(num))
            if not p:
                links.append(f"#{num}")
                continue
            url = f"https://steamcommunity.com/profiles/{p['steamid']}/recommended/{appid}/"
            snippet = p["record"].split(" | ", 1)[-1][:280]
            d = dt.datetime.fromtimestamp(p["created"], dt.timezone.utc).date().isoformat()
            title = f"{d} · {'poleca' if p['up'] else 'nie poleca'} · {snippet}"
            links.append(f'<a class="cite" href="{esc(url)}" title="{esc(title)}" target="_blank" rel="noopener">#{num}</a>')
        return "[" + ", ".join(links) + "]"

    summary_html = markdown(summary_md, cite) if summary_md else "<p><em>Bez streszczenia (uruchomiono bez modelu).</em></p>"

    # table of window months
    trs = []
    for r in rows:
        if r.get("yearly") or r["end"] <= window_start:
            continue
        n = r["positive"] + r["negative"]
        if not n:
            continue
        sn = r.get("steam_positive", 0) + r.get("steam_negative", 0)
        kn = n - sn
        kp = r["positive"] - r.get("steam_positive", 0)
        pk = f"{kp / kn * 100:.0f}%" if kn >= MIN_N else "—"
        ps = f"{r['steam_positive'] / sn * 100:.0f}%" if sn >= MIN_N else "—"
        trs.append(f"<tr><td>{esc(r['label'])}</td><td>{n}</td><td>{r['positive'] / n * 100:.0f}%</td>"
                   f"<td>{kn / n * 100:.0f}%</td><td>{ps}</td><td>{pk}</td></tr>")
    split = [r for r in rows if "steam_positive" in r]
    base_n = sum(r["positive"] + r["negative"] for r in split)
    base_keys = base_n - sum(r["steam_positive"] + r["steam_negative"] for r in split)

    wi = next((i for i, m in enumerate(months) if m["end"] > window_start), None)
    window_note = (f"Zacieniowane: okno analizy od {esc(window_start)}." if wi else
                   f"Okno analizy (od {esc(window_start)}) obejmuje cały wykres." if wi == 0 else "")
    hist = ""
    if years:
        cells = "".join(f"<tr><td>{esc(r['label'])}</td><td>{r['positive'] + r['negative']}</td>"
                        f"<td>{r['positive'] / (r['positive'] + r['negative']) * 100:.0f}%</td></tr>" for r in years)
        hist = (f"<details><summary>Wcześniejsze lata ({years[0]['label'][:4]}–{years[-1]['label'][:4]})</summary>"
                f"<div class='scroll'><table><tr><th>Rok</th><th>Recenzje</th><th>Pozytywne</th></tr>{cells}</table></div></details>")

    ann_list = "".join(f"<li>{esc(a['date'])}: <a href='{esc(a['url'])}' target='_blank' rel='noopener'>{esc(a['title'])}</a></li>"
                       for a in sorted(announcements, key=lambda a: a["date"], reverse=True))

    by_period = {}
    for p in ids.values():
        by_period[p["period"]] = by_period.get(p["period"], 0) + 1
    sample_desc = ", ".join(f"{k}: {v}" for k, v in by_period.items())
    u = meta.get("usage", {})
    cost = meta.get("cost")
    store = f"https://store.steampowered.com/app/{appid}/"
    page = f"""<!doctype html>
<html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(info['name'])} – recenzje</title><style>{CSS}</style></head>
<body><main>
<h1>{esc(info['name'])}</h1>
<p class="sub"><a href="{store}" target="_blank" rel="noopener">Strona w sklepie Steam</a> · premiera: {esc(info['release'])} ·
recenzje w językach: {esc(', '.join(meta['languages']))} · stan na {esc(meta['today'])}</p>

<h2>Oceny w czasie</h2>
<p class="caption">Ostatnie {len(months)} miesięcy. {window_note} Najedź na miesiąc, by zobaczyć liczby i ogłoszenia twórców.</p>
<div id="charts" class="figure">
<p class="chart-title">Odsetek pozytywnych recenzji w miesiącu</p>
<div class="legend"><span><span class="key"></span>kupione na Steamie</span><span><span class="key s2"></span>aktywowane kluczem</span>
<span><span class="key band"></span>pasmo: gdzie prawdopodobnie leży prawdziwa wartość (mało recenzji = szersze)</span>
<span><span class="key ann"></span>ogłoszenie twórców</span></div>
{_line_chart(months, window_start, announcements)}
<p class="caption">Punkt nie jest rysowany, gdy grupa ma mniej niż {MIN_N} recenzji w miesiącu.</p>
<p class="chart-title">Liczba recenzji w miesiącu</p>
<div class="legend"><span><span class="key"></span>kupione na Steamie</span><span><span class="key s2"></span>aktywowane kluczem</span></div>
{_bars_chart(months)}
<div class="tip" id="tip"></div>
</div>
{hist}

<h2>Streszczenie opinii</h2>
{('<p class="caption">' + esc(meta['summary_note']) + ' Wykresy i tabela są aktualne, streszczenie pochodzi z zapisanego uruchomienia.</p>') if meta.get('summary_note') else ''}
<div class="summary">{summary_html}</div>

<h2>Liczby z okna analizy</h2>
<p>Liczone ze wszystkich recenzji w wybranych językach, nie z próbki. „Z kluczy” to gry aktywowane kluczem kupionym poza Steamem
albo otrzymanym np. od wydawcy. W ostatnich {len(split)} miesiącach to średnio {base_keys / base_n * 100 if base_n else 0:.0f}% recenzji tej gry, więc to jest punkt odniesienia.</p>
<div class="scroll"><table><tr><th>Miesiąc</th><th>Recenzje</th><th>Pozytywne</th><th>Z kluczy</th><th>Pozytywne: zakup na Steamie</th><th>Pozytywne: klucze</th></tr>
{''.join(trs)}</table></div>
<p class="caption">„—”: mniej niż 30 recenzji w grupie, odsetek byłby przypadkowy.</p>

{('<h2>Ogłoszenia twórców</h2><ul>' + ann_list + '</ul>') if ann_list else ''}

<div class="fine">
<p><strong>Jak to policzono.</strong> Wykresy i tabele: wszystkie recenzje Steama w wybranych językach (z recenzjami spoza tematu gry, np. akcjami masowego zaniżania ocen).
Streszczenie: model {esc(meta['model'])} przeczytał {len(ids)} recenzji ({esc(sample_desc)}). W każdym miesiącu okna połowa to najbardziej pomocne, połowa najnowsze;
strona mniejszościowa (pozytywne albo negatywne) ma co najmniej ćwierć miejsca, więc proporcje w próbce są celowo wyrównane.
Przed oknem: najbardziej pomocne recenzje z całej wcześniejszej historii. Recenzje krótsze niż {meta['min_chars']} znaków i powtórzone kopie pominięto, bardzo długie skrócono.</p>
<p>Wejście: {u.get('input', '?')} tokenów, wyjście: {u.get('output', '?')} tokenów, {u.get('seconds', '?')} s{(', koszt ok. $' + format(cost, '.3f')) if cost is not None else ''}.
Odnośniki [#n] prowadzą do recenzji na Steamie; po najechaniu widać jej początek.</p>
</div>
</main>
<script type="application/json" id="months-data">{json.dumps(months, ensure_ascii=False).replace('</', '<\\/')}</script>
<script>{JS}</script>
</body></html>"""
    return page
