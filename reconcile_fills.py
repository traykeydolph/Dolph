#!/usr/bin/env python3
"""Reconcile booked P&L against Alpaca's actual fills.

The bot records executed_price/P&L off the analyst's SIGNAL price, not the real
Alpaca fill (LIVE_SAFETY Blocker 2), so the DB's numbers run optimistic/wrong.
Alpaca is the ground truth: every order carries filled_avg_price and a
deterministic client_order_id ({analyst}_{message_id}[_exit][_mkt]).

This tool fetches the account's order history, computes the REAL fill price per
trade and REAL P&L per closed lifecycle, and writes reports/fills_cache.json for
the dashboard to display alongside the booked numbers.

READ-ONLY against Alpaca (list_orders) and the DB. Touches no execution logic.

Usage:  ./venv/bin/python reconcile_fills.py
"""

import json
import os
import sqlite3
import sys
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DB_PATH = os.path.join(HERE, "trading_bot.db")
CACHE_PATH = os.path.join(HERE, "reports", "fills_cache.json")
ERA_START = "2026-07-15"


def occ_symbol(ticker, expiry, direction, strike):
    """Build the OCC option symbol from position fields (matches the bot's builder)."""
    try:
        d = date.fromisoformat(str(expiry)[:10])
    except (ValueError, TypeError):
        return None
    cp = "C" if direction == "call" else "P"
    return f"{ticker}{d.strftime('%y%m%d')}{cp}{int(round(float(strike) * 1000)):08d}"


def to_utc(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def fetch_orders(api, after_iso):
    """All account orders since `after`, paginated (newest-first)."""
    out, until = [], None
    while True:
        try:
            batch = api.list_orders(status="all", limit=500, after=after_iso,
                                    until=until, direction="desc", nested=True)
        except TypeError:  # older SDKs without `nested`
            batch = api.list_orders(status="all", limit=500, after=after_iso,
                                    until=until, direction="desc")
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 500:
            break
        until = batch[-1].submitted_at
    return out


def order_candidates(analyst, message_id, action):
    """The client_order_id(s) the bot would have used for this trade."""
    base = f"{analyst}_{message_id}"
    if action == "entry":
        return [base, f"{base}_mkt"]
    return [f"{base}_exit", f"{base}_exit_mkt"]  # exit / trim / stop_hit


def main():
    from config import Config
    from execution.alpaca_client import AlpacaClient

    try:
        client = AlpacaClient(Config())
        account = client.api.get_account().account_number
        orders = fetch_orders(client.api, f"{ERA_START}T00:00:00Z")
    except Exception as exc:  # noqa: BLE001 — surface, don't clobber a good cache
        print(f"❌ Could not reach Alpaca: {exc}", file=sys.stderr)
        print("   Leaving any existing cache untouched.", file=sys.stderr)
        return 1

    # Index fills two ways: by client_order_id (exact, per-trade) and by symbol
    # (per-lifecycle P&L, independent of trade↔position matching).
    by_coid, by_symbol = {}, {}
    for o in orders:
        fap = getattr(o, "filled_avg_price", None)
        fq = int(float(getattr(o, "filled_qty", 0) or 0))
        filled = o.status == "filled" and fap and fq > 0
        rec = {"price": float(fap) if fap else None, "qty": fq, "status": o.status,
               "side": o.side, "symbol": o.symbol,
               "submitted_at": str(o.submitted_at)}
        coid = getattr(o, "client_order_id", "") or ""
        if coid:
            # a filled order wins over a canceled sibling with the same coid
            if coid not in by_coid or filled:
                by_coid[coid] = rec
        if filled:
            by_symbol.setdefault(o.symbol, []).append(rec)

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # ── per-trade real fill (for the daily "Real $" column) ──
    trade_real = {}
    for t in conn.execute("SELECT id, analyst, message_id, action FROM trades"):
        if not t["message_id"]:
            continue
        for cand in order_candidates(t["analyst"], t["message_id"], t["action"]):
            r = by_coid.get(cand)
            if r and r["status"] == "filled" and r["price"] is not None:
                trade_real[str(t["id"])] = {"real_price": r["price"], "qty": r["qty"],
                                            "client_order_id": cand}
                break

    # ── per-lifecycle real P&L (symbol + time-window scoped) ──
    position_real = {}
    booked_tot = real_tot = 0.0
    matched_n = 0
    rows = []
    for p in conn.execute(
            "SELECT * FROM positions WHERE status='closed' AND opened_at>=? ORDER BY closed_at",
            (ERA_START,)):
        sym = occ_symbol(p["ticker"], p["expiry"], p["direction"], p["strike"])
        booked = p["total_pnl"] or 0.0
        rec = {"booked_pnl": round(booked, 2), "symbol": sym, "matched": False}
        if sym:
            lo = to_utc(p["opened_at"])
            hi = to_utc(p["closed_at"])
            fills = by_symbol.get(sym, [])
            if lo and hi:
                window = []
                for f in fills:
                    st = to_utc(f["submitted_at"])
                    if st and lo.timestamp() - 21600 <= st.timestamp() <= hi.timestamp() + 21600:
                        window.append(f)
                fills = window or fills
            buys = [f for f in fills if f["side"] == "buy"]
            sells = [f for f in fills if f["side"] == "sell"]
            if buys and sells:
                bq = sum(f["qty"] for f in buys)
                sq = sum(f["qty"] for f in sells)
                buy_cost = sum(f["price"] * f["qty"] for f in buys)
                sell_pro = sum(f["price"] * f["qty"] for f in sells)
                real = (sell_pro - buy_cost) * 100  # options: ×100 shares
                rec.update(matched=True, real_pnl=round(real, 2),
                           real_entry=round(buy_cost / bq, 4) if bq else None,
                           real_exit=round(sell_pro / sq, 4) if sq else None,
                           delta=round(real - booked, 2))
                real_tot += real
                matched_n += 1
        booked_tot += booked
        position_real[str(p["id"])] = rec
        rows.append((p["ticker"], booked, rec.get("real_pnl"), rec.get("delta"), rec["matched"]))

    conn.close()

    cache = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "account": account,
        "era_start": ERA_START,
        "orders_seen": len(orders),
        "lifecycles_matched": matched_n,
        "booked_total": round(booked_tot, 2),
        "real_total": round(real_tot, 2),
        "trade_real": trade_real,
        "position_real": position_real,
    }
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

    # ── stdout summary ──
    print(f"Reconciled account {account} · {len(orders)} orders · "
          f"{matched_n} lifecycles matched")
    print(f"{'Ticker':8s} {'Booked':>9s} {'Real':>9s} {'Δ':>8s}")
    for tk, b, r, d, m in rows:
        rs = f"{r:+.2f}" if m else "  n/a"
        ds = f"{d:+.2f}" if m else "   —"
        print(f"{tk:8s} {b:+9.2f} {rs:>9s} {ds:>8s}")
    print(f"{'TOTAL':8s} {booked_tot:+9.2f} {real_tot:+9.2f} {real_tot-booked_tot:+8.2f}")
    print(f"\nWrote {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
