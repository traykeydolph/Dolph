# VPS Deployment Runbook

Get the bot running always-on on a Linux VPS with auto-restart-on-crash and a
health monitor that pages you on Telegram when anything breaks (Discord, Alpaca,
Gemini, DB, or the poll loop itself). Built after the workstation proved fragile:
a network outage (07-23), manual starts, and a **silently-dead Gemini key** that
went unnoticed for days — the monitor exists so that never happens again.

The bot only makes **outbound** connections (Discord, Alpaca, Telegram, Gemini,
Coinbase). No inbound ports — keep the firewall closed to inbound.

---

## 0. Provision

- Any small Linux VPS is plenty (1 vCPU / 1 GB RAM). Ubuntu 22.04/24.04 or Debian 12.
- The bot is CPU-light (polls every ~15s) but must be **always-on** — that's the point.
- Note the box's timezone; the app logs in CT and stores UTC (see §6). Set the
  host to UTC to avoid surprises: `sudo timedatectl set-timezone UTC`.

## 1. System packages

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
python3 --version    # need 3.11+ (local dev is on 3.14; 3.11/3.12 are fine)
```

## 2. Get the code

```bash
sudo useradd -m -s /bin/bash trader      # dedicated non-root user (recommended)
sudo su - trader
git clone https://github.com/traykeydolph/Dolph.git ~/trading
cd ~/trading
git checkout fix/missed-exit-on-restart  # or main, once merged
```

## 3. Python environment

```bash
cd ~/trading
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
./venv/bin/python -m pytest tests/ -q     # sanity: expect ~410 passed
```

## 4. Secrets — copy `.env` securely (it is gitignored, by design)

`.env` holds the Discord user token, Alpaca keys, Telegram + Gemini keys. It is
**not** in git. Copy it from your workstation:

```bash
# from your Mac (NOT on the VPS):
scp ~/Desktop/trading/.env trader@YOUR_VPS_IP:~/trading/.env
```

Then on the VPS confirm routing:

```bash
cd ~/trading
grep -E "^ENABLED_ANALYSTS|^SHADOW_ANALYSTS|^GEMINI_API_KEY" .env
./venv/bin/python -c "from config import Config; c=Config(); print('enabled:', c.enabled_analysts, '| shadow:', c.shadow_analysts)"
```

Expect `enabled: {'eva','ace'} | shadow: {'waxui'}`.

## 5. Install the systemd services

```bash
exit                       # back to a sudo-capable user
cd /home/trader/trading
sudo ./deploy/install.sh   # templates USER+APP_DIR, installs 3 units, enables them
```

This installs and **enables** (start-on-boot):
- `trading-bot.service` — the bot, `Restart=on-failure` (a clean stop stays stopped).
- `trading-bot-health.timer` → `-health.service` — the health monitor, every 5 min.
- `trading-bot-verify.timer` → `-verify.service` — the daily verification gate,
  weekdays 21:30 UTC (≥30 min after the 15:00 CT close). It refreshes the
  reconciliation + Waxui shadow P&L, checks the day was CLEAN (zero parser errors,
  no failed trades, DB↔Alpaca in sync, P&L reconciles to real fills), Telegrams a
  CLEAN/DIRTY verdict, and advances the consecutive-clean-day streak toward 30.
  A DIRTY day resets the streak. Only Alpaca-calendar market days count.

It does **not** start the bot yet — you do that once you've eyeballed `.env`.

## 6. Launch + verify

```bash
sudo systemctl start trading-bot
journalctl -u trading-bot -f          # watch the startup health check
```

Confirm all of:
- Health check **7 green**: Discord token · Alpaca (`PA3OQ9Y8K2X7`) · Database ·
  Telegram · **Gemini fallback reachable** · Obsidian library · Positions in sync.
- Routing: `eva → execute`, `ace → execute`, `waxui → SHADOW`.
- Cursors **resume from persisted** (no "seeding to latest" unless first-ever run).

Then confirm the monitor is live:

```bash
systemctl list-timers trading-bot-health.timer   # shows next run
./venv/bin/python health_monitor.py --dry-run    # run it by hand; --dry-run = no Telegram
```

You should get a Telegram "✅ Trading bot healthy" heartbeat within a day, and an
immediate 🚨 if any component fails.

## 7. Day-to-day operations

```bash
# status / logs
systemctl status trading-bot
journalctl -u trading-bot -f                 # live systemd view
tail -f ~/trading/trading_bot.log            # the bot's own rotating log

# stop / start / restart (SIGTERM = graceful; bot removes its pidfile)
sudo systemctl stop trading-bot
sudo systemctl restart trading-bot

# manual health check
cd ~/trading && ./venv/bin/python health_monitor.py

# manual daily verification (CLEAN/DIRTY verdict + streak; --dry-run = no Telegram)
./venv/bin/python daily_verify.py --dry-run
systemctl list-timers 'trading-bot-*'          # next scheduled runs

# EOD review / dashboards still work exactly the same
./venv/bin/python reconcile_fills.py
./venv/bin/python shadow_pnl.py
./venv/bin/python daily_report.py            # writes reports/latest.html
```

## 8. Deploying code updates

```bash
sudo systemctl stop trading-bot
sudo su - trader
cd ~/trading && git pull && ./venv/bin/pip install -r requirements.txt
./venv/bin/python -m pytest tests/ -q
exit
sudo systemctl start trading-bot
```

## Notes / gotchas

- **Discord user token is ToS-fragile.** Don't lower `POLLING_INTERVAL` below ~3–5s;
  aggressive REST polling raises self-bot-detection risk. Real low-latency (0DTE
  Waxui) wants a gateway websocket, not faster polling — that's a separate project.
- **Continuous vs market-hours.** These units run the bot 24/7 (Discord is quiet
  off-hours; simplest and most reliable). If you'd rather start/stop at market
  hours to cut off-hours token exposure, add `OnCalendar` timers — ask and I'll
  write them. If you do that, the health monitor's heartbeat check will (correctly)
  alert when the bot is intentionally down, so you'd gate it on market hours too.
- **Double `Bot stopped.`** on shutdown is a known-harmless double-teardown; fix
  is deferred. It doesn't affect systemd.
- **`Restart=on-failure`** means a *clean* stop stays stopped (good). A crash
  restarts after 10s; 5 crashes in 5 min trips the limit and the health monitor
  pages you instead of thrashing.
- **Timezone:** keep the host on UTC. `trading_bot.log` timestamps are CT by the
  app's own config (`TIMEZONE` in .env); the DB stores UTC. The EOD tooling is
  CT/UTC-aware regardless.
