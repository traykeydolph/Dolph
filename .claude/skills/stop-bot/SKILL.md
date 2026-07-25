---
name: stop-bot
description: |
  Stop the trading bot cleanly via its pidfile and confirm graceful shutdown.
  Use when asked to "stop the bot", "kill the bot", "shut down the bot", or
  "turn the bot off".
allowed-tools:
  - Bash
---

# Stop the trading bot

Always stop via the **pidfile + SIGTERM** so the bot shuts down gracefully
(closes async tasks, removes its own pidfile). Work in `~/Desktop/trading`.

**Do NOT** use `pgrep -f "venv/bin/python main.py"` — the process shows in `ps`
as `.../Python main.py`, so that pattern matches nothing and the bot keeps running.

## 1. Send SIGTERM via the pidfile
```bash
cd ~/Desktop/trading
cat trading_bot.pid 2>/dev/null || echo "NO pidfile — is the bot running?"
kill -TERM $(cat trading_bot.pid) 2>&1; echo "kill exit: $?"
```

## 2. Wait for graceful shutdown, then confirm
```bash
cd ~/Desktop/trading
for i in $(seq 1 15); do [ ! -f trading_bot.pid ] && { echo "pidfile gone after ~${i}s"; break; }; sleep 1; done
[ -f trading_bot.pid ] && echo "STILL EXISTS: $(cat trading_bot.pid)" || echo "pidfile removed ✓"
tail -5 trading_bot.log
```
Confirm both:
- **pidfile removed** and the PID is gone (`ps -p <pid>` returns nothing)
- Log shows **`Bot stopped.`**

**Known quirk:** `Bot stopped.` (and `Shutting down trading bot...`) may appear
**twice** — the double-teardown-on-SIGTERM issue. It is harmless/idempotent
(documented open item, fix deferred to the VPS phase). Note it but don't act on it.

## If it doesn't stop
If the pidfile is still present and the process still alive after ~15s, read the
log tail to see whether shutdown is in progress. Only escalate to `kill -9` as a
last resort, and report that graceful shutdown failed (that's a real finding).
