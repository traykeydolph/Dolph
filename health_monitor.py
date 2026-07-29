#!/usr/bin/env python3
"""External health monitor for the trading bot.

Runs independently of the bot (via a systemd timer / cron, every few minutes)
so it can report the bot being DOWN. Probes the things that can fail *silently* —
the ones the bot itself won't notice until it's too late:

  • heartbeat  — the poll loop writes trading_bot.heartbeat each cycle; stale or
                 missing = process dead OR loop hung.
  • alpaca     — account reachable (auth/API broken → next trade would fail).
  • gemini     — the fallback that died silently for days in July and nobody knew.
  • database   — real read + write probe (disk full / locked).

Alerts to Telegram on a NEW failure, again on RECOVERY, and once a day as an
"all healthy" heartbeat so silence is meaningful. Dedups via .health_state.json
so it never spams. Exit code 0 = all healthy, 1 = something failing.

Usage:  ./venv/bin/python health_monitor.py
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from config import Config  # noqa: E402

HEARTBEAT_FILE = os.path.join(HERE, "trading_bot.heartbeat")
PID_FILE = os.path.join(HERE, "trading_bot.pid")
STATE_FILE = os.path.join(HERE, ".health_state.json")
HEARTBEAT_MAX_AGE = 180  # s — bot writes every ~15s; 3 min is generous slack


# ── individual checks: each returns (ok: bool, detail: str) ─────────

def check_heartbeat():
    """Process + poll-loop liveness in one signal."""
    if not os.path.exists(HEARTBEAT_FILE):
        # Distinguish "never started" from "was running, file gone"
        running = os.path.exists(PID_FILE)
        return False, ("no heartbeat file — bot not running"
                       + (" (pidfile present — crashed?)" if running else ""))
    age = time.time() - os.path.getmtime(HEARTBEAT_FILE)
    if age > HEARTBEAT_MAX_AGE:
        return False, f"heartbeat STALE ({age:.0f}s > {HEARTBEAT_MAX_AGE}s) — loop hung or process dead"
    return True, f"fresh ({age:.0f}s ago)"


def check_alpaca(cfg):
    try:
        import alpaca_trade_api as tradeapi
        api = tradeapi.REST(cfg.alpaca_api_key, cfg.alpaca_secret_key,
                            cfg.alpaca_base_url, api_version="v2")
        acct = api.get_account()
        return True, f"{acct.account_number} {acct.status}"
    except Exception as e:  # noqa: BLE001
        return False, str(e).splitlines()[0][:140]


def check_gemini(cfg):
    try:
        from parsers.gemini_parser import GeminiParser
        return GeminiParser(cfg).health_check()
    except Exception as e:  # noqa: BLE001
        return False, str(e).splitlines()[0][:140]


def check_database(cfg):
    """Real read + write probe — catches disk-full / locked, not just presence."""
    try:
        conn = sqlite3.connect(cfg.db_path, timeout=5)
        conn.execute("SELECT 1").fetchone()
        conn.execute("CREATE TABLE IF NOT EXISTS _health_probe (t TEXT)")
        conn.execute("INSERT INTO _health_probe VALUES (?)",
                     (datetime.now(timezone.utc).isoformat(),))
        conn.execute("DROP TABLE _health_probe")   # leave no schema residue
        conn.commit()
        conn.close()
        return True, "read + write ok"
    except Exception as e:  # noqa: BLE001
        return False, str(e).splitlines()[0][:140]


# ── notify + state ─────────────────────────────────────────────────

def telegram(cfg, text):
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage",
            json={"chat_id": cfg.telegram_chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15)
        return r.status_code == 200
    except requests.RequestException:
        return False


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:  # noqa: BLE001
        pass


def main():
    dry_run = "--dry-run" in sys.argv
    cfg = Config()
    checks = {
        "heartbeat": check_heartbeat(),
        "alpaca": check_alpaca(cfg),
        "gemini": check_gemini(cfg),
        "database": check_database(cfg),
    }
    failing = {k: detail for k, (ok, detail) in checks.items() if not ok}

    state = load_state()
    prev = set(state.get("failing", []))
    now = set(failing)
    new_fail = now - prev
    recovered = prev - now
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    alerts = []
    if new_fail:
        alerts.append("🚨 <b>TRADING BOT — HEALTH FAILURE</b>\n"
                      + "\n".join(f"❌ <b>{k}</b>: {failing[k]}" for k in sorted(new_fail)))
    if recovered:
        alerts.append("✅ <b>Recovered</b>: " + ", ".join(sorted(recovered)))
    if not now and state.get("last_ok_heartbeat") != today:
        alerts.append("✅ <b>Trading bot healthy</b> — all systems green\n"
                      + " · ".join(sorted(checks)))
        state["last_ok_heartbeat"] = today

    for msg in alerts:
        if dry_run:
            print("[dry-run] would send Telegram:\n" + msg + "\n")
        else:
            telegram(cfg, msg)

    if not dry_run:
        state["failing"] = sorted(now)
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        save_state(state)

    # stdout for manual runs / journalctl
    for k, (ok, detail) in checks.items():
        print(f"{'✅' if ok else '❌'} {k:9s} {detail}")
    return 0 if not now else 1


if __name__ == "__main__":
    sys.exit(main())
