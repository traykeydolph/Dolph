---
name: start-bot
description: |
  Launch the trading bot safely with the full pre-flight runbook: stale-pidfile
  check, test suite, background launch, and startup health-check + routing
  verification. Use when asked to "start the bot", "launch the bot", "turn the
  bot on", or "run the bot".
allowed-tools:
  - Bash
  - Read
---

# Start the trading bot

Follow these steps in order. Work in `~/Desktop/trading`. **Never skip the
pidfile check** — a stale `trading_bot.pid` from a dead process was the root
cause of a week-long "outage", so always confirm clean state first.

## 1. Confirm clean state (no bot already running, no stale pidfile)
```bash
cd ~/Desktop/trading
if [ -f trading_bot.pid ]; then
  echo "pidfile exists: $(cat trading_bot.pid)"
  ps -p $(cat trading_bot.pid) -o pid,stat,command 2>/dev/null \
    || echo "  -> PID NOT running = STALE pidfile"
else
  echo "no pidfile ✓"
fi
ps aux | grep "[P]ython main.py" || echo "no main.py process running ✓"
```
- If a **real** process is running, stop — the bot is already up; do not launch a second one.
- If the pidfile is **stale** (file exists but PID not running), remove it: `rm trading_bot.pid`, then continue.

## 2. Pre-flight tests
```bash
./venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
```
Expect **~367 passed**. If tests fail, do NOT launch — report the failure.

## 3. Launch (background)
Launch as a background process so it keeps running:
```bash
cd ~/Desktop/trading && ./venv/bin/python main.py
```
(Use the harness background-run mode; the bot writes to `trading_bot.log`.)

## 4. Verify startup (wait ~6s, then read the log)
```bash
sleep 6
cat trading_bot.pid 2>/dev/null && echo " (pidfile present)"
tail -40 trading_bot.log
```
Confirm all of:
- **pidfile present** with a live PID
- **Health check — 6 green items:** Discord token valid · Alpaca connected
  (account `PA3OQ9Y8K2X7`) · Database OK · Telegram alerter OK · Obsidian Signal
  Library loaded · Positions in sync
- **All 3 channels seeded/polling:**
  - eva `1035245170582626334` → EXECUTE
  - ace `1478050123786485831` → EXECUTE
  - waxui `1347238168109387857` → SHADOW (log-only, never orders)
- Routing comes from `.env` (`ENABLED_ANALYSTS=eva,ace`, `SHADOW_ANALYSTS=waxui`) —
  there is no dedicated routing log line; confirm the `.env` values if in doubt.

## 5. Report
State the PID, the 6 health items, and one Gate-1 note: compare Ace's seeded
message ID to the previous session's — if identical, **Ace still hasn't posted**
(the first-Ace-lifecycle milestone is still pending). Remind that any parser
error on Eva/Ace resets the Gate-1 clock.
