#!/usr/bin/env python3
"""Position sync tool — reconciles DB positions vs Alpaca broker positions.

Detects ghost positions (in DB but not on broker) and orphaned positions
(on broker but not in DB). Optionally fixes them.

Usage:
    ./venv/bin/python sync_positions.py           # Dry run — report only
    ./venv/bin/python sync_positions.py --fix      # Fix mismatches
"""

import argparse
import logging
import sys
from datetime import datetime, timezone

from config import Config
from storage.database import Database
from execution.alpaca_client import AlpacaClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("sync")


def sync(fix: bool = False):
    config = Config()
    db = Database(config.db_path)

    # Connect to Alpaca
    try:
        alpaca = AlpacaClient(config)
    except Exception:
        logger.error("Failed to connect to Alpaca — cannot sync")
        sys.exit(1)

    # Get positions from both sources
    db_positions = db.get_open_positions()
    alpaca_positions = alpaca.list_open_positions()

    # Build lookup sets
    # DB positions keyed by ticker (simplified — doesn't handle multi-position per ticker)
    db_tickers = {}
    for pos in db_positions:
        key = f"{pos['analyst']}_{pos['ticker']}"
        db_tickers[key] = pos

    # Alpaca positions keyed by symbol
    alpaca_symbols = {}
    for pos in alpaca_positions:
        alpaca_symbols[pos.symbol] = {
            "symbol": pos.symbol,
            "qty": int(pos.qty),
            "side": pos.side,
            "unrealized_pl": float(pos.unrealized_pl),
            "current_price": float(pos.current_price),
            "avg_entry_price": float(pos.avg_entry_price),
        }

    print("\n" + "=" * 70)
    print("POSITION SYNC REPORT")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)

    print(f"\nDB open positions:     {len(db_positions)}")
    print(f"Alpaca open positions: {len(alpaca_positions)}")

    # ── Ghost positions (in DB, not on Alpaca) ────────────────────
    ghosts = []
    for key, pos in db_tickers.items():
        ticker = pos["ticker"].upper()
        # Check if any Alpaca position matches this ticker
        found = any(
            sym.startswith(ticker) for sym in alpaca_symbols
        )
        if not found:
            ghosts.append(pos)

    if ghosts:
        print(f"\n🔴 GHOST POSITIONS (in DB, NOT on Alpaca): {len(ghosts)}")
        print("-" * 50)
        for pos in ghosts:
            print(f"  {pos['analyst']:15s} | {pos['ticker']:6s} | "
                  f"qty={pos['current_quantity']} | entry=${pos.get('entry_price', 0) or 0:.2f} | "
                  f"opened={pos.get('opened_at', '?')[:10]}")
            if fix:
                logger.info("Closing ghost position %d (%s %s) in DB",
                           pos["id"], pos["analyst"], pos["ticker"])
                db.close_position(pos["id"], total_pnl=0)
                print(f"    → FIXED: Closed in DB (P&L unknown — was already gone from Alpaca)")
    else:
        print("\n✅ No ghost positions")

    # ── Orphaned positions (on Alpaca, not tracked in DB) ─────────
    orphans = []
    db_ticker_set = {pos["ticker"].upper() for pos in db_positions}
    for sym, data in alpaca_symbols.items():
        # Extract base ticker from option symbol (e.g., SPY260321C00580000 → SPY)
        base_ticker = ""
        for i, ch in enumerate(sym):
            if ch.isdigit():
                base_ticker = sym[:i]
                break
        if not base_ticker:
            base_ticker = sym  # Stock symbol

        if base_ticker not in db_ticker_set:
            orphans.append(data)

    if orphans:
        print(f"\n🟡 ORPHANED POSITIONS (on Alpaca, NOT in DB): {len(orphans)}")
        print("-" * 50)
        for orph in orphans:
            print(f"  {orph['symbol']:25s} | qty={orph['qty']} | "
                  f"entry=${orph['avg_entry_price']:.2f} | "
                  f"P&L=${orph['unrealized_pl']:.2f}")
            if fix:
                print(f"    → NOTE: Cannot auto-fix orphans — must close manually on Alpaca")
                print(f"             or add to DB with correct analyst attribution")
    else:
        print("\n✅ No orphaned positions")

    # ── Quantity mismatches ───────────────────────────────────────
    mismatches = []
    for key, pos in db_tickers.items():
        ticker = pos["ticker"].upper()
        for sym, data in alpaca_symbols.items():
            if sym.startswith(ticker) and len(sym) > len(ticker):
                db_qty = pos["current_quantity"]
                alpaca_qty = data["qty"]
                if db_qty != alpaca_qty:
                    mismatches.append({
                        "ticker": ticker,
                        "db_qty": db_qty,
                        "alpaca_qty": alpaca_qty,
                        "symbol": sym,
                        "db_pos": pos,
                    })

    if mismatches:
        print(f"\n🟠 QUANTITY MISMATCHES: {len(mismatches)}")
        print("-" * 50)
        for mm in mismatches:
            print(f"  {mm['ticker']:6s} | DB qty={mm['db_qty']} | "
                  f"Alpaca qty={mm['alpaca_qty']} ({mm['symbol']})")
            if fix:
                logger.info("Fixing qty mismatch for %s: %d → %d",
                           mm["ticker"], mm["db_qty"], mm["alpaca_qty"])
                db.update_position(mm["db_pos"]["id"],
                                  current_quantity=mm["alpaca_qty"])
                print(f"    → FIXED: DB qty updated to {mm['alpaca_qty']}")
    else:
        print("\n✅ No quantity mismatches")

    # ── Summary ───────────────────────────────────────────────────
    total_issues = len(ghosts) + len(orphans) + len(mismatches)
    print(f"\n{'=' * 70}")
    if total_issues == 0:
        print("✅ ALL POSITIONS IN SYNC")
    else:
        print(f"⚠️ {total_issues} issue(s) found" +
              (" — FIXED" if fix else " — run with --fix to resolve"))
    print("=" * 70 + "\n")

    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync trading bot positions with Alpaca")
    parser.add_argument("--fix", action="store_true",
                       help="Fix mismatches (close ghosts, update quantities)")
    args = parser.parse_args()
    sync(fix=args.fix)
