#!/usr/bin/env python3
"""Hypothetical P&L for Waxui shadow ideas — "what Waxui would have made."

Waxui runs log-only (shadow), so it has no trades/positions. But its signals
are recorded in logs/waxui_shadow.jsonl, and its exits usually state no price
("Closed SPY here"). To evaluate whether Waxui is worth executing (the
Tastytrade decision), this reconstructs each idea (entry → trims → exit) and
prices it as accurately as possible:

  • SPY / non-index: the ACTUAL Alpaca option price at each signal's timestamp
    (entry, exit, and the peak in between) — verified market data, not Waxui's
    self-reported numbers.
  • SPX / index: Alpaca has no data, so it falls back to Waxui's stated prices,
    clearly labelled unverified.

Two outcomes per idea, because Waxui trims in a ladder:
  • exit  P&L — enter at the entry signal, exit at the exit signal (realistic).
  • peak  P&L — the best the contract reached during the hold (upper bound).

READ-ONLY. Writes reports/shadow_pnl_cache.json for the dashboard.
Usage:  ./venv/bin/python shadow_pnl.py
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SHADOW_LOG = os.path.join(HERE, "logs", "waxui_shadow.jsonl")
CACHE_PATH = os.path.join(HERE, "reports", "shadow_pnl_cache.json")
BARS_URL = "https://data.alpaca.markets/v1beta1/options/bars"
TICKER_RE = re.compile(r"\b(SPX|SPY|QQQ|IWM|NDX|RUT|XSP|DIA)\b")
CONTRACT_MULT = 100  # option contract = 100 shares


def occ_symbol(ticker, expiry, direction, strike):
    try:
        d = date.fromisoformat(str(expiry)[:10])
    except (ValueError, TypeError):
        return None
    cp = "C" if direction == "call" else "P"
    return f"{ticker}{d.strftime('%y%m%d')}{cp}{int(round(float(strike) * 1000)):08d}"


def parse_ts(s):
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _ticker(obs):
    if obs.get("ticker"):
        return obs["ticker"]
    m = TICKER_RE.search(obs.get("raw_text") or "")
    return m.group(1) if m else None


def load_ideas():
    """Walk the shadow log chronologically; pair entry → trims → exit per ticker."""
    rows = []
    for line in open(SHADOW_LOG, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(d.get("message_id", "")).startswith("replay") or not d.get("parsed"):
            continue
        rows.append(d)
    rows.sort(key=lambda r: r["observed_at"])

    ideas, open_by_ticker = [], {}
    for r in rows:
        tk = _ticker(r)
        if not tk:
            continue
        action = r.get("action")
        if action == "entry":
            idea = {
                "ticker": tk, "is_index": bool(r.get("is_index")),
                "occ": occ_symbol(tk, r.get("expiry"), r.get("direction"), r.get("strike"))
                if r.get("strike") else None,
                "entry_ts": r["observed_at"], "entry_stated": r.get("price"),
                "trims": [], "exit_ts": None, "closed": False,
            }
            open_by_ticker[tk] = idea
            ideas.append(idea)
        elif action in ("trim", "exit"):
            idea = open_by_ticker.get(tk)
            if not idea:
                continue
            if r.get("price") is not None:
                idea["trims"].append({"ts": r["observed_at"], "stated": r["price"]})
            if action == "exit":
                idea["exit_ts"] = r["observed_at"]
                idea["closed"] = True
                open_by_ticker.pop(tk, None)
    return ideas


def fetch_bars(headers, sym, start, end):
    """1-minute option bars over [start, end+2m]. [] if none / no data."""
    if not sym:
        return []
    params = {"symbols": sym, "timeframe": "1Min",
              "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "end": (end + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "limit": 1000}
    try:
        r = requests.get(BARS_URL, headers=headers, params=params, timeout=20)
        if r.status_code != 200:
            return []
        return r.json().get("bars", {}).get(sym, []) or []
    except requests.RequestException:
        return []


def price_market(bars, ts, field):
    """Bar value nearest `ts` for a given field ('o' entry, 'c' exit)."""
    if not bars:
        return None
    target = parse_ts(ts)
    best = min(bars, key=lambda b: abs(parse_ts(b["t"]).timestamp() - target.timestamp()))
    return best.get(field)


def main():
    from config import Config
    cfg = Config()
    headers = {"APCA-API-KEY-ID": cfg.alpaca_api_key,
               "APCA-API-SECRET-KEY": cfg.alpaca_secret_key}

    ideas = load_ideas()
    out_ideas = []
    tot = {"executable": {"first": 0.0, "exit": 0.0, "ladder": 0.0, "peak": 0.0, "n": 0},
           "index": {"first": 0.0, "exit": 0.0, "ladder": 0.0, "peak": 0.0, "n": 0}}

    for idea in ideas:
        rec = {"ticker": idea["ticker"], "occ": idea["occ"],
               "is_index": idea["is_index"], "entry_ts": idea["entry_ts"],
               "exit_ts": idea["exit_ts"], "closed": idea["closed"]}
        if not idea["closed"]:
            rec["status"] = "open"
            out_ideas.append(rec)
            continue

        entry_dt, exit_dt = parse_ts(idea["entry_ts"]), parse_ts(idea["exit_ts"])
        stated_trims = [t["stated"] for t in idea["trims"] if t["stated"] is not None]

        # `sells` = the price captured at each trim, plus the final exit. Waxui
        # trims in a ladder, so the realistic realized return is the average of
        # these (equal-weight) — between the exit (bulk) and peak (best case).
        if not idea["is_index"] and idea["occ"]:
            bars = fetch_bars(headers, idea["occ"], entry_dt, exit_dt)
            if bars:
                source = "alpaca"
                entry_px = price_market(bars, idea["entry_ts"], "o")
                exit_px = price_market(bars, idea["exit_ts"], "c")
                peak_px = max((b["h"] for b in bars), default=None)
                sells = [price_market(bars, t["ts"], "c") for t in idea["trims"]]
                sells = [s for s in sells if s is not None]
                if exit_px is not None:
                    sells.append(exit_px)
            else:
                source = "waxui-stated"  # SPY data gap → fall back
                entry_px = idea["entry_stated"]
                sells = list(stated_trims)
                exit_px = sells[-1] if sells else entry_px
                if exit_px is not None and (not sells):
                    sells = [exit_px]
                peak_px = max(sells + [entry_px or 0]) or None
        else:
            source = "waxui-stated"  # index (SPX): no Alpaca data
            entry_px = idea["entry_stated"]
            sells = list(stated_trims)
            exit_px = sells[-1] if sells else entry_px
            if not sells and exit_px is not None:
                sells = [exit_px]
            peak_px = max(sells + [entry_px or 0]) or None

        ladder_px = sum(sells) / len(sells) if sells else None
        # First position update: close the WHOLE contract at the first trim's
        # price (or the exit price if the idea never trimmed). This is the bot's
        # actual 1-contract model — any trim/exit marker flattens.
        first_px = sells[0] if sells else None

        rec["source"] = source
        rec["entry_px"] = round(entry_px, 4) if entry_px is not None else None
        rec["exit_px"] = round(exit_px, 4) if exit_px is not None else None
        rec["peak_px"] = round(peak_px, 4) if peak_px is not None else None
        rec["first_px"] = round(first_px, 4) if first_px is not None else None
        rec["entry_stated"] = idea["entry_stated"]
        rec["n_trims"] = len(idea["trims"])
        rec["first_is_exit"] = len(idea["trims"]) == 0  # no trim → first update was the close
        for key, px in (("first", first_px), ("exit", exit_px),
                        ("ladder", ladder_px), ("peak", peak_px)):
            if entry_px and px is not None:
                rec[f"{key}_pnl"] = round((px - entry_px) * CONTRACT_MULT, 2)
                rec[f"{key}_pct"] = round((px - entry_px) / entry_px * 100, 1)

        bucket = "index" if idea["is_index"] else "executable"
        if "exit_pnl" in rec:
            for key in ("first", "exit", "ladder", "peak"):
                tot[bucket][key] += rec.get(f"{key}_pnl", rec["exit_pnl"])
            tot[bucket]["n"] += 1
        out_ideas.append(rec)

    def _all(key):
        return round(tot["executable"][key] + tot["index"][key], 2)
    cache = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ideas": out_ideas,
        "totals": {
            "executable": {k: round(v, 2) if isinstance(v, float) else v
                           for k, v in tot["executable"].items()},
            "index": {k: round(v, 2) if isinstance(v, float) else v
                      for k, v in tot["index"].items()},
            "all": {"first": _all("first"), "exit": _all("exit"), "ladder": _all("ladder"),
                    "peak": _all("peak"), "n": tot["executable"]["n"] + tot["index"]["n"]},
        },
    }
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

    # ── stdout summary ──
    print(f"Waxui shadow hypothetical P&L · {len(out_ideas)} ideas "
          f"({cache['totals']['all']['n']} closed & priced)\n")
    print(f"{'Date':10s} {'Tkr':4s} {'Src':6s} {'Entry':>6s} {'1stUpd':>7s} "
          f"{'FirstP&L':>9s} {'ExitP&L':>8s} {'LadderP&L':>9s} {'PeakP&L':>8s}")
    for i in out_ideas:
        if not i.get("closed"):
            print(f"{i['entry_ts'][:10]} {i['ticker']:4s} {'—':6s}  (still open)")
            continue
        src = {"alpaca": "mkt", "waxui-stated": "stated"}.get(i.get("source"), "?")
        flag = "*" if i.get("first_is_exit") else " "
        print(f"{i['entry_ts'][:10]} {i['ticker']:4s} {src:6s} "
              f"{str(i.get('entry_px','?')):>6s} {str(i.get('first_px','?')):>6s}{flag}"
              f"{i.get('first_pnl',0):+9.2f} {i.get('exit_pnl',0):+8.2f} "
              f"{i.get('ladder_pnl',0):+9.2f} {i.get('peak_pnl',0):+8.2f}")
    print("  (* = idea never trimmed, so 'first update' was the exit itself)")
    t = cache["totals"]
    for lbl, key in (("Executable (SPY/ETF)", "executable"), ("Index-only (SPX) ", "index"),
                     ("ALL              ", "all")):
        b = t[key]
        print(f"  {lbl}: first-trim {b['first']:+.2f} · exit {b['exit']:+.2f} · "
              f"ladder {b['ladder']:+.2f} · peak {b['peak']:+.2f}  ({b['n']} ideas)")
    print(f"\nWrote {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
