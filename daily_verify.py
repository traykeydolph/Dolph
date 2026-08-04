#!/usr/bin/env python3
"""Automated daily verification gate — the objective 30-day streak counter.

Runs once after market close (systemd timer). It turns the manual EOD review
into a same-day, automatic pass/fail:

  1. refresh the fill reconciliation + Waxui shadow P&L caches,
  2. verify the day was CLEAN:
       • zero parser errors (the Gate-1 tripwire),
       • no failed/unverified trades,
       • DB positions in sync with Alpaca,
       • today's P&L reconciles to the real fills (Blocker 2 stays fixed),
  3. emit a CLEAN/DIRTY verdict, update the consecutive-clean-day streak
     (toward 30; a DIRTY day resets it to 0), and Telegram the summary,
  4. refresh the dashboard.

Only MARKET days count (Alpaca calendar — robust vs weekends AND holidays).
Idempotent: re-running the same day recomputes rather than double-counting.

Usage:  ./venv/bin/python daily_verify.py [--dry-run] [--date YYYY-MM-DD]
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from config import Config                                    # noqa: E402
from daily_report import ct_date, load_log_all, DB_PATH, REPORT_DIR  # noqa: E402
from health_monitor import telegram                          # noqa: E402

CT = ZoneInfo("America/Chicago")
STATE_FILE = os.path.join(HERE, ".verify_state.json")
STREAK_GOAL = 30
PNL_DELTA_TOLERANCE = 2.0   # $ — post-Blocker-2, a day's lifecycles reconcile to ~0
FAIL_STATUSES = {"failed", "exit_failed", "trim_failed", "timeout", "executed_unverified"}


def _ro(cfg):
    conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def refresh(script, timeout=150):
    """Run a sibling tool to refresh its cache. Returns (ok, last_line)."""
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, script)],
                           capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or r.stderr).strip().splitlines()
        return r.returncode == 0, (out[-1] if out else "")
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:120]


def is_market_day(cfg, day):
    try:
        from execution.alpaca_client import AlpacaClient
        return len(AlpacaClient(cfg).api.get_calendar(start=day, end=day)) > 0
    except Exception:  # noqa: BLE001 — fall back to weekday
        return datetime.strptime(day, "%Y-%m-%d").weekday() < 5


# ── checks: each returns (ok, detail) ──────────────────────────────

def check_parser_errors(day):
    perr = [e for e in load_log_all().get(day, [])
            if e["level"] == "ERROR" and e["parse_related"]]
    return (not perr, f"{len(perr)} parser ERROR(s): "
            + "; ".join(e["msg"][:60] for e in perr[:3]) if perr else "none")


def check_execution_failures(cfg, day):
    conn = _ro(cfg)
    bad = [f"{r['ticker']}:{r['status']}"
           for r in conn.execute("SELECT ticker,status,executed_at,created_at FROM trades")
           if (ct_date(r["executed_at"]) or ct_date(r["created_at"])) == day
           and r["status"] in FAIL_STATUSES]
    conn.close()
    return (not bad, ", ".join(bad) if bad else "none")


def check_position_sync(cfg):
    from execution.alpaca_client import AlpacaClient
    conn = _ro(cfg)
    db_tickers = [r["ticker"].upper() for r in
                  conn.execute("SELECT ticker FROM positions WHERE status='open'")]
    conn.close()
    ap_syms = [p.symbol for p in AlpacaClient(cfg).api.list_positions()]
    ghost = [t for t in db_tickers if not any(s.startswith(t) for s in ap_syms)]
    orphan = [s for s in ap_syms if not any(s.startswith(t) for t in db_tickers)]
    ok = not ghost and not orphan and len(db_tickers) == len(ap_syms)
    detail = f"DB {len(db_tickers)} / Alpaca {len(ap_syms)}"
    if ghost:
        detail += f" · ghost(DB-only) {ghost}"
    if orphan:
        detail += f" · orphan(Alpaca-only) {orphan}"
    return ok, detail


def _lifecycles_closed_today(cfg, day):
    conn = _ro(cfg)
    ids = {str(r["id"]) for r in conn.execute(
        "SELECT id,closed_at FROM positions WHERE status='closed'")
        if ct_date(r["closed_at"]) == day}
    conn.close()
    return ids


def check_pnl_integrity(cfg, day):
    """Today's closed lifecycles must book ≈ their real Alpaca fills."""
    cache = _load(os.path.join(REPORT_DIR, "fills_cache.json"))
    if not cache:
        return True, "no reconciliation cache (skipped)"
    today = _lifecycles_closed_today(cfg, day)
    breaches = [f"{pr.get('symbol', '?')} Δ{pr['delta']:+.0f}"
                for pid, pr in cache.get("position_real", {}).items()
                if pid in today and pr.get("matched")
                and abs(pr.get("delta", 0)) > PNL_DELTA_TOLERANCE]
    return (not breaches, ", ".join(breaches) if breaches else "booked ≈ real")


def day_summary(cfg, day):
    """Human numbers for the alert: real P&L today, lifecycles, Waxui as-if-live."""
    cache = _load(os.path.join(REPORT_DIR, "fills_cache.json")) or {}
    today = _lifecycles_closed_today(cfg, day)
    real = booked = 0.0
    for pid, pr in cache.get("position_real", {}).items():
        if pid in today:
            booked += pr.get("booked_pnl", 0)
            if pr.get("matched"):
                real += pr.get("real_pnl", 0)
    spnl = _load(os.path.join(REPORT_DIR, "shadow_pnl_cache.json")) or {}
    waxui = (spnl.get("totals", {}).get("all", {}) or {}).get("ladder")
    return {"n": len(today), "real": round(real, 2), "booked": round(booked, 2),
            "waxui_ladder": waxui}


# ── streak state ───────────────────────────────────────────────────

def compute_streak(history):
    """Consecutive CLEAN market days ending at the most recent verified day."""
    streak = 0
    for h in sorted(history, key=lambda x: x["day"], reverse=True):
        if h["verdict"] == "CLEAN":
            streak += 1
        else:
            break
    return streak


def main():
    dry = "--dry-run" in sys.argv
    cfg = Config()
    day = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--date=")),
               None) or datetime.now(CT).strftime("%Y-%m-%d")

    if not is_market_day(cfg, day):
        print(f"{day} is not a market day — nothing to verify.")
        return 0

    # 1. refresh caches (best-effort; a network blip shouldn't crash the gate)
    for script in ("reconcile_fills.py", "shadow_pnl.py"):
        ok, tail = refresh(script)
        print(f"{'✅' if ok else '⚠️ '} refreshed {script}: {tail}")

    # 2. checks
    checks = {
        "parser-errors": check_parser_errors(day),
        "exec-failures": check_execution_failures(cfg, day),
        "position-sync": check_position_sync(cfg),
        "pnl-integrity": check_pnl_integrity(cfg, day),
    }
    dirty = {k: d for k, (ok, d) in checks.items() if not ok}
    verdict = "DIRTY" if dirty else "CLEAN"

    # 3. streak (idempotent: replace any prior entry for `day`)
    state = _load(STATE_FILE) or {"history": []}
    history = [h for h in state.get("history", []) if h["day"] != day]
    history.append({"day": day, "verdict": verdict, "reasons": dirty})
    streak = compute_streak(history)

    summ = day_summary(cfg, day)

    # 4. compose + alert
    head = ("✅ <b>CLEAN</b>" if verdict == "CLEAN" else "🚨 <b>DIRTY</b>")
    lines = [f"📋 <b>Daily Verification — {day}</b>",
             f"{head} · streak <b>{streak}/{STREAK_GOAL}</b>"]
    if dirty:
        lines += [f"❌ <b>{k}</b>: {v}" for k, v in dirty.items()]
    lines.append(f"Real P&amp;L today: {summ['real']:+.2f} (booked {summ['booked']:+.2f}) · "
                 f"{summ['n']} lifecycle(s)")
    if summ["waxui_ladder"] is not None:
        lines.append(f"Waxui as-if-live (laddered, all-time): {summ['waxui_ladder']:+.2f}")
    lines.append("Checks: " + " · ".join(
        f"{k} {'✅' if (ok) else '❌'}" for k, (ok, _) in checks.items()))
    msg = "\n".join(lines)

    if dry:
        print("\n[dry-run] would send Telegram:\n" + msg.replace("<b>", "").replace("</b>", "")
              .replace("&amp;", "&"))
    else:
        telegram(cfg, msg)
        state["history"] = sorted(history, key=lambda x: x["day"])
        state["streak"] = streak
        state["last_run"] = datetime.now(CT).isoformat()
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        refresh("daily_report.py")   # keep the dashboard current

    print(f"\n{day}: {verdict} · streak {streak}/{STREAK_GOAL}")
    for k, (ok, d) in checks.items():
        print(f"  {'✅' if ok else '❌'} {k:14s} {d}")
    return 0 if verdict == "CLEAN" else 1


if __name__ == "__main__":
    sys.exit(main())
