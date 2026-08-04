#!/usr/bin/env python3
"""Daily trading-bot report → a self-contained HTML dashboard.

Reads the runtime state (trading_bot.db, logs/waxui_shadow.jsonl,
trading_bot.log) and writes reports/daily_<CT-date>.html plus a stable
reports/latest.html you can bookmark and open each day.

READ-ONLY. Touches no parse or execution logic — safe to run any time,
including during the Gate-1 freeze.

Usage:
    ./venv/bin/python daily_report.py                 # today (America/Chicago)
    ./venv/bin/python daily_report.py --date 2026-07-24
    ./venv/bin/python daily_report.py --open          # also open in browser
    ./venv/bin/python daily_report.py --days 14       # trend strip length
"""

import argparse
import calendar as calmod
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "trading_bot.db")
SHADOW_LOG = os.path.join(HERE, "logs", "waxui_shadow.jsonl")
BOT_LOG = os.path.join(HERE, "trading_bot.log")
REPORT_DIR = os.path.join(HERE, "reports")
CT = ZoneInfo("America/Chicago")

# Current paper run (post-restart). The All-time tab is scoped to this so the
# track record reflects the validated Eva/Ace/Waxui era, not the abandoned
# pre-restart experiments still sitting in the DB.
ERA_START = "2026-07-15"

# ── time helpers ───────────────────────────────────────────────────

def parse_ts(s):
    """Parse a DB timestamp into an aware UTC datetime. Handles both
    'YYYY-MM-DD HH:MM:SS[.ffffff]' (naive → treated as UTC) and ISO strings
    with an explicit offset."""
    if not s:
        return None
    s = str(s).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ct_date(s):
    """CT calendar date (YYYY-MM-DD) for a DB timestamp, or None."""
    dt = parse_ts(s)
    return dt.astimezone(CT).strftime("%Y-%m-%d") if dt else None


def ct_hm(s):
    dt = parse_ts(s)
    return dt.astimezone(CT).strftime("%H:%M") if dt else "—"


# ── data loading ───────────────────────────────────────────────────

def channel_map():
    """channel_id → (analyst, mode) using the live config as source of truth."""
    sys.path.insert(0, HERE)
    from config import Config
    cfg = Config()
    out = {}
    for ch, analyst in cfg._discord_channel_analyst_pairs:
        if not ch:
            continue
        if cfg.is_shadow_analyst(analyst):
            mode = "shadow"
        elif cfg._analyst_enabled(analyst):
            mode = "execute"
        else:
            mode = "off"
        out[ch] = (analyst, mode)
    return out


def load_day(conn, day):
    """Everything relevant to one CT trading day."""
    trades, positions_closed, messages = [], [], []

    for r in conn.execute("SELECT * FROM trades ORDER BY id"):
        r = dict(r)
        if ct_date(r["executed_at"]) == day or ct_date(r["created_at"]) == day:
            trades.append(r)

    for r in conn.execute("SELECT * FROM positions ORDER BY id"):
        r = dict(r)
        if ct_date(r["closed_at"]) == day or ct_date(r["opened_at"]) == day:
            positions_closed.append(r)

    open_positions = [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE status='open' ORDER BY id")]

    for r in conn.execute("SELECT * FROM message_log ORDER BY processed_at"):
        r = dict(r)
        if ct_date(r["processed_at"]) == day:
            messages.append(r)

    return trades, positions_closed, open_positions, messages


def _iter_shadow():
    """Yield real (non-replay) shadow observations from the jsonl log."""
    if not os.path.exists(SHADOW_LOG):
        return
    for line in open(SHADOW_LOG, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(d.get("message_id", "")).startswith("replay"):
            continue
        yield d


def load_shadow(day):
    return [d for d in _iter_shadow() if ct_date(d.get("observed_at")) == day]


def load_shadow_all():
    return list(_iter_shadow())


def _load_cache(filename):
    try:
        with open(os.path.join(REPORT_DIR, filename), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_recon():
    """Alpaca fill reconciliation cache (written by reconcile_fills.py), or None."""
    return _load_cache("fills_cache.json")


def load_shadow_pnl():
    """Waxui hypothetical-P&L cache (written by shadow_pnl.py), or None."""
    return _load_cache("shadow_pnl_cache.json")


def real_pnl_cell(recon, position_id):
    """(real_pnl_html, delta_html) for a closed position, or ('—','—')."""
    if not recon:
        return "<span class=tag>run reconcile</span>", "—"
    pr = recon.get("position_real", {}).get(str(position_id))
    if not pr or not pr.get("matched"):
        return "<span class=tag>unmatched</span>", "—"
    return money(pr["real_pnl"]), money(pr["delta"])


LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d+ \[(\w+)\] ([^:]+): (.*)$")

def load_log_all():
    """All ERROR/WARNING lines grouped by CT day (log is already CT)."""
    by_day = {}
    if not os.path.exists(BOT_LOG):
        return by_day
    for line in open(BOT_LOG, encoding="utf-8", errors="replace"):
        m = LOG_RE.match(line)
        if not m:
            continue
        d, t, level, logger, msg = m.groups()
        if level not in ("ERROR", "WARNING"):
            continue
        parse_related = bool(re.search(r"pars|router|gemini|signal", logger + msg, re.I))
        by_day.setdefault(d, []).append({"time": t, "level": level,
                                         "logger": logger.strip(), "msg": msg,
                                         "parse_related": parse_related})
    return by_day


def load_log_events(day):
    return load_log_all().get(day, [])


def trend(conn, days_back, end_day):
    """Per-day objective stats for the trailing strip."""
    end = datetime.strptime(end_day, "%Y-%m-%d").date()
    day_list = [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_back)][::-1]
    stats = {d: {"trades": 0, "pnl": 0.0, "closed": 0, "signals": 0} for d in day_list}
    idx = set(day_list)

    for r in conn.execute("SELECT executed_at, created_at, pnl, status FROM trades"):
        d = ct_date(r["executed_at"]) or ct_date(r["created_at"])
        if d in idx:
            stats[d]["trades"] += 1
            if r["pnl"] is not None:
                stats[d]["pnl"] += r["pnl"]
    for r in conn.execute("SELECT closed_at FROM positions WHERE status='closed'"):
        d = ct_date(r["closed_at"])
        if d in idx:
            stats[d]["closed"] += 1
    for r in conn.execute("SELECT processed_at, parsed_as FROM message_log"):
        d = ct_date(r["processed_at"])
        if d not in idx:
            continue
        pa = r["parsed_as"]
        if pa and '"action"' in pa:      # a real parsed live signal
            stats[d]["signals"] += 1
    return day_list, stats


def load_alltime(conn, era_start):
    """Trades, closed lifecycles, and open positions since the era start."""
    trades = [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY id")]
    trades = [t for t in trades
              if (ct_date(t["executed_at"] or t["created_at"]) or "") >= era_start]

    closed = [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE status='closed' ORDER BY closed_at")]
    closed = [p for p in closed if (ct_date(p["closed_at"]) or "") >= era_start]

    open_pos = [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE status='open' ORDER BY id")]

    # daily realized-P&L series (for the equity curve + history table)
    by_day = {}
    for t in trades:
        d = ct_date(t["executed_at"] or t["created_at"])
        if not d:
            continue
        s = by_day.setdefault(d, {"trades": 0, "pnl": 0.0, "closed": 0})
        s["trades"] += 1
        if t["pnl"] is not None:
            s["pnl"] += t["pnl"]
    for p in closed:
        d = ct_date(p["closed_at"])
        if d in by_day:
            by_day[d]["closed"] += 1
        elif d:
            by_day[d] = {"trades": 0, "pnl": 0.0, "closed": 1}
    series = []
    cum = 0.0
    for d in sorted(by_day):
        cum += by_day[d]["pnl"]
        series.append({"date": d, **by_day[d], "cum": cum})

    return trades, closed, open_pos, series


def analyst_breakdown(trades, closed, ch_map):
    """Per-analyst rollup. Shadow analysts show observation counts, not P&L."""
    shadow_analysts = {a for (a, m) in ch_map.values() if m == "shadow"}
    rows = {}
    for t in trades:
        a = t["analyst"]
        r = rows.setdefault(a, {"trades": 0, "pnl": 0.0, "lifecycles": 0,
                                "wins": 0, "losses": 0})
        r["trades"] += 1
        if t["pnl"] is not None:
            r["pnl"] += t["pnl"]
    for p in closed:
        r = rows.setdefault(p["analyst"], {"trades": 0, "pnl": 0.0, "lifecycles": 0,
                                           "wins": 0, "losses": 0})
        r["lifecycles"] += 1
        if (p["total_pnl"] or 0) > 0:
            r["wins"] += 1
        elif (p["total_pnl"] or 0) < 0:
            r["losses"] += 1
    return rows, shadow_analysts


def equity_curve_svg(series):
    """Inline SVG line of cumulative realized P&L. Empty-safe."""
    pts = [(s["date"], s["cum"]) for s in series]
    if len(pts) < 1:
        return "<div class=empty>No realized P&amp;L yet.</div>"
    W, H, PAD = 720, 200, 28
    vals = [v for _, v in pts]
    lo, hi = min(vals + [0.0]), max(vals + [0.0])
    span = (hi - lo) or 1.0
    n = len(pts)

    def x(i):
        return PAD + (i * (W - 2 * PAD) / (n - 1)) if n > 1 else W / 2

    def y(v):
        return H - PAD - (v - lo) * (H - 2 * PAD) / span

    zero_y = y(0.0)
    line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(pts))
    dots = "".join(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3" class="ec-dot"/>'
                   for i, (_, v) in enumerate(pts))
    end_cls = "pos" if vals[-1] > 0 else ("neg" if vals[-1] < 0 else "flat")
    return (
        f'<svg viewBox="0 0 {W} {H}" class="equity" preserveAspectRatio="none" '
        f'role="img" aria-label="Cumulative realized P&L">'
        f'<line x1="{PAD}" y1="{zero_y:.1f}" x2="{W-PAD}" y2="{zero_y:.1f}" class="ec-zero"/>'
        f'<polyline points="{line}" class="ec-line"/>{dots}'
        f'<text x="{PAD}" y="14" class="ec-lbl">{hi:+,.0f}</text>'
        f'<text x="{PAD}" y="{H-8}" class="ec-lbl">{lo:+,.0f}</text>'
        f'<text x="{W-PAD}" y="{y(vals[-1])-8:.1f}" text-anchor="end" '
        f'class="ec-end {end_cls}">{vals[-1]:+,.2f}</text>'
        f'</svg>')


# ── parse-outcome interpretation ───────────────────────────────────

def parse_outcome(parsed_as_raw):
    """Turn a message_log.parsed_as blob into (label, css_class, detail)."""
    if not parsed_as_raw:
        return ("no signal", "muted", "noise / not actionable")
    try:
        pa = json.loads(parsed_as_raw)
    except (json.JSONDecodeError, TypeError):
        return ("?", "muted", str(parsed_as_raw)[:120])
    if not isinstance(pa, dict):
        return ("?", "muted", str(pa)[:120])

    if pa.get("shadow"):
        tier = pa.get("tier", "?")
        pretty = {"regex-extract": "parsed (regex)", "unparsed": "would-hit-Gemini",
                  "noise-skip": "noise"}.get(tier, tier)
        return (pretty, "shadow", f"shadow · tier={tier} · order={'YES' if pa.get('executed') else 'NO'}")
    if "skipped" in pa:
        return (f"skipped: {pa['skipped']}", "warn", json.dumps(pa))
    if "error" in pa:
        return (f"error: {pa['error']}", "err", json.dumps(pa))
    if pa.get("action"):
        conf = pa.get("confidence")
        conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "?"
        detail = pa.get("raw_message") or ""
        return (f"{pa['action']} {pa.get('ticker','')}".strip(), "ok",
                f"conf {conf_s} · {detail}")
    return ("logged", "muted", json.dumps(pa)[:120])


# ── html rendering ─────────────────────────────────────────────────

def esc(x):
    return html.escape("" if x is None else str(x))


def money(v):
    if v is None:
        return "—"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "flat")
    return f'<span class="{cls}">{"+" if v > 0 else ""}{v:,.2f}</span>'


def contract(analyst, ticker, direction, strike, expiry):
    parts = [ticker or "?"]
    if strike:
        rp = {"call": "C", "put": "P"}.get(direction, "")
        parts.append(f"{strike:g}{rp}")
    if expiry:
        parts.append(str(expiry))
    return " ".join(parts)


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1a1d21;--muted:#6b7280;--line:#e5e7eb;
--pos:#0a7d33;--neg:#c0392b;--accent:#2563eb;--shadow-c:#7c3aed;--warn:#b45309;--err:#b91c1c;
--chip:#eef2ff;--head:#f0f2f5;}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--ink:#e6e8eb;
--muted:#9aa4b2;--line:#262c35;--pos:#3fb950;--neg:#f85149;--accent:#4d94ff;--shadow-c:#a978ff;
--warn:#e3a008;--err:#ff6b6b;--chip:#1d2530;--head:#1c2129;}}
:root[data-theme=dark]{--bg:#0f1216;--card:#171b21;--ink:#e6e8eb;--muted:#9aa4b2;--line:#262c35;
--pos:#3fb950;--neg:#f85149;--accent:#4d94ff;--shadow-c:#a978ff;--warn:#e3a008;--err:#ff6b6b;--chip:#1d2530;--head:#1c2129;}
:root[data-theme=light]{--bg:#f6f7f9;--card:#fff;--ink:#1a1d21;--muted:#6b7280;--line:#e5e7eb;
--pos:#0a7d33;--neg:#c0392b;--accent:#2563eb;--shadow-c:#7c3aed;--warn:#b45309;--err:#b91c1c;--chip:#eef2ff;--head:#f0f2f5;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:24px}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:22px;margin:0 0 2px}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
margin:32px 0 10px;font-weight:600}
.sub{color:var(--muted);font-size:13px;margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.card .v{font-size:24px;font-weight:650;margin-top:4px}
.card.big-ok .v{color:var(--pos)} .card.big-err .v{color:var(--err)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:10px;overflow:hidden;font-size:13.5px}
.scroll{overflow-x:auto}
th{background:var(--head);text-align:left;font-size:11.5px;text-transform:uppercase;
letter-spacing:.04em;color:var(--muted);padding:9px 12px;white-space:nowrap}
td{padding:9px 12px;border-top:1px solid var(--line);vertical-align:top}
tr:hover td{background:color-mix(in srgb,var(--head) 45%,transparent)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.pos{color:var(--pos);font-weight:600} .neg{color:var(--neg);font-weight:600} .flat{color:var(--muted)}
.chip{display:inline-block;padding:1px 8px;border-radius:20px;background:var(--chip);
font-size:11.5px;font-weight:600;white-space:nowrap}
.chip.ok{color:var(--pos)} .chip.warn{color:var(--warn)} .chip.err{color:var(--err)}
.chip.shadow{color:var(--shadow-c)} .chip.muted{color:var(--muted)}
.chip.execute{color:var(--accent)}
.tag{font-size:11px;color:var(--muted)}
.detail{color:var(--muted);font-size:12px;max-width:520px;white-space:pre-wrap;word-break:break-word}
.empty{color:var(--muted);background:var(--card);border:1px dashed var(--line);
border-radius:10px;padding:16px;text-align:center;font-size:13.5px}
.trend td,.trend th{text-align:center;white-space:nowrap}
.trend td:first-child,.trend th:first-child{text-align:left}
.today-row td{background:color-mix(in srgb,var(--accent) 10%,transparent);font-weight:600}
.foot{margin-top:36px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:14px}
a{color:var(--accent)}
.tabs{display:flex;gap:6px;margin:16px 0 4px;border-bottom:1px solid var(--line)}
.tab{appearance:none;background:none;border:0;border-bottom:2px solid transparent;color:var(--muted);
font:inherit;font-weight:600;padding:9px 14px;cursor:pointer;font-size:14px}
.tab:hover{color:var(--ink)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.pane{display:none} .pane.active{display:block}
.equity{width:100%;height:auto;display:block;background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:6px}
.ec-line{fill:none;stroke:var(--accent);stroke-width:2;vector-effect:non-scaling-stroke}
.ec-zero{stroke:var(--line);stroke-width:1;stroke-dasharray:4 4;vector-effect:non-scaling-stroke}
.ec-dot{fill:var(--accent)}
.ec-lbl{fill:var(--muted);font-size:11px}
.ec-end{font-size:13px;font-weight:700}
.ec-end.pos{fill:var(--pos)} .ec-end.neg{fill:var(--neg)} .ec-end.flat{fill:var(--muted)}
.cal-month{margin:18px 0}
.cal-month h3{font-size:14px;margin:0 0 6px;color:var(--muted);font-weight:600}
table.cal{table-layout:fixed;min-width:520px}
table.cal th{background:var(--head);text-align:center;padding:6px;font-size:11px}
table.cal td{border:1px solid var(--line);height:66px;vertical-align:top;padding:0}
.cal-empty{background:transparent;border:1px solid transparent!important}
.cal-off .cal-day{color:var(--muted);opacity:.45;padding:6px;display:block;font-size:13px}
.cal-btn{width:100%;min-height:64px;border:0;background:none;cursor:pointer;display:flex;
flex-direction:column;align-items:flex-start;justify-content:space-between;gap:8px;
padding:6px 8px;font:inherit;color:var(--ink);text-align:left}
.cal-btn:hover{background:color-mix(in srgb,var(--accent) 14%,transparent)}
.cal-day{font-size:13px;font-weight:600}
.cal-pnl{font-size:14px;font-weight:700;align-self:flex-end}
.cal-mut{color:var(--muted);font-weight:500;font-size:11px}
.cal-pos{background:color-mix(in srgb,var(--pos) 15%,transparent)}
.cal-pos .cal-pnl{color:var(--pos)}
.cal-neg{background:color-mix(in srgb,var(--neg) 15%,transparent)}
.cal-neg .cal-pnl{color:var(--neg)}
.cal-flat .cal-pnl{color:var(--muted)}
.backbtn{appearance:none;background:var(--chip);border:1px solid var(--line);border-radius:8px;
color:var(--accent);font:inherit;font-weight:600;padding:8px 14px;cursor:pointer;margin-bottom:14px}
.backbtn:hover{border-color:var(--accent)}
.day-detail{border-top:1px solid var(--line);padding-top:6px}
.note{background:color-mix(in srgb,var(--warn) 12%,var(--card));border:1px solid var(--warn);
border-radius:9px;padding:10px 13px;margin:10px 0 4px;font-size:12.5px;color:var(--ink)}
.note b{color:var(--warn)}
"""

# Every P&L in this report is "booked" P&L — computed from the analyst's signal
# price, not the actual Alpaca fill (LIVE_SAFETY.md Blocker 2). It is therefore
# optimistic and not yet fill-verified. Surfaced honestly wherever P&L appears.
PNL_CAVEAT = (
    "<div class=note><b>⚠ Booked P&amp;L, not fill-verified.</b> P&amp;L is computed from each "
    "analyst's <i>signal price</i>, not the actual Alpaca fill (LIVE_SAFETY Blocker 2), so it "
    "runs optimistic. Run <span class=mono>./venv/bin/python reconcile_fills.py</span> to add the "
    "<b>Real</b> fills from Alpaca.</div>"
)


def pnl_note(recon):
    """Caveat that adapts to whether Alpaca reconciliation is available."""
    if not recon:
        return PNL_CAVEAT
    when = esc(str(recon.get("generated_at", ""))[:16].replace("T", " "))
    return (
        "<div class=note style='border-color:var(--accent);"
        "background:color-mix(in srgb,var(--accent) 10%,var(--card))'>"
        "<b style='color:var(--accent)'>Booked vs Real.</b> <b>Booked</b> P&amp;L is off the "
        "analyst's signal price (optimistic — Blocker 2). <b>Real</b> is the actual Alpaca fill. "
        f"Reconciled from account {esc(recon.get('account',''))} at {when} · "
        f"{recon.get('lifecycles_matched', 0)} lifecycles matched · "
        f"booked {recon.get('booked_total',0):+.0f} vs real {recon.get('real_total',0):+.0f}. "
        "<b>Trust the Real columns.</b></div>"
    )

JS = """
function showDay(d){
  var g=document.getElementById('cal-grid'); if(g) g.style.display='none';
  var det=document.getElementById('cal-detail'); if(det) det.style.display='block';
  document.querySelectorAll('.day-detail').forEach(function(x){x.style.display='none';});
  var el=document.getElementById('day-'+d); if(el) el.style.display='block';
  window.scrollTo(0,0);
}
function backToCal(){
  var det=document.getElementById('cal-detail'); if(det) det.style.display='none';
  var g=document.getElementById('cal-grid'); if(g) g.style.display='block';
  window.scrollTo(0,0);
}
(function(){
  var tabs=document.querySelectorAll('.tab');
  function show(name){
    tabs.forEach(function(t){t.classList.toggle('active',t.dataset.pane===name);});
    document.querySelectorAll('.pane').forEach(function(p){
      p.classList.toggle('active', p.id==='pane-'+name);
    });
    if(name==='calendar') backToCal();  // always land on the grid
    try{localStorage.setItem('dash_tab',name);}catch(e){}
  }
  tabs.forEach(function(t){t.addEventListener('click',function(){show(t.dataset.pane);});});
  try{var saved=localStorage.getItem('dash_tab'); if(saved) show(saved);}catch(e){}
})();
"""


def render_daily(day, data, ch_map, days_back, recon=None):
    trades, closed, open_pos, messages, shadow, log_events, (tdays, tstats) = data

    realized = sum(t["pnl"] for t in trades if t["pnl"] is not None)
    n_exec = sum(1 for t in trades if t["status"] == "executed")
    n_closed_today = sum(1 for p in closed if ct_date(p["closed_at"]) == day and p["status"] == "closed")
    n_errors = sum(1 for e in log_events if e["level"] == "ERROR")
    n_parse_err = sum(1 for e in log_events if e["level"] == "ERROR" and e["parse_related"])

    out = []
    out.append(f"<div class=sub>Trading day <b>{esc(day)}</b> (America/Chicago)</div>")

    # summary cards
    pnl_cls = "big-ok" if realized > 0 else ("big-err" if realized < 0 else "")
    err_cls = "big-err" if n_errors else "big-ok"
    out.append("<div class=cards>")
    out.append(f'<div class="card {pnl_cls}"><div class=k>Booked P&amp;L</div>'
               f'<div class=v>{money(realized)}</div></div>')
    out.append(f'<div class=card><div class=k>Trades executed</div><div class=v>{n_exec}</div></div>')
    out.append(f'<div class=card><div class=k>Lifecycles closed</div><div class=v>{n_closed_today}</div></div>')
    out.append(f'<div class=card><div class=k>Open positions</div><div class=v>{len(open_pos)}</div></div>')
    out.append(f'<div class="card {err_cls}"><div class=k>Parser errors</div>'
               f'<div class=v>{n_parse_err}</div></div>')
    out.append("</div>")

    if any(t["pnl"] is not None for t in trades):
        out.append(pnl_note(recon))

    # trend strip
    out.append(f"<h2>Last {days_back} days</h2><div class=scroll><table class=trend>")
    out.append("<tr><th>Date</th><th>Signals parsed</th><th>Trades</th>"
               "<th>Booked P&amp;L</th><th>Lifecycles closed</th></tr>")
    for d in tdays:
        s = tstats[d]
        cls = " class=today-row" if d == day else ""
        pnl = money(s["pnl"]) if s["trades"] else "—"
        out.append(f"<tr{cls}><td>{esc(d)}</td><td>{s['signals'] or '—'}</td>"
                   f"<td>{s['trades'] or '—'}</td><td>{pnl}</td><td>{s['closed'] or '—'}</td></tr>")
    out.append("</table></div>")

    # executed trades
    out.append("<h2>Trades executed today</h2>")
    if trades:
        out.append("<div class=scroll><table>")
        out.append("<tr><th>Time</th><th>Analyst</th><th>Action</th><th>Contract</th>"
                   "<th>Signal $</th><th title='Recorded off signal price, not the real fill'>"
                   "Booked $</th><th title='Actual Alpaca fill'>Real $</th>"
                   "<th>P&amp;L</th><th>Status</th><th>Conf</th></tr>")
        for t in trades:
            conf = t["confidence"]
            conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "—"
            act_cls = "ok" if t["action"] == "entry" else "warn"
            entry_s = esc(t["entry_price"]) if t["entry_price"] is not None else "—"
            fill_s = esc(t["executed_price"]) if t["executed_price"] is not None else "—"
            tr = recon.get("trade_real", {}).get(str(t["id"])) if recon else None
            real_s = f"<b>{esc(tr['real_price'])}</b>" if tr else "—"
            out.append(
                f"<tr><td class=mono>{ct_hm(t['executed_at'] or t['created_at'])}</td>"
                f"<td>{esc(t['analyst'])}</td>"
                f"<td><span class='chip {act_cls}'>{esc(t['action'])}</span></td>"
                f"<td class=mono>{esc(contract(t['analyst'],t['ticker'],t['direction'],t['strike'],t['expiry']))}</td>"
                f"<td class=mono>{entry_s}</td>"
                f"<td class=mono>{fill_s}</td>"
                f"<td class=mono>{real_s}</td>"
                f"<td class=mono>{money(t['pnl'])}</td>"
                f"<td><span class=tag>{esc(t['status'])}</span></td>"
                f"<td class=mono>{conf_s}</td></tr>")
        out.append("</table></div>")
    else:
        out.append("<div class=empty>No trades executed today.</div>")

    # lifecycles: closed today
    closed_today = [p for p in closed if ct_date(p["closed_at"]) == day and p["status"] == "closed"]
    out.append("<h2>Lifecycles closed today</h2>")
    if closed_today:
        out.append("<div class=scroll><table>")
        out.append("<tr><th>Analyst</th><th>Contract</th><th>Opened</th><th>Closed</th>"
                   "<th>Entry $</th><th>Trims</th><th>Booked P&amp;L</th>"
                   "<th>Real P&amp;L</th><th>Δ</th></tr>")
        for p in closed_today:
            real_c, delta_c = real_pnl_cell(recon, p["id"])
            out.append(
                f"<tr><td>{esc(p['analyst'])}</td>"
                f"<td class=mono>{esc(contract(p['analyst'],p['ticker'],p['direction'],p['strike'],p['expiry']))}</td>"
                f"<td class=mono>{esc((ct_date(p['opened_at']) or '')+' '+ct_hm(p['opened_at']))}</td>"
                f"<td class=mono>{ct_hm(p['closed_at'])}</td>"
                f"<td class=mono>{esc(p['entry_price'])}</td>"
                f"<td class=mono>{esc(p['trim_count'])}</td>"
                f"<td class=mono>{money(p['total_pnl'])}</td>"
                f"<td class=mono>{real_c}</td>"
                f"<td class=mono>{delta_c}</td></tr>")
        out.append("</table></div>")
    else:
        out.append("<div class=empty>No positions closed today.</div>")

    # open carried positions
    out.append("<h2>Open positions (carried)</h2>")
    if open_pos:
        out.append("<div class=scroll><table>")
        out.append("<tr><th>Analyst</th><th>Contract</th><th>Qty</th><th>Entry $</th><th>Opened</th></tr>")
        for p in open_pos:
            out.append(
                f"<tr><td>{esc(p['analyst'])}</td>"
                f"<td class=mono>{esc(contract(p['analyst'],p['ticker'],p['direction'],p['strike'],p['expiry']))}</td>"
                f"<td class=mono>{esc(p['current_quantity'])}</td>"
                f"<td class=mono>{esc(p['entry_price'])}</td>"
                f"<td class=mono>{esc((ct_date(p['opened_at']) or '')+' '+ct_hm(p['opened_at']))}</td></tr>")
        out.append("</table></div>")
    else:
        out.append("<div class=empty>Flat — no open positions.</div>")

    # signals & parsing — live executing analysts (eva, ace)
    live = [m for m in messages if ch_map.get(m["channel_id"], ("?", "off"))[1] == "execute"]
    out.append("<h2>Signals &amp; parsing — live analysts</h2>")
    if live:
        out.append("<div class=scroll><table>")
        out.append("<tr><th>Time</th><th>Analyst</th><th>Message</th><th>Parsed as</th><th>How</th></tr>")
        for m in live:
            analyst = ch_map.get(m["channel_id"], ("?", ""))[0]
            label, cls, detail = parse_outcome(m["parsed_as"])
            # prefer the real signal text (raw_message) when the visible content is just a ping
            shown = (m["content"] or "").strip()
            if detail and detail.startswith("conf ") and "·" in detail:
                raw = detail.split("·", 1)[1].strip()
                if raw:
                    shown = raw
            out.append(
                f"<tr><td class=mono>{ct_hm(m['processed_at'])}</td>"
                f"<td>{esc(analyst)}</td>"
                f"<td class=detail>{esc(shown[:200]) or '<em>(embed / no text)</em>'}</td>"
                f"<td><span class='chip {cls}'>{esc(label)}</span></td>"
                f"<td class=detail>{esc(detail[:160])}</td></tr>")
        out.append("</table></div>")
    else:
        out.append("<div class=empty>No Eva/Ace messages seen today.</div>")

    # waxui shadow
    out.append("<h2>Waxui shadow (log-only — never orders)</h2>")
    if shadow:
        tiers = {}
        execu = {"executable": 0, "index/SPX": 0, "other": 0}
        orders = 0
        for d in shadow:
            tiers[d.get("tier", "?")] = tiers.get(d.get("tier", "?"), 0) + 1
            if d.get("executed"):
                orders += 1
            if d.get("parsed"):
                if d.get("is_index"):
                    execu["index/SPX"] += 1
                elif d.get("executable_on_alpaca"):
                    execu["executable"] += 1
                else:
                    execu["other"] += 1
        tier_str = " · ".join(f"{k}: {v}" for k, v in sorted(tiers.items()))
        order_chip = ("<span class='chip ok'>0 orders ✓</span>" if orders == 0
                      else f"<span class='chip err'>{orders} ORDERS ⚠</span>")
        out.append(f"<div class=sub>{len(shadow)} observations · {esc(tier_str)} · "
                   f"executable {execu['executable']} / SPX-index {execu['index/SPX']} · {order_chip}</div>")
        out.append("<div class=scroll><table>")
        out.append("<tr><th>Time</th><th>Message</th><th>Tier</th><th>Parsed</th><th>Instrument</th><th>Order</th></tr>")
        for d in shadow:
            label, cls, _ = parse_outcome(json.dumps({"shadow": True, "tier": d.get("tier"),
                                                      "executed": d.get("executed")}))
            parsed = "—"
            if d.get("parsed"):
                parsed = f"{d.get('action','')} {d.get('ticker','')}".strip()
            instr = d.get("instrument") or "—"
            ok = "✓" if d.get("executable_on_alpaca") else ("index" if d.get("is_index") else "—")
            out.append(
                f"<tr><td class=mono>{ct_hm(d.get('observed_at'))}</td>"
                f"<td class=detail>{esc((d.get('raw_text') or '')[:120])}</td>"
                f"<td><span class='chip {cls}'>{esc(d.get('tier'))}</span></td>"
                f"<td class=mono>{esc(parsed)}</td>"
                f"<td class=detail>{esc(instr)} <span class=tag>{ok}</span></td>"
                f"<td><span class=tag>NO</span></td></tr>")
        out.append("</table></div>")
    else:
        out.append("<div class=empty>No Waxui observations today.</div>")

    # errors & warnings
    out.append("<h2>Errors &amp; warnings (from trading_bot.log)</h2>")
    if log_events:
        note = ("Parser errors reset the Gate-1 clock; infra warnings (order timeouts, "
                "network retries) usually don't. Read each line.")
        out.append(f"<div class=sub>{esc(note)}</div><div class=scroll><table>")
        out.append("<tr><th>Time</th><th>Level</th><th>Source</th><th>Message</th></tr>")
        for e in log_events:
            lvl_cls = "err" if e["level"] == "ERROR" else "warn"
            flag = " <span class='chip err'>parse-related</span>" if (e["parse_related"] and e["level"] == "ERROR") else ""
            out.append(
                f"<tr><td class=mono>{esc(e['time'])}</td>"
                f"<td><span class='chip {lvl_cls}'>{esc(e['level'])}</span>{flag}</td>"
                f"<td class=tag>{esc(e['logger'])}</td>"
                f"<td class=detail>{esc(e['msg'][:220])}</td></tr>")
        out.append("</table></div>")
    else:
        out.append("<div class=empty>Clean — no errors or warnings logged today. ✓</div>")

    return "".join(out)


def render_alltime(day, alltime, ch_map, recon=None, spnl=None):
    trades, closed, open_pos, series, shadow_all = alltime
    rows, shadow_analysts = analyst_breakdown(trades, closed, ch_map)

    total_pnl = sum(t["pnl"] for t in trades if t["pnl"] is not None)
    wins = sum(1 for p in closed if (p["total_pnl"] or 0) > 0)
    losses = sum(1 for p in closed if (p["total_pnl"] or 0) < 0)
    n_life = len(closed)
    days_traded = len({s["date"] for s in series if s["trades"]})
    winrate = f"{100*wins/(wins+losses):.0f}%" if (wins + losses) else "—"

    # Best/worst prefer REAL fills when reconciled — that's the whole point.
    real_vals = ([pr["real_pnl"] for pr in recon.get("position_real", {}).values()
                  if pr.get("matched")] if recon else [])
    if real_vals:
        best, worst, bw_lbl = max(real_vals), min(real_vals), "Best / worst (real)"
    else:
        best = max((p["total_pnl"] or 0 for p in closed), default=0.0)
        worst = min((p["total_pnl"] or 0 for p in closed), default=0.0)
        bw_lbl = "Best / worst (booked)"

    out = []
    out.append(f"<div class=sub>Current paper run · <b>{esc(ERA_START)} → {esc(day)}</b> "
               f"(Eva/Ace execute, Waxui shadow). Excludes pre-restart experiments.</div>")

    out.append("<div class=cards>")
    pnl_cls = "big-ok" if total_pnl > 0 else ("big-err" if total_pnl < 0 else "")
    out.append(f'<div class="card {pnl_cls}"><div class=k>Total booked P&amp;L</div>'
               f'<div class=v>{money(total_pnl)}</div></div>')
    if recon:
        rt = recon.get("real_total", 0.0)
        rt_cls = "big-ok" if rt > 0 else ("big-err" if rt < 0 else "")
        out.append(f'<div class="card {rt_cls}"><div class=k>Total real P&amp;L</div>'
                   f'<div class=v>{money(rt)}</div>'
                   f'<div class=tag>Δ {money(round(rt-total_pnl,2))} vs booked</div></div>')
    out.append(f'<div class=card><div class=k>Lifecycles closed</div>'
               f'<div class=v>{n_life}</div><div class=tag>W/L {wins}/{losses} · {winrate}</div></div>')
    out.append(f'<div class=card><div class=k>Trades</div><div class=v>{len(trades)}</div></div>')
    out.append(f'<div class=card><div class=k>Days traded</div><div class=v>{days_traded}</div></div>')
    out.append(f'<div class=card><div class=k>{bw_lbl}</div>'
               f'<div class=v style="font-size:16px">{money(best)} / {money(worst)}</div></div>')
    out.append(f'<div class=card><div class=k>Open now</div><div class=v>{len(open_pos)}</div></div>')
    out.append("</div>")
    out.append(pnl_note(recon))

    # equity curve
    out.append("<h2>Cumulative booked P&amp;L</h2>")
    out.append(equity_curve_svg(series))

    # per-analyst breakdown
    out.append("<h2>By analyst</h2><div class=scroll><table>")
    out.append("<tr><th>Analyst</th><th>Mode</th><th>Trades</th><th>Lifecycles</th>"
               "<th>W/L</th><th>Booked P&amp;L</th></tr>")
    for a in sorted(rows, key=lambda a: -rows[a]["pnl"]):
        r = rows[a]
        is_shadow = a in shadow_analysts
        mode = "<span class='chip shadow'>shadow</span>" if is_shadow else "<span class='chip execute'>execute</span>"
        wl = f"{r['wins']}/{r['losses']}" if (r["wins"] or r["losses"]) else "—"
        pnl = "<span class=tag>n/a (shadow)</span>" if is_shadow else money(r["pnl"])
        out.append(f"<tr><td>{esc(a)}</td><td>{mode}</td><td class=mono>{r['trades'] or '—'}</td>"
                   f"<td class=mono>{r['lifecycles'] or '—'}</td><td class=mono>{wl}</td>"
                   f"<td class=mono>{pnl}</td></tr>")
    out.append("</table></div>")

    # all lifecycles
    out.append("<h2>All lifecycles</h2>")
    if closed:
        out.append("<div class=scroll><table>")
        out.append("<tr><th>Closed</th><th>Analyst</th><th>Contract</th><th>Opened</th>"
                   "<th>Held</th><th>Entry $</th><th>Booked P&amp;L</th>"
                   "<th>Real P&amp;L</th><th>Δ</th></tr>")
        for p in sorted(closed, key=lambda p: str(p["closed_at"]), reverse=True):
            op, cl = parse_ts(p["opened_at"]), parse_ts(p["closed_at"])
            held = "—"
            if op and cl:
                hrs = (cl - op).total_seconds() / 3600
                held = f"{hrs:.0f}h" if hrs < 48 else f"{hrs/24:.0f}d"
            real_c, delta_c = real_pnl_cell(recon, p["id"])
            out.append(
                f"<tr><td class=mono>{esc(ct_date(p['closed_at']))}</td>"
                f"<td>{esc(p['analyst'])}</td>"
                f"<td class=mono>{esc(contract(p['analyst'],p['ticker'],p['direction'],p['strike'],p['expiry']))}</td>"
                f"<td class=mono>{esc(ct_date(p['opened_at']))}</td>"
                f"<td class=mono>{held}</td>"
                f"<td class=mono>{esc(p['entry_price'])}</td>"
                f"<td class=mono>{money(p['total_pnl'])}</td>"
                f"<td class=mono>{real_c}</td>"
                f"<td class=mono>{delta_c}</td></tr>")
        out.append("</table></div>")
    else:
        out.append("<div class=empty>No closed lifecycles yet in this era.</div>")

    # daily history
    out.append("<h2>Daily history</h2>")
    if series:
        out.append("<div class=scroll><table class=trend>")
        out.append("<tr><th>Date</th><th>Trades</th><th>Booked P&amp;L</th>"
                   "<th>Cumulative</th><th>Lifecycles</th></tr>")
        for s in reversed(series):
            cls = " class=today-row" if s["date"] == day else ""
            out.append(f"<tr{cls}><td>{esc(s['date'])}</td><td>{s['trades'] or '—'}</td>"
                       f"<td>{money(s['pnl']) if s['trades'] else '—'}</td>"
                       f"<td>{money(s['cum'])}</td><td>{s['closed'] or '—'}</td></tr>")
        out.append("</table></div>")
    else:
        out.append("<div class=empty>No trading days yet in this era.</div>")

    # shadow totals
    out.append("<h2>Waxui shadow — all-time</h2>")
    if shadow_all:
        tiers, execu, orders = {}, {"executable": 0, "index/SPX": 0, "other": 0}, 0
        for d in shadow_all:
            tiers[d.get("tier", "?")] = tiers.get(d.get("tier", "?"), 0) + 1
            if d.get("executed"):
                orders += 1
            if d.get("parsed"):
                if d.get("is_index"):
                    execu["index/SPX"] += 1
                elif d.get("executable_on_alpaca"):
                    execu["executable"] += 1
                else:
                    execu["other"] += 1
        tier_str = " · ".join(f"{k}: {v}" for k, v in sorted(tiers.items()))
        order_chip = ("<span class='chip ok'>0 orders ✓</span>" if orders == 0
                      else f"<span class='chip err'>{orders} ORDERS ⚠</span>")
        out.append(f"<div class=sub>{len(shadow_all)} observations · {esc(tier_str)}</div>")
        out.append("<div class=cards>")
        out.append(f'<div class=card><div class=k>Observations</div><div class=v>{len(shadow_all)}</div></div>')
        out.append(f'<div class=card><div class=k>Executable / SPX-index</div>'
                   f'<div class=v style="font-size:18px">{execu["executable"]} / {execu["index/SPX"]}</div></div>')
        out.append(f'<div class=card><div class=k>Orders placed</div><div class=v>{order_chip}</div></div>')
        out.append("</div>")
    else:
        out.append("<div class=empty>No Waxui observations recorded yet.</div>")

    out.append(render_shadow_pnl(spnl))
    return "".join(out)


def render_shadow_pnl(spnl):
    """Waxui hypothetical P&L — 'what Waxui would have made' for the Tastytrade call."""
    out = ["<h2>Waxui shadow — hypothetical P&amp;L</h2>"]
    if not spnl or not spnl.get("ideas"):
        out.append("<div class=note>Waxui places no orders (shadow). Run "
                   "<span class=mono>./venv/bin/python shadow_pnl.py</span> to estimate what "
                   "its ideas would have returned.</div>")
        return "".join(out)

    when = esc(str(spnl.get("generated_at", ""))[:16].replace("T", " "))
    out.append(
        "<div class=note style='border-color:var(--shadow-c);"
        "background:color-mix(in srgb,var(--shadow-c) 10%,var(--card))'>"
        "<b style='color:var(--shadow-c)'>Hypothetical — Waxui never trades.</b> "
        "SPY/ETF ideas are priced at the <b>actual Alpaca option price at each signal's "
        "timestamp</b> (verified market data). SPX has no Alpaca data, so it uses Waxui's "
        "stated prices (<b>unverified</b>). Four outcomes per idea: "
        "<b>First-trim</b> (flatten the whole contract at the first trim/exit update — the bot's "
        "actual 1-contract model), <b>Hold-to-exit</b> (held to the close signal), "
        "<b>Laddered</b> (equal trims at the real price each), <b>Peak</b> (sold all at the top — "
        f"best case). 1 contract/idea. Reconciled {when}.</div>")

    t = spnl["totals"]

    def card(label, value, tag=""):
        cls = "big-ok" if value > 0 else ("big-err" if value < 0 else "")
        tag_html = f"<div class=tag>{tag}</div>" if tag else ""
        return (f'<div class="card {cls}"><div class=k>{label}</div>'
                f'<div class=v style="font-size:20px">{money(value)}</div>{tag_html}</div>')

    out.append("<div class=cards>")
    out.append(card("First-trim, full close", t["all"]["first"],
                    f"the bot's 1-contract model · {t['all']['n']} ideas"))
    out.append(card("Laddered", t["all"]["ladder"], "equal trims (realistic)"))
    out.append(card("Hold-to-exit", t["all"]["exit"], "held to close signal"))
    out.append(card("Peak", t["all"]["peak"], "best case (sold at top)"))
    out.append(card("Executable (SPY/ETF)", t["executable"]["first"],
                    f"first-trim · ladder {t['executable']['ladder']:+.0f} / exit {t['executable']['exit']:+.0f}"))
    out.append(card("Index-only (SPX)", t["index"]["first"],
                    f"first-trim · UNVERIFIED · {t['index']['n']} idea(s)"))
    out.append("</div>")

    out.append("<div class=scroll><table>")
    out.append("<tr><th>Entry date</th><th>Ticker</th><th>Contract</th><th>Priced</th>"
               "<th>Entry $</th><th>1st-upd $</th><th>Peak $</th>"
               "<th>First-trim (full)</th><th>Hold-to-exit</th><th>Laddered</th><th>Peak</th></tr>")
    for i in spnl["ideas"]:
        tk = esc(i.get("ticker"))
        if not i.get("closed"):
            out.append(f"<tr><td class=mono>{esc(str(i.get('entry_ts'))[:10])}</td><td>{tk}</td>"
                       f"<td class=mono>{esc(i.get('occ') or '—')}</td>"
                       f"<td colspan=8><span class=tag>still open — not yet priced</span></td></tr>")
            continue
        src = i.get("source")
        src_chip = ("<span class='chip ok'>market</span>" if src == "alpaca"
                    else "<span class='chip warn'>stated</span>")
        first_star = " <span class=tag>*</span>" if i.get("first_is_exit") else ""

        def pct(key):
            p = i.get(f"{key}_pct")
            return f" <span class=tag>{p:+.0f}%</span>" if p is not None else ""
        out.append(
            f"<tr><td class=mono>{esc(str(i.get('entry_ts'))[:10])}</td><td>{tk}</td>"
            f"<td class=mono>{esc(i.get('occ') or '—')}</td>"
            f"<td>{src_chip}</td>"
            f"<td class=mono>{esc(i.get('entry_px'))}</td>"
            f"<td class=mono>{esc(i.get('first_px'))}{first_star}</td>"
            f"<td class=mono>{esc(i.get('peak_px'))}</td>"
            f"<td class=mono><b>{money(i.get('first_pnl'))}</b>{pct('first')}</td>"
            f"<td class=mono>{money(i.get('exit_pnl'))}{pct('exit')}</td>"
            f"<td class=mono>{money(i.get('ladder_pnl'))}{pct('ladder')}</td>"
            f"<td class=mono>{money(i.get('peak_pnl'))}{pct('peak')}</td></tr>")
    out.append("</table></div>")
    out.append("<div class=sub>* the idea never trimmed, so its first position update <i>was</i> "
               "the exit — first-trim and hold-to-exit are identical for those.</div>")
    return "".join(out)


def build_calendar(conn, ch_map, era_start, today, days_back, recon=None):
    """Per-day P&L for the grid + a fully-rendered detail pane per active day."""
    pnl_by_day, active = {}, set()

    for r in conn.execute("SELECT executed_at, created_at, pnl FROM trades"):
        d = ct_date(r["executed_at"] or r["created_at"])
        if d and era_start <= d <= today:
            active.add(d)
            if r["pnl"] is not None:
                pnl_by_day[d] = pnl_by_day.get(d, 0.0) + r["pnl"]

    for r in conn.execute("SELECT processed_at, channel_id FROM message_log"):
        d = ct_date(r["processed_at"])
        if d and era_start <= d <= today and r["channel_id"] in ch_map:
            active.add(d)

    shadow_map = {}
    for o in _iter_shadow():
        d = ct_date(o.get("observed_at"))
        if d:
            shadow_map.setdefault(d, []).append(o)
            if era_start <= d <= today:
                active.add(d)

    log_map = load_log_all()
    for d in log_map:
        if era_start <= d <= today:
            active.add(d)

    # Render each active day's full detail once (reuses the daily view).
    details = {}
    for d in sorted(active):
        data = (*load_day(conn, d), shadow_map.get(d, []), log_map.get(d, []),
                trend(conn, days_back, d))
        details[d] = render_daily(d, data, ch_map, days_back, recon)

    # Month range era_start → today.
    months = []
    y, m = int(era_start[:4]), int(era_start[5:7])
    ey, em = int(today[:4]), int(today[5:7])
    while (y, m) <= (ey, em):
        months.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1

    return months, details, pnl_by_day, active


WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def render_calendar(cal_data):
    months, details, pnl_by_day, active = cal_data
    out = []
    out.append("<div class=sub>Green/red = realized P&amp;L that day. "
               "Click any highlighted day for its full breakdown.</div>")

    out.append("<div id=cal-grid>")
    grid = calmod.Calendar(firstweekday=6)  # Sunday-first
    for (y, m) in months:
        out.append(f"<div class=cal-month><h3>{calmod.month_name[m]} {y}</h3>")
        out.append("<div class=scroll><table class=cal><tr>"
                   + "".join(f"<th>{w}</th>" for w in WEEKDAYS) + "</tr>")
        for week in grid.monthdayscalendar(y, m):
            out.append("<tr>")
            for dd in week:
                if dd == 0:
                    out.append("<td class=cal-empty></td>")
                    continue
                ds = f"{y:04d}-{m:02d}-{dd:02d}"
                pnl = pnl_by_day.get(ds)
                is_active = ds in active
                inner = f"<span class=cal-day>{dd}</span>"
                cls = ""
                if pnl is not None:
                    cls = "cal-pos" if pnl > 0 else ("cal-neg" if pnl < 0 else "cal-flat")
                    inner += f"<span class=cal-pnl>{'+' if pnl > 0 else ''}{pnl:,.0f}</span>"
                elif is_active:
                    inner += "<span class='cal-pnl cal-mut'>seen</span>"
                if is_active:
                    out.append(f"<td class='cal-cell {cls}'>"
                               f"<button class=cal-btn onclick=\"showDay('{ds}')\">{inner}</button></td>")
                else:
                    out.append(f"<td class='cal-cell cal-off'>{inner}</td>")
            out.append("</tr>")
        out.append("</table></div></div>")
    out.append("</div>")  # #cal-grid

    out.append("<div id=cal-detail style='display:none'>")
    out.append("<button class=backbtn onclick='backToCal()'>← Back to calendar</button>")
    for d in sorted(details):
        out.append(f"<div class=day-detail id='day-{d}' style='display:none'>{details[d]}</div>")
    out.append("</div>")
    return "".join(out)


def build_html(day, data, alltime, cal_data, ch_map, days_back, recon=None, spnl=None):
    daily = render_daily(day, data, ch_map, days_back, recon)
    alltime_html = render_alltime(day, alltime, ch_map, recon, spnl)
    calendar_html = render_calendar(cal_data)
    generated = datetime.now(CT).strftime("%Y-%m-%d %H:%M %Z")
    body = (
        f"<div class=wrap>"
        f"<h1>Trading Bot Dashboard</h1>"
        f"<div class=sub>generated {esc(generated)}</div>"
        f"<div class=tabs role=tablist>"
        f"<button class='tab active' data-pane=daily>Daily</button>"
        f"<button class=tab data-pane=alltime>All-time</button>"
        f"<button class=tab data-pane=calendar>Calendar</button>"
        f"</div>"
        f"<div id=pane-daily class='pane active'>{daily}</div>"
        f"<div id=pane-alltime class=pane>{alltime_html}</div>"
        f"<div id=pane-calendar class=pane>{calendar_html}</div>"
        f"<div class=foot>Read-only report · sources: trading_bot.db · "
        f"logs/waxui_shadow.jsonl · trading_bot.log · <a href='latest.html'>latest.html</a></div>"
        f"</div>")
    return (f"<!doctype html><html><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Trading Bot Dashboard — {esc(day)}</title><style>{CSS}</style></head>"
            f"<body>{body}<script>{JS}</script></body></html>")


# ── main ───────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="CT date YYYY-MM-DD (default: today)")
    ap.add_argument("--days", type=int, default=10, help="trend strip length")
    ap.add_argument("--open", action="store_true", help="open the report in a browser")
    args = ap.parse_args()

    day = args.date or datetime.now(CT).strftime("%Y-%m-%d")

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    ch_map = channel_map()

    recon = load_recon()
    spnl = load_shadow_pnl()
    data = (*load_day(conn, day), load_shadow(day), load_log_events(day),
            trend(conn, args.days, day))
    alltime = (*load_alltime(conn, ERA_START), load_shadow_all())
    cal_data = build_calendar(conn, ch_map, ERA_START, day, args.days, recon)
    doc = build_html(day, data, alltime, cal_data, ch_map, args.days, recon, spnl)
    conn.close()

    os.makedirs(REPORT_DIR, exist_ok=True)
    dated = os.path.join(REPORT_DIR, f"daily_{day}.html")
    latest = os.path.join(REPORT_DIR, "latest.html")
    for path in (dated, latest):
        with open(path, "w", encoding="utf-8") as f:
            f.write(doc)

    print(f"Report written: {dated}")
    print(f"Bookmark this:  {latest}")
    if args.open:
        subprocess.run(["open", dated], check=False)


if __name__ == "__main__":
    main()
