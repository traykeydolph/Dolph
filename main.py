"""Main orchestrator — polling loop, signal processing, trade execution."""

import asyncio
import logging
import os
import signal
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from config import Config
from discord_poller import DiscordPoller, DiscordMessage
from signal_router import SignalRouter
from parsers.base import ParsedSignal, SignalAction, AssetType
from storage.database import Database
from execution.position_manager import PositionManager
from execution.alpaca_client import AlpacaClient
from execution.spx_converter import SPXConverter
from execution.coinbase_client import CoinbaseClient
from alerts.telegram import TelegramAlerter
from risk.drawdown_manager import DrawdownManager
from integrations.google_sheets import SheetsSync
from integrations.obsidian_journal import open_trade, record_trim, close_trade
from shadow_logger import log_shadow_observation, format_shadow_alert

# ── logging setup ─────────────────────────────────────────────────

def setup_logging(level: str = "INFO"):
    from logging.handlers import RotatingFileHandler
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            RotatingFileHandler(
                "trading_bot.log", encoding="utf-8",
                maxBytes=10 * 1024 * 1024,  # 10MB per file
                backupCount=5,              # Keep 5 rotated logs
            ),
        ],
    )

logger = logging.getLogger("main")

# ── bot ───────────────────────────────────────────────────────────

class TradingBot:
    def __init__(self):
        self.config = Config()
        self.db = Database(self.config.db_path)
        self.poller = DiscordPoller(self.config)
        self.router = SignalRouter(self.config)
        self.position_mgr = PositionManager(self.config, self.db)
        self.spx_converter = SPXConverter()
        self.alerter = TelegramAlerter(self.config)
        self.drawdown_mgr = DrawdownManager(self.config, self.db)
        self._alpaca: Optional[AlpacaClient] = None
        self._coinbase: Optional[CoinbaseClient] = None
        self._running = False
        self._last_daily_reset: Optional[str] = None
        self.sheets = SheetsSync()

    # ── lifecycle ─────────────────────────────────────────────────

    async def start(self):
        """Initialize connections and start the polling loop."""
        setup_logging(self.config.log_level)
        logger.info("Starting trading bot...")

        # Connect to Alpaca (sync init, run in thread)
        try:
            self._alpaca = await asyncio.wait_for(
                asyncio.to_thread(AlpacaClient, self.config), timeout=30
            )
            logger.info("Alpaca client connected")
        except asyncio.TimeoutError:
            logger.error("Alpaca connection timed out (30s) — running without execution")
            await self.alerter.alert_error("Startup", "Alpaca connection timed out. Bot running in monitor-only mode.")
        except Exception:
            logger.exception("Alpaca connection failed — running without execution")
            await self.alerter.alert_error("Startup", "Alpaca connection failed. Bot running in monitor-only mode.")

        # Connect to Coinbase (crypto execution)
        try:
            self._coinbase = CoinbaseClient()
            connected = await asyncio.wait_for(
                asyncio.to_thread(self._coinbase.connect), timeout=30
            )
            if connected:
                usd = await asyncio.wait_for(
                    asyncio.to_thread(self._coinbase.get_usd_balance), timeout=15
                )
                logger.info("Coinbase connected — $%.2f USD available", usd)
            else:
                logger.warning("Coinbase connection failed — crypto execution disabled")
                self._coinbase = None
        except Exception:
            logger.exception("Coinbase initialization failed — crypto execution disabled")
            self._coinbase = None

        # ── Startup health check ──────────────────────────────────
        health = await self._health_check()
        if health["critical_failures"]:
            logger.error("STARTUP ABORTED — critical failures: %s", health["critical_failures"])
            await self.alerter.send_message(
                f"🚨 BOT STARTUP FAILED\n\n"
                f"Critical failures:\n" +
                "\n".join(f"  - {f}" for f in health["critical_failures"])
            )
            return

        # Startup banner
        await self.alerter.alert_startup()

        # Report health status
        warnings = health.get("warnings", [])
        if warnings:
            await self.alerter.send_message(
                f"⚠️ Startup warnings:\n" +
                "\n".join(f"  - {w}" for w in warnings)
            )

        # Start polling loop
        self._running = True
        await self._poll_loop()

    async def stop(self):
        """Graceful shutdown."""
        logger.info("Shutting down trading bot...")
        self._running = False
        await self.alerter.alert_shutdown()
        await self.alerter.close()
        self.db.close()
        logger.info("Bot stopped.")

    # ── health check ─────────────────────────────────────────────

    async def _health_check(self) -> dict:
        """Validate all connections before entering poll loop.
        Returns dict with 'critical_failures' and 'warnings' lists."""
        critical = []
        warnings = []

        # 1. Discord token — try fetching 1 message from any channel
        test_channel = next(
            (ch for ch in self.config.discord_only_channels if ch), None
        )
        if test_channel:
            try:
                import requests
                resp = requests.get(
                    f"{self.config.DISCORD_API_BASE}/channels/{test_channel}/messages",
                    headers={"Authorization": self.config.discord_user_token},
                    params={"limit": 1},
                    timeout=10,
                )
                if resp.status_code == 401:
                    critical.append("Discord token is INVALID (401 Unauthorized)")
                elif resp.status_code == 403:
                    warnings.append(f"Discord channel {test_channel} returned 403 — may be a cancelled subscription")
                elif resp.status_code == 200:
                    logger.info("✅ Discord token valid")
                else:
                    warnings.append(f"Discord returned {resp.status_code} on health check")
            except Exception as e:
                critical.append(f"Discord connection failed: {e}")
        else:
            critical.append("No Discord channels configured")

        # 2. Alpaca
        if self._alpaca:
            try:
                acct = await asyncio.wait_for(
                    asyncio.to_thread(lambda: self._alpaca.api.get_account()),
                    timeout=10,
                )
                bp = float(acct.buying_power)
                logger.info("✅ Alpaca connected — $%.2f buying power", bp)
                if bp < 100:
                    warnings.append(f"Alpaca buying power very low: ${bp:.2f}")
            except Exception as e:
                warnings.append(f"Alpaca health check failed: {e}")
        else:
            warnings.append("Alpaca client not connected — running in monitor-only mode")

        # 3. Database
        try:
            open_positions = self.db.get_open_positions()
            logger.info("✅ Database OK — %d open positions", len(open_positions))
        except Exception as e:
            critical.append(f"Database error: {e}")

        # 4. Telegram alerter
        try:
            # Send a test message to verify bot token + chat ID
            await self.alerter.send_message("🏥 Health check — bot starting up...")
            logger.info("✅ Telegram alerter OK")
        except Exception as e:
            warnings.append(f"Telegram alerter failed: {e}")

        # 5. Obsidian Signal Library
        try:
            from parsers.obsidian_matcher import match as obsidian_match
            # Quick test — try matching a known pattern
            test_result = obsidian_match("TP1 hit", "grizzlies")
            if test_result:
                logger.info("✅ Obsidian Signal Library loaded")
            else:
                logger.info("✅ Obsidian Signal Library accessible (no match for test)")
        except Exception as e:
            warnings.append(f"Obsidian Signal Library error: {e}")

        # 6. Position mismatch check (DB vs broker)
        if self._alpaca:
            try:
                alpaca_positions = await asyncio.wait_for(
                    asyncio.to_thread(self._alpaca.list_open_positions),
                    timeout=10,
                )
                db_count = len(open_positions)
                alpaca_count = len(alpaca_positions)
                if db_count != alpaca_count:
                    warnings.append(
                        f"Position mismatch: DB has {db_count} open, Alpaca has {alpaca_count}. "
                        f"Run sync_positions.py to reconcile."
                    )
                else:
                    logger.info("✅ Positions in sync: %d in DB, %d on Alpaca", db_count, alpaca_count)
            except Exception as e:
                warnings.append(f"Position sync check failed: {e}")

        return {"critical_failures": critical, "warnings": warnings}

    # ── main loop ─────────────────────────────────────────────────

    async def _poll_loop(self):
        """Core polling loop — runs every config.polling_interval seconds."""
        stop_check_counter = 0
        while self._running:
            # Daily drawdown reset at midnight UTC
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._last_daily_reset != today:
                self._last_daily_reset = today
                self.drawdown_mgr.reset_daily()
                logger.info("Daily drawdown counters reset for %s", today)

            try:
                await self._poll_cycle()
            except Exception:
                logger.exception("Unhandled error in poll cycle")
                try:
                    await self.alerter.alert_error("Poll cycle", "Unhandled exception — see logs")
                except Exception:
                    logger.exception("Failed to send error alert")

            # Check stops every 4 poll cycles (~60s at 15s interval)
            stop_check_counter += 1
            if stop_check_counter >= 4:
                stop_check_counter = 0
                try:
                    await self._check_stop_losses()
                except Exception:
                    logger.exception("Error in stop loss check")

            await asyncio.sleep(self.config.polling_interval)

    async def _poll_cycle(self):
        """Single poll cycle: fetch → parse → dedup → execute → alert → log."""
        # 1. Fetch new Discord messages (sync call in thread, 60s timeout)
        try:
            messages: list[DiscordMessage] = await asyncio.wait_for(
                asyncio.to_thread(self.poller.poll), timeout=60
            )
        except asyncio.TimeoutError:
            logger.error("Discord poll timed out (60s) — skipping this cycle")
            await self.alerter.send_message("⚠️ Discord poll timed out (60s) — possible API hang")
            return

        if not messages:
            return

        logger.info("Fetched %d new messages", len(messages))

        for msg in messages:
            try:
                await self._process_message(msg)
            except Exception:
                logger.exception("Error processing message %s", msg.message_id)
                try:
                    await self.alerter.alert_error(
                        f"Message {msg.message_id}",
                        f"Processing failed for: {msg.content[:100]}",
                    )
                except Exception:
                    pass

    # ── per-message processing ────────────────────────────────────

    async def _alert_no_action(self, msg: DiscordMessage, verdict: str):
        """Max-verbosity notification: confirm the bot saw and judged a message
        even when no trade results. Gated by ALERT_NOISE env (default on)."""
        if not self.config.alert_noise:
            return
        analyst = self.config.channel_to_analyst.get(msg.channel_id, "unknown")
        preview = (msg.content or "").strip()
        if not preview and msg.embeds:
            e = msg.embeds[0]
            preview = f"{e.get('title', '')} | {e.get('description', '')}".strip(" |")
        preview = preview[:150] or "(empty message)"
        await self.alerter.send_message(
            f"👁 <b>{analyst.replace('_', ' ').title()}</b> — {verdict}\n{preview}"
        )

    async def _process_message(self, msg: DiscordMessage):
        """Process a single Discord message end-to-end."""

        # 2. Dedup — skip if already processed
        if self.db.is_message_processed(msg.message_id):
            logger.debug("Skipping duplicate message %s", msg.message_id)
            return

        # 2b. Stale signal guard — skip messages older than 5 minutes
        # Prevents executing old signals after bot restart
        if msg.timestamp:
            try:
                msg_time = datetime.fromisoformat(msg.timestamp.replace('Z', '+00:00'))
                age_seconds = (datetime.now(timezone.utc) - msg_time).total_seconds()
                if age_seconds > self.config.stale_signal_seconds:
                    logger.info("⏭️ Skipping stale message (%.0fs old): %s", age_seconds, msg.message_id)
                    self.db.log_message(
                        message_id=msg.message_id, channel_id=msg.channel_id,
                        content=msg.content, parsed_as={"skipped": "stale_signal", "age_seconds": age_seconds},
                    )
                    return
            except Exception:
                pass  # If timestamp parsing fails, process normally

        # 2c. SHADOW / LOG-ONLY channels — observe and return. This branch is
        # the single gate: it returns before step 6's dispatch, so a shadow
        # message can never reach _handle_entry/_handle_trim/_handle_exit and
        # therefore never reaches Alpaca or Coinbase. Asserted in
        # tests/test_shadow_mode.py.
        if self.config.is_shadow_channel(msg.channel_id):
            await self._process_shadow_message(msg)
            return

        # 3. Route & parse via signal router (sync, run in thread, 30s timeout)
        try:
            signal: Optional[ParsedSignal] = await asyncio.wait_for(
                asyncio.to_thread(
                    self.router.route_message,
                    msg.channel_id, msg.message_id, msg.content,
                    msg.timestamp, msg.embeds, msg.referenced_message,
                ),
                timeout=30,
            )
        except asyncio.TimeoutError:
            logger.error("Signal routing timed out (30s) for message %s — likely Gemini hang", msg.message_id)
            self.db.log_message(
                message_id=msg.message_id, channel_id=msg.channel_id,
                content=msg.content, parsed_as={"error": "routing_timeout"},
            )
            return

        # 4. Log the raw message regardless of parse result
        self.db.log_message(
            message_id=msg.message_id,
            channel_id=msg.channel_id,
            content=msg.content,
            parsed_as=asdict(signal) if signal else None,
        )

        if signal is None:
            logger.debug("No actionable signal in message %s", msg.message_id)
            await self._alert_no_action(msg, "no action (noise/unparseable)")
            return

        if signal.action == SignalAction.INFO:
            logger.debug("Info-only signal in message %s", msg.message_id)
            await self._alert_no_action(msg, "info only — no trade")
            return

        logger.info("Signal: %s %s %s from %s (confidence %.2f)",
                     signal.action, signal.ticker, signal.direction or "",
                     signal.analyst, signal.confidence)

        # 5. SPX signals — SKIP until Tastytrade is connected (no conversion hack)
        if signal.ticker and signal.ticker.upper() == "SPX":
            logger.info("⏭️ Skipping SPX signal from %s — SPX disabled until Tastytrade connected", signal.analyst)
            self.db.create_trade(
                analyst=signal.analyst, message_id=signal.message_id,
                action=signal.action, asset_type=signal.asset_type,
                ticker=signal.ticker, direction=signal.direction,
                strike=signal.strike, expiry=signal.expiry,
                entry_price=signal.entry_price, confidence=signal.confidence,
                raw_message=signal.raw_message, status="skipped_spx",
            )
            return

        # 6. Dispatch by action type
        if signal.action == SignalAction.ENTRY:
            await self._handle_entry(signal)
        elif signal.action == SignalAction.TRIM:
            await self._handle_trim(signal)
        elif signal.action in (SignalAction.EXIT, SignalAction.STOP_HIT):
            await self._handle_exit(signal)

    # ── shadow / log-only path ─────────────────────────────────────

    async def _process_shadow_message(self, msg: DiscordMessage):
        """Observe a shadow-channel message: parse, log to JSONL, alert.

        Deliberately does NOT call _handle_entry/_handle_trim/_handle_exit,
        create_trade, or open_position. Nothing here can place an order.
        """
        analyst = self.config.channel_to_analyst.get(msg.channel_id, "unknown")

        try:
            signal, tier = await asyncio.wait_for(
                asyncio.to_thread(
                    self.router.classify_shadow,
                    msg.channel_id, msg.message_id, msg.content,
                    msg.timestamp, msg.embeds, msg.referenced_message,
                ),
                timeout=30,
            )
        except asyncio.TimeoutError:
            logger.error("Shadow classification timed out (30s) for %s", msg.message_id)
            signal, tier = None, "timeout"
        except Exception:
            logger.exception("Shadow classification failed for %s", msg.message_id)
            signal, tier = None, "error"

        path = await asyncio.to_thread(
            log_shadow_observation, analyst, msg.channel_id, msg.message_id,
            msg.content, msg.timestamp, signal, tier,
        )

        # Mark processed so the message isn't re-observed next cycle.
        self.db.log_message(
            message_id=msg.message_id, channel_id=msg.channel_id,
            content=msg.content,
            parsed_as={"shadow": True, "tier": tier, "executed": False,
                       **({"ticker": signal.ticker} if signal else {})},
        )

        logger.info("[SHADOW] %s %s → tier=%s signal=%s (logged to %s, NO ORDER)",
                    analyst, msg.message_id, tier,
                    f"{signal.action} {signal.ticker}" if signal else "none", path)

        if self.config.alert_noise or signal is not None:
            try:
                await self.alerter.send_message(
                    format_shadow_alert(analyst, msg.content, signal, tier)
                )
            except Exception:
                logger.exception("Shadow Telegram alert failed for %s", msg.message_id)

    # ── sheets sync helper ─────────────────────────────────────────

    async def _sync_sheets(self, event: dict):
        """Fire-and-forget sync to Google Sheets after any trade event."""
        try:
            signal = event.get("signal")
            result = event.get("result") or {}
            trade_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "analyst": getattr(signal, "analyst", ""),
                "ticker": getattr(signal, "ticker", ""),
                "action": event.get("action", getattr(signal, "action", "")),
                "direction": getattr(signal, "direction", ""),
                "asset_type": getattr(signal, "asset_type", ""),
                "strike": getattr(signal, "strike", ""),
                "expiry": getattr(signal, "expiry", ""),
                "entry_price": getattr(signal, "entry_price", ""),
                "exit_price": result.get("filled_price", ""),
                "quantity": result.get("filled_qty", ""),
                "pnl": result.get("total_pnl", result.get("trim_pnl", result.get("pnl", ""))),
                "confidence": getattr(signal, "confidence", ""),
                "status": "executed",
                "raw_message": getattr(signal, "raw_message", "")[:120],
            }
            await asyncio.to_thread(self.sheets.log_trade, trade_data)
            positions = self.position_mgr.get_open_positions()
            pos_dicts = [
                {
                    "analyst": p.analyst, "ticker": p.ticker,
                    "direction": p.direction, "entry_price": p.entry_price,
                    "current_quantity": p.current_quantity, "stop_price": p.stop_price,
                    "target_prices": str(p.target_prices or ""),
                    "trim_count": p.trim_count, "opened_at": p.opened_at,
                    "position_size": p.position_size,
                }
                for p in positions
            ]
            await asyncio.to_thread(self.sheets.update_open_positions, pos_dicts)
            await asyncio.to_thread(self.sheets.update_analyst_scoreboard)
        except Exception:
            logger.debug("Sheets sync failed (non-critical)", exc_info=True)

    # ── action handlers ───────────────────────────────────────────

    async def _handle_entry(self, signal: ParsedSignal):
        """Handle a new entry signal."""

        # ── Duplicate position guard ──────────────────────────────
        # Check for exact match (same ticker + strike + expiry) to allow
        # multiple positions in same ticker with different strikes
        existing = self.position_mgr.find_position(signal.ticker, signal.analyst)
        if existing and existing.current_quantity > 0:
            # Allow different strikes/expiries for the same ticker
            same_contract = (
                (existing.strike is None and signal.strike is None) or
                (existing.strike == signal.strike and existing.expiry == signal.expiry)
            )
            if same_contract:
                logger.info("Skipping duplicate entry — %s already has open %s position (%s qty)",
                           signal.analyst, signal.ticker, existing.current_quantity)
                await self.alerter.send_message(
                    f"⚠️ Duplicate entry skipped — {signal.analyst} already holds {signal.ticker}"
                    + (f" {existing.strike}{existing.direction[0].upper() if existing.direction else ''}" if existing.strike else "")
                )
                self.db.create_trade(
                    analyst=signal.analyst, message_id=signal.message_id,
                    action=signal.action, asset_type=signal.asset_type,
                    ticker=signal.ticker, direction=signal.direction,
                    strike=signal.strike, expiry=signal.expiry,
                    entry_price=signal.entry_price, confidence=signal.confidence,
                    raw_message=signal.raw_message, status="skipped_duplicate",
                )
                return
            else:
                logger.info("Different contract for same ticker — allowing: %s %s %s vs existing %s %s",
                           signal.ticker, signal.strike, signal.expiry,
                           existing.strike, existing.expiry)

        # ── PDT safety check ───────────────────────────────────────
        if self._alpaca:
            try:
                acct = await asyncio.to_thread(self._alpaca.get_account_info)
                if acct:
                    # Alpaca exposes daytrade_count via raw account object
                    raw_acct = await asyncio.to_thread(lambda: self._alpaca.api.get_account())
                    dt_count = int(getattr(raw_acct, 'daytrade_count', 0))
                    if dt_count >= 4:
                        logger.warning("⚠️ PDT WARNING: %d/4 day trades used — entry for %s WILL trigger PDT flag",
                                     dt_count, signal.ticker)
                        await self.alerter.send_message(
                            f"⚠️ PDT WARNING — {dt_count}/4 day trades used\n"
                            f"Entering {signal.ticker} may trigger PDT flag if closed today.\n"
                            f"Proceeding (pdt_check=exit) but be aware."
                        )
            except Exception:
                logger.debug("PDT check failed (non-critical)")

        # ── Drawdown risk gate ────────────────────────────────────
        safe, risk_reason = self.drawdown_mgr.check_risk_limits()
        if not safe:
            logger.warning("🚫 RISK BLOCK: %s — %s", signal.ticker, risk_reason)
            await self.alerter.send_message(
                f"🚫 TRADE BLOCKED — {signal.ticker}\n{risk_reason}\n\n"
                f"Analyst: {signal.analyst}"
            )
            self.db.create_trade(
                analyst=signal.analyst, message_id=signal.message_id,
                action=signal.action, asset_type=signal.asset_type,
                ticker=signal.ticker, direction=signal.direction,
                strike=signal.strike, expiry=signal.expiry,
                entry_price=signal.entry_price, confidence=signal.confidence,
                raw_message=signal.raw_message, status="risk_blocked",
            )
            return

        # ── Position sizing with risk enforcement ──────────────────
        position_size = self.position_mgr.calculate_position_size(signal)
        
        # If analyst provided a stop, use risk-based sizing (smaller of the two)
        if signal.stop_price and signal.entry_price:
            risk_size = self.drawdown_mgr.calculate_position_size(
                signal.entry_price, signal.stop_price
            )
            if risk_size < position_size:
                logger.info("Risk-based sizing caps %s: $%.2f → $%.2f (stop $%.6f)",
                           signal.ticker, position_size, risk_size, signal.stop_price)
                position_size = risk_size
        
        # Enforce max position size as % of account
        can_open, size_reason = self.drawdown_mgr.can_open_position(position_size)
        if not can_open:
            logger.warning("🚫 POSITION SIZE BLOCKED: %s — %s", signal.ticker, size_reason)
            await self.alerter.send_message(
                f"🚫 SIZE BLOCKED — {signal.ticker}\n{size_reason}\n"
                f"Analyst: {signal.analyst}"
            )
            self.db.create_trade(
                analyst=signal.analyst, message_id=signal.message_id,
                action=signal.action, asset_type=signal.asset_type,
                ticker=signal.ticker, direction=signal.direction,
                strike=signal.strike, expiry=signal.expiry,
                entry_price=signal.entry_price, confidence=signal.confidence,
                raw_message=signal.raw_message, status="risk_blocked",
            )
            return
        if position_size <= 0:
            logger.warning("Position size is 0 for %s — max positions reached?", signal.ticker)
            await self.alerter.alert_skipped(signal, "Max open positions reached or zero size")
            self.db.create_trade(
                analyst=signal.analyst, message_id=signal.message_id,
                action=signal.action, asset_type=signal.asset_type,
                ticker=signal.ticker, direction=signal.direction,
                strike=signal.strike, expiry=signal.expiry,
                entry_price=signal.entry_price, confidence=signal.confidence,
                raw_message=signal.raw_message, status="skipped",
            )
            return

        # Crypto execution via Coinbase
        if signal.asset_type == AssetType.CRYPTO:
            if self._coinbase is None:
                logger.info("Crypto signal for %s — Coinbase not connected, skipping", signal.ticker)
                await self.alerter.alert_skipped(signal, "Coinbase not connected")
                self.db.create_trade(
                    analyst=signal.analyst, message_id=signal.message_id,
                    action=signal.action, asset_type=signal.asset_type,
                    ticker=signal.ticker, direction=signal.direction,
                    strike=signal.strike, expiry=signal.expiry,
                    entry_price=signal.entry_price, confidence=signal.confidence,
                    raw_message=signal.raw_message, status="skipped",
                )
                return

            # Crypto position sizing: $10/play (configurable)
            crypto_size = self.config.position_size_crypto
            
            order_result = await asyncio.wait_for(
                asyncio.to_thread(self._coinbase.execute_entry_order, signal, crypto_size),
                timeout=30,
            )

            if order_result is None:
                logger.error("Coinbase entry failed for %s", signal.ticker)
                await self.alerter.alert_error("Crypto Order Failed", f"Entry failed for {signal.ticker}")
                self.db.create_trade(
                    analyst=signal.analyst, message_id=signal.message_id,
                    action=signal.action, asset_type=signal.asset_type,
                    ticker=signal.ticker, direction=signal.direction,
                    entry_price=signal.entry_price, confidence=signal.confidence,
                    raw_message=signal.raw_message, status="failed",
                )
                return

            filled_price = order_result.get("filled_price", signal.entry_price or 0)
            filled_qty = order_result.get("filled_qty", 0)

            # Track position
            pos = self.position_mgr.open_position(signal, filled_price, filled_qty)

            self.db.create_trade(
                analyst=signal.analyst, message_id=signal.message_id,
                action=signal.action, asset_type=signal.asset_type,
                ticker=signal.ticker, direction=signal.direction,
                entry_price=signal.entry_price, executed_price=filled_price,
                quantity=filled_qty, position_size=crypto_size,
                confidence=signal.confidence, raw_message=signal.raw_message,
                status="executed",
            )

            # Write Obsidian trade journal
            if pos:
                try:
                    open_trade(
                        trade_id=pos.id, analyst=signal.analyst, ticker=signal.ticker,
                        asset_type=signal.asset_type, direction=signal.direction,
                        entry_price=signal.entry_price, executed_price=filled_price,
                        quantity=filled_qty, position_size=crypto_size,
                        stop_price=signal.stop_price, target_prices=signal.target_prices,
                        message_id=signal.message_id, raw_message=signal.raw_message,
                        confidence=signal.confidence,
                    )
                except Exception:
                    logger.warning("Obsidian journal write failed (entry) — non-critical")

            await self.alerter.alert_new_entry(signal, order_result, filled_qty, crypto_size)
            await self._sync_sheets({"signal": signal, "result": order_result, "action": "entry"})
            return

        # Execute via Alpaca
        if self._alpaca is None:
            logger.error("Alpaca client not available — cannot execute")
            await self.alerter.alert_error("Execution", f"Alpaca offline — missed entry for {signal.ticker}")
            self.db.create_trade(
                analyst=signal.analyst, message_id=signal.message_id,
                action=signal.action, asset_type=signal.asset_type,
                ticker=signal.ticker, direction=signal.direction,
                strike=signal.strike, expiry=signal.expiry,
                entry_price=signal.entry_price, confidence=signal.confidence,
                raw_message=signal.raw_message, status="failed",
            )
            return

        try:
            order_result = await asyncio.wait_for(
                asyncio.to_thread(self._alpaca.execute_entry_order, signal, position_size),
                timeout=60,
            )
        except asyncio.TimeoutError:
            logger.error("Alpaca entry order timed out (60s) for %s — order may be orphaned", signal.ticker)
            await self.alerter.send_message(
                f"🚨 ENTRY TIMEOUT — {signal.ticker}\n"
                f"Alpaca order timed out after 60s. Check Alpaca for orphaned orders."
            )
            self.db.create_trade(
                analyst=signal.analyst, message_id=signal.message_id,
                action=signal.action, asset_type=signal.asset_type,
                ticker=signal.ticker, direction=signal.direction,
                strike=signal.strike, expiry=signal.expiry,
                entry_price=signal.entry_price, confidence=signal.confidence,
                raw_message=signal.raw_message, status="timeout",
            )
            return

        if order_result is None:
            logger.error("Entry order failed for %s", signal.ticker)
            await self.alerter.alert_error("Order Failed", f"Entry order failed for {signal.ticker}")
            self.db.create_trade(
                analyst=signal.analyst, message_id=signal.message_id,
                action=signal.action, asset_type=signal.asset_type,
                ticker=signal.ticker, direction=signal.direction,
                strike=signal.strike, expiry=signal.expiry,
                entry_price=signal.entry_price, confidence=signal.confidence,
                raw_message=signal.raw_message, status="failed",
            )
            return

        filled_qty = order_result.get("filled_qty", order_result.get("quantity", 1))
        filled_price = order_result.get("filled_price", signal.entry_price or 0)

        # Track position
        pos = self.position_mgr.open_position(signal, filled_price, filled_qty)

        # Log trade
        self.db.create_trade(
            analyst=signal.analyst, message_id=signal.message_id,
            action=signal.action, asset_type=signal.asset_type,
            ticker=signal.ticker, direction=signal.direction,
            strike=signal.strike, expiry=signal.expiry,
            entry_price=signal.entry_price, executed_price=filled_price,
            quantity=filled_qty, position_size=position_size,
            confidence=signal.confidence, raw_message=signal.raw_message,
            status="executed",
        )

        # Write Obsidian trade journal
        if pos:
            try:
                open_trade(
                    trade_id=pos.id, analyst=signal.analyst, ticker=signal.ticker,
                    asset_type=signal.asset_type, direction=signal.direction,
                    strike=signal.strike, expiry=signal.expiry,
                    entry_price=signal.entry_price, executed_price=filled_price,
                    quantity=filled_qty, position_size=position_size,
                    stop_price=signal.stop_price, target_prices=signal.target_prices,
                    message_id=signal.message_id, raw_message=signal.raw_message,
                    confidence=signal.confidence,
                )
            except Exception:
                logger.warning("Obsidian journal write failed (entry) — non-critical")

        # Send Telegram alert
        await self.alerter.alert_new_entry(signal, order_result, filled_qty, position_size)
        await self._sync_sheets({"signal": signal, "result": order_result, "action": "entry"})

    async def _handle_trim(self, signal: ParsedSignal):
        """Handle a trim signal."""

        position = self.position_mgr.find_position(signal.ticker, signal.analyst)
        if position is None:
            logger.warning("No open position for trim: %s %s", signal.analyst, signal.ticker)
            await self.alerter.alert_skipped(signal, "No open position to trim")
            self.db.create_trade(
                analyst=signal.analyst, message_id=signal.message_id,
                action=signal.action, asset_type=signal.asset_type,
                ticker=signal.ticker, direction=signal.direction,
                strike=signal.strike, expiry=signal.expiry,
                confidence=signal.confidence, raw_message=signal.raw_message,
                status="skipped",
            )
            return

        # ── FIX: Enrich signal with position data for trims ─────
        if position.strike and not signal.strike:
            signal.strike = position.strike
        if position.expiry and not signal.expiry:
            signal.expiry = position.expiry
        if position.direction and not signal.direction:
            signal.direction = position.direction
        if position.asset_type and not signal.asset_type:
            signal.asset_type = position.asset_type

        # Calculate trim quantity for exit order
        # If holding 1 contract, any trim = sell all (can't sell fractional contracts)
        if position.current_quantity <= 1:
            trim_qty = position.current_quantity
            trim_fraction = 1.0
            logger.info("Single contract position — selling full on trim signal")
        else:
            trim_fraction = signal.trim_fraction or 0.2
            trim_qty = max(1, int(position.current_quantity * trim_fraction))
            trim_qty = min(trim_qty, position.current_quantity)

        # Execute exit for the trimmed portion
        is_crypto = signal.asset_type in (AssetType.CRYPTO, AssetType.CRYPTO.value, 'crypto')
        order_result = None
        try:
            if is_crypto and self._coinbase:
                order_result = await asyncio.wait_for(
                    asyncio.to_thread(self._coinbase.execute_trim, signal.ticker, trim_fraction),
                    timeout=30,
                )
                trim_price = order_result.get("filled_price", 0) if order_result else 0
            elif self._alpaca and not is_crypto:
                order_result = await asyncio.wait_for(
                    asyncio.to_thread(self._alpaca.execute_exit_order, signal, trim_qty),
                    timeout=60,
                )
                trim_price = order_result.get("filled_price", 0) if order_result else 0
        except asyncio.TimeoutError:
            logger.error("Trim order timed out for %s — position UNCHANGED", signal.ticker)
            await self.alerter.send_message(
                f"🚨 TRIM TIMEOUT — {signal.ticker}\nOrder timed out. Check broker for orphaned orders."
            )
            order_result = None
        else:
            trim_price = signal.entry_price or 0

        # If order failed, don't update position — we still hold the contracts
        if order_result is None:
            logger.error("Trim order failed for %s %s — position UNCHANGED", signal.analyst, signal.ticker)
            await self.alerter.send_message(
                f"⚠️ TRIM FAILED — {signal.ticker}\n"
                f"Analyst: {signal.analyst}\n"
                f"Position still open, DB not updated.\n"
                f"{'Coinbase' if is_crypto else 'Alpaca'} sell failed."
            )
            self.db.create_trade(
                analyst=signal.analyst, message_id=signal.message_id,
                action=signal.action, asset_type=signal.asset_type,
                ticker=signal.ticker, direction=signal.direction,
                confidence=signal.confidence, raw_message=signal.raw_message,
                status="trim_failed",
            )
            return
        
        # Verify fill (same pattern as exit fix)
        if order_result.get("filled_qty", 0) == 0 or order_result.get("status") in ("pending", "cancelled", "rejected"):
            logger.error("Trim order not filled (status=%s, filled_qty=%s) for %s — position UNCHANGED",
                        order_result.get("status"), order_result.get("filled_qty"), signal.ticker)
            await self.alerter.send_message(
                f"⚠️ TRIM NOT FILLED — {signal.ticker}\n"
                f"Status: {order_result.get('status')}\n"
                f"Position unchanged."
            )
            self.db.create_trade(
                analyst=signal.analyst, message_id=signal.message_id,
                action=signal.action, asset_type=signal.asset_type,
                ticker=signal.ticker, direction=signal.direction,
                confidence=signal.confidence, raw_message=signal.raw_message,
                status="trim_failed",
            )
            return

        # Update position tracking
        trim_result = self.position_mgr.trim_position(signal, trim_price)
        if trim_result is None:
            logger.error("Position trim failed for %s", signal.ticker)
            await self.alerter.alert_error("Trim Failed", f"Could not trim {signal.ticker}")
            return

        # Enrich trim_result for the alert
        trim_result["entry_price"] = position.entry_price
        trim_result["trim_price"] = trim_price
        trim_result["trim_count"] = position.trim_count + 1

        # Log trade
        self.db.create_trade(
            analyst=signal.analyst, message_id=signal.message_id,
            action=signal.action, asset_type=signal.asset_type,
            ticker=signal.ticker, direction=signal.direction,
            strike=signal.strike, expiry=signal.expiry,
            executed_price=trim_price, quantity=trim_result["trim_quantity"],
            trim_fraction=trim_fraction, pnl=trim_result["trim_pnl"],
            confidence=signal.confidence, raw_message=signal.raw_message,
            status="executed",
        )

        # After first trim, move stop to breakeven (entry price)
        # After subsequent trims, trail stop up to previous target price
        if position.stop_price and position.entry_price:
            new_stop = position.entry_price  # Default: move to breakeven after T1
            
            # If we have target prices, trail stop to the target we just hit minus one
            # e.g. after T2 hit, move stop to T1 price
            if position.target_prices:
                import json as _json
                try:
                    targets = _json.loads(position.target_prices) if isinstance(position.target_prices, str) else position.target_prices
                    trim_num = position.trim_count + 1  # Already incremented above
                    if trim_num >= 2 and len(targets) >= 1:
                        # After T2, stop = T1. After T3, stop = T2. etc.
                        new_stop = targets[min(trim_num - 2, len(targets) - 1)]
                    # After T1, stop = entry (breakeven) — already set above
                except Exception:
                    pass
            
            if new_stop > position.stop_price:
                old_stop = position.stop_price
                self.db.update_position(position.id, stop_price=new_stop)
                position.stop_price = new_stop
                logger.info("Trailing stop: %s %s moved $%.6f → $%.6f",
                           position.analyst, position.ticker, old_stop, new_stop)

        # Write Obsidian trade journal
        try:
            record_trim(
                trade_id=position.id, ticker=signal.ticker, analyst=signal.analyst,
                trim_number=position.trim_count + 1,
                trim_qty=trim_result.get("trim_quantity"),
                trim_price=trim_price,
                trim_pnl=trim_result.get("trim_pnl"),
                remaining_qty=trim_result.get("remaining_quantity"),
                message_id=signal.message_id,
                raw_message=signal.raw_message,
            )
        except Exception:
            logger.warning("Obsidian journal write failed (trim) — non-critical")

        # Send Telegram alert
        await self.alerter.alert_trim(signal, trim_result)
        await self._sync_sheets({"signal": signal, "result": trim_result, "action": "trim"})

    async def _handle_exit(self, signal: ParsedSignal):
        """Handle exit or stop-hit signal."""

        position = self.position_mgr.find_position(signal.ticker, signal.analyst)
        if position is None:
            logger.warning("No open position for exit: %s %s", signal.analyst, signal.ticker)
            await self.alerter.alert_skipped(signal, "No open position to exit")
            self.db.create_trade(
                analyst=signal.analyst, message_id=signal.message_id,
                action=signal.action, asset_type=signal.asset_type,
                ticker=signal.ticker, direction=signal.direction,
                strike=signal.strike, expiry=signal.expiry,
                confidence=signal.confidence, raw_message=signal.raw_message,
                status="skipped",
            )
            return

        # ── FIX: Enrich signal with position data for exits ───────
        # Exit signals often lack strike/expiry/direction (e.g. "Closed SPY here").
        # Use the POSITION data so we can build the correct option symbol.
        if position.strike and not signal.strike:
            signal.strike = position.strike
        if position.expiry and not signal.expiry:
            signal.expiry = position.expiry
        if position.direction and not signal.direction:
            signal.direction = position.direction
        if position.asset_type and not signal.asset_type:
            signal.asset_type = position.asset_type

        # Execute full exit
        order_result = None
        is_crypto = signal.asset_type in (AssetType.CRYPTO, AssetType.CRYPTO.value, 'crypto')
        try:
            if is_crypto and self._coinbase:
                order_result = await asyncio.wait_for(
                    asyncio.to_thread(self._coinbase.execute_exit, signal.ticker),
                    timeout=30,
                )
                exit_price = order_result.get("filled_price", 0) if order_result else 0
            elif self._alpaca and not is_crypto:
                order_result = await asyncio.wait_for(
                    asyncio.to_thread(self._alpaca.execute_exit_order, signal, position.current_quantity),
                    timeout=60,
                )
                exit_price = order_result.get("filled_price", 0) if order_result else 0
        except asyncio.TimeoutError:
            logger.error("Exit order timed out for %s — attempting force close", signal.ticker)
            await self.alerter.send_message(
                f"🚨 EXIT TIMEOUT — {signal.ticker}\nOrder timed out. Attempting force close..."
            )
            order_result = None  # Falls through to force-close retry below
        else:
            exit_price = signal.entry_price or 0

        # ── FIX: Check if order actually filled (not just submitted) ──
        if order_result and order_result.get("status") in ("pending", "cancelled", "rejected", "expired"):
            logger.warning("Exit order not filled (status=%s) for %s %s — treating as failed",
                          order_result.get("status"), signal.analyst, signal.ticker)
            order_result = None  # Trigger force-close retry below

        if order_result and order_result.get("filled_qty", 0) == 0:
            logger.warning("Exit order filled_qty=0 for %s %s — treating as failed",
                          signal.analyst, signal.ticker)
            order_result = None  # Trigger force-close retry below

        # If exit order failed, try force-closing at current market price
        if order_result is None and (is_crypto or (self._alpaca and not is_crypto)):
            logger.warning("Primary exit failed for %s %s — attempting force close", signal.analyst, signal.ticker)
            
            if not is_crypto and self._alpaca and position:
                try:
                    # Method 1: Alpaca's native force-close on matching positions (most reliable)
                    alpaca_positions = await asyncio.to_thread(self._alpaca.list_open_positions)
                    ticker_upper = signal.ticker.upper()
                    for ap in alpaca_positions:
                        if ap.symbol.startswith(ticker_upper) and len(ap.symbol) > len(ticker_upper):
                            order_result = await asyncio.to_thread(
                                self._alpaca.force_close_by_symbol, ap.symbol,
                            )
                            if order_result:
                                logger.info("Force-closed %s via Alpaca API", ap.symbol)
                                break
                    
                    # Method 2: If no position found on Alpaca, rebuild symbol from position data
                    if order_result is None and position.strike and position.expiry:
                        retry_signal = ParsedSignal(
                            analyst=signal.analyst, action=signal.action,
                            asset_type=signal.asset_type, ticker=signal.ticker,
                            direction=position.direction, strike=position.strike,
                            expiry=position.expiry, entry_price=signal.entry_price,
                            confidence=signal.confidence, raw_message=signal.raw_message,
                            message_id=signal.message_id,
                        )
                        order_result = await asyncio.to_thread(
                            self._alpaca.execute_exit_order, retry_signal, position.current_quantity,
                        )
                        # Verify this retry actually filled
                        if order_result and order_result.get("filled_qty", 0) == 0:
                            logger.warning("Retry exit also got filled_qty=0 — force close failed")
                            order_result = None
                except Exception:
                    logger.exception("Force close retry also failed for %s", signal.ticker)
            
            if order_result is None:
                # Truly failed — DO NOT close in DB, alert user for manual intervention
                logger.error("🚨 ALL EXIT ATTEMPTS FAILED for %s %s — position STILL OPEN on exchange",
                           signal.analyst, signal.ticker)
                await self.alerter.send_message(
                    f"🚨 EXIT FAILED — MANUAL ACTION REQUIRED\n\n"
                    f"Ticker: {signal.ticker}\n"
                    f"Analyst: {signal.analyst}\n"
                    f"Qty: {position.current_quantity}\n"
                    f"Strike: {position.strike} {position.direction}\n"
                    f"Expiry: {position.expiry}\n\n"
                    f"⚠️ Position is STILL OPEN on Alpaca. DB NOT updated.\n"
                    f"Please close manually on Alpaca and run position sync."
                )
                self.db.create_trade(
                    analyst=signal.analyst, message_id=signal.message_id,
                    action=signal.action, asset_type=signal.asset_type,
                    ticker=signal.ticker, direction=signal.direction,
                    strike=signal.strike, expiry=signal.expiry,
                    confidence=signal.confidence, raw_message=signal.raw_message,
                    status="exit_failed",
                )
                return  # DO NOT close the position in DB — it's still live
            else:
                exit_price = order_result.get("filled_price", 0)

        # ── FIX: Post-exit verification — confirm position is gone from Alpaca ──
        if not is_crypto and self._alpaca:
            try:
                await asyncio.sleep(2)  # Give Alpaca a moment to process
                alpaca_positions = await asyncio.to_thread(self._alpaca.list_open_positions)
                ticker_upper = signal.ticker.upper()
                still_open = [ap for ap in alpaca_positions 
                            if ap.symbol.startswith(ticker_upper) and len(ap.symbol) > len(ticker_upper)]
                if still_open:
                    logger.warning("⚠️ POST-EXIT CHECK: %s still has %d open position(s) on Alpaca after exit!",
                                 signal.ticker, len(still_open))
                    await self.alerter.send_message(
                        f"⚠️ Exit verification warning — {signal.ticker} may still be open on Alpaca.\n"
                        f"Positions found: {', '.join(ap.symbol for ap in still_open)}\n"
                        f"Please verify manually."
                    )
                else:
                    logger.info("✅ Post-exit verification: %s confirmed closed on Alpaca", signal.ticker)
            except Exception:
                logger.warning("Post-exit verification failed (non-critical) for %s", signal.ticker)

        # Update position tracking
        close_result = self.position_mgr.close_position(signal, exit_price)
        if close_result is None:
            logger.error("Position close failed for %s", signal.ticker)
            await self.alerter.alert_error("Exit Failed", f"Could not close {signal.ticker}")
            return

        # Enrich for alert
        close_result["entry_price"] = position.entry_price

        # Log trade (only "executed" if we actually verified a fill)
        trade_status = "executed" if exit_price > 0 else "executed_unverified"
        self.db.create_trade(
            analyst=signal.analyst, message_id=signal.message_id,
            action=signal.action, asset_type=signal.asset_type,
            ticker=signal.ticker, direction=signal.direction,
            strike=signal.strike, expiry=signal.expiry,
            executed_price=exit_price, quantity=close_result["exit_quantity"],
            pnl=close_result["total_pnl"], confidence=signal.confidence,
            raw_message=signal.raw_message, status=trade_status,
        )

        # Write Obsidian trade journal
        try:
            close_trade(
                trade_id=position.id, ticker=signal.ticker, analyst=signal.analyst,
                exit_price=exit_price,
                total_pnl=close_result.get("total_pnl"),
                exit_quantity=close_result.get("exit_quantity"),
                reason="stop_hit" if signal.action == SignalAction.STOP_HIT else "signal",
                message_id=signal.message_id,
                raw_message=signal.raw_message,
            )
        except Exception:
            logger.warning("Obsidian journal write failed (exit) — non-critical")

        # Send appropriate alert type
        if signal.action == SignalAction.STOP_HIT:
            await self.alerter.alert_stop_hit(signal, close_result)
        else:
            await self.alerter.alert_exit(signal, close_result)
        await self._sync_sheets({"signal": signal, "result": close_result, "action": "exit"})

        # ── Post-close drawdown check ─────────────────────────────
        safe, risk_reason = self.drawdown_mgr.check_risk_limits()
        if not safe:
            summary = self.drawdown_mgr.get_risk_summary()
            logger.warning("🚨 DRAWDOWN BREACH after closing %s: %s", signal.ticker, risk_reason)
            await self.alerter.send_message(
                f"🚨 URGENT — DRAWDOWN LIMIT BREACHED\n\n"
                f"{risk_reason}\n\n"
                f"Daily PnL: ${summary['daily_pnl']:.2f}\n"
                f"Total PnL: ${summary['total_pnl']:.2f}\n"
                f"⛔ New trades HALTED until manual reset"
            )

    # ── auto-stop monitor ─────────────────────────────────────────

    async def _check_stop_losses(self):
        """Check all open positions against their stop prices. Sell if breached."""
        positions = self.position_mgr.get_open_positions()
        if not positions:
            return

        for position in positions:
            if not position.stop_price or position.stop_price <= 0:
                continue
            if position.current_quantity <= 0:
                continue

            try:
                # Get current price (crypto via Coinbase, options/stocks TBD)
                current_price = None
                if position.asset_type in (AssetType.CRYPTO.value, 'crypto') and self._coinbase:
                    current_price = await asyncio.to_thread(
                        self._coinbase.get_current_price, position.ticker
                    )
                elif position.asset_type in (AssetType.OPTION.value, 'option', AssetType.OPTION) and self._alpaca:
                    # Check options positions for 50%+ drawdown alerts
                    try:
                        alpaca_positions = await asyncio.to_thread(self._alpaca.list_open_positions)
                        ticker_upper = position.ticker.upper()
                        for ap in alpaca_positions:
                            if ap.symbol.startswith(ticker_upper) and len(ap.symbol) > len(ticker_upper):
                                current_price = float(ap.current_price) if hasattr(ap, 'current_price') and ap.current_price else float(ap.market_value) / (float(ap.qty) * 100) if float(ap.qty) > 0 else None
                                if current_price and position.entry_price:
                                    drawdown_pct = (current_price - position.entry_price) / position.entry_price
                                    if drawdown_pct <= -0.50:
                                        alert_key = f"dd50_{position.id}"
                                        if not hasattr(self, '_dd_alerts_sent'):
                                            self._dd_alerts_sent = set()
                                        if alert_key not in self._dd_alerts_sent:
                                            self._dd_alerts_sent.add(alert_key)
                                            await self.alerter.send_message(
                                                f"⚠️ OPTIONS DRAWDOWN ALERT\n"
                                                f"{position.ticker} {position.strike}{position.direction[0].upper() if position.direction else '?'} {position.expiry or ''}\n"
                                                f"Entry: ${position.entry_price:.2f} → Now: ${current_price:.2f}\n"
                                                f"Down {abs(drawdown_pct)*100:.0f}%\n"
                                                f"Analyst: {position.analyst}\n\n"
                                                f"Consider manual exit."
                                            )
                                            logger.warning("⚠️ 50%+ drawdown alert: %s %s @ $%.2f (entry $%.2f, down %.0f%%)",
                                                         position.analyst, position.ticker, current_price, position.entry_price, abs(drawdown_pct)*100)
                                break
                    except Exception:
                        logger.debug("Options price check failed for %s", position.ticker, exc_info=True)
                    continue
                else:
                    continue

                if current_price is None:
                    continue

                # Check if stop is breached
                is_long = position.direction in ("long", "call")
                stop_hit = (is_long and current_price <= position.stop_price) or \
                           (not is_long and current_price >= position.stop_price)

                if not stop_hit:
                    continue

                logger.warning("🛑 STOP HIT: %s %s — price $%.6f breached stop $%.6f",
                             position.analyst, position.ticker, current_price, position.stop_price)

                # Execute the exit
                if position.asset_type in (AssetType.CRYPTO.value, 'crypto') and self._coinbase:
                    order_result = await asyncio.to_thread(
                        self._coinbase.execute_exit, position.ticker,
                    )
                    exit_price = order_result.get("filled_price", current_price) if order_result else current_price
                elif self._alpaca and position.asset_type not in (AssetType.CRYPTO.value, 'crypto'):
                    # Build a minimal signal for the exit
                    stop_signal = ParsedSignal(
                        analyst=position.analyst, action=SignalAction.STOP_HIT.value,
                        asset_type=position.asset_type, ticker=position.ticker,
                        direction=position.direction, strike=position.strike,
                        expiry=position.expiry, entry_price=current_price,
                        trim_fraction=None, confidence=1.0,
                        raw_message=f"Auto-stop: {position.ticker} hit ${position.stop_price}",
                        message_id=f"autostop-{position.id}-{int(datetime.now(timezone.utc).timestamp())}",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                    order_result = await asyncio.to_thread(
                        self._alpaca.execute_exit_order, stop_signal, position.current_quantity,
                    )
                    exit_price = order_result.get("filled_price", current_price) if order_result else current_price
                else:
                    exit_price = current_price

                # Close the position in tracking (options × 100 multiplier)
                multiplier = 100 if position.asset_type in ('option', 'OPTION', AssetType.OPTION.value, AssetType.OPTION) else 1
                pnl = (exit_price - position.entry_price) * position.current_quantity * multiplier
                total_pnl = position.total_pnl + pnl
                self.db.close_position(position.id, total_pnl)

                # Remove from cache
                position_key = self.position_mgr._position_key(position)
                if position_key in self.position_mgr._positions_cache:
                    del self.position_mgr._positions_cache[position_key]

                # Log trade
                self.db.create_trade(
                    analyst=position.analyst,
                    message_id=f"autostop-{position.id}-{int(datetime.now(timezone.utc).timestamp())}",
                    action="stop_hit", asset_type=position.asset_type or "crypto",
                    ticker=position.ticker, direction=position.direction,
                    executed_price=exit_price, quantity=position.current_quantity,
                    pnl=total_pnl, confidence=1.0,
                    raw_message=f"Auto-stop: {position.ticker} breached stop ${position.stop_price:.6f} (price: ${exit_price:.6f})",
                    status="executed",
                )

                # Alert
                await self.alerter.send_message(
                    f"🛑 AUTO-STOP: {position.ticker}\n"
                    f"Stop: ${position.stop_price:.6f} | Exit: ${exit_price:.6f}\n"
                    f"Entry: ${position.entry_price:.6f} | PnL: ${pnl:.2f}\n"
                    f"Analyst: {position.analyst}"
                )

                logger.info("Auto-stop executed: %s %s exit @ $%.6f (PnL: $%.2f)",
                           position.analyst, position.ticker, exit_price, pnl)

            except Exception:
                logger.exception("Error checking stop for %s %s", position.analyst, position.ticker)


    async def _eod_cleanup(self):
        """End-of-day: close ALL open positions on all exchanges + DB."""
        logger.info("🧹 EOD CLEANUP — closing all positions")
        closed = []

        # 1. Close all Alpaca positions
        if self._alpaca:
            try:
                results = await asyncio.to_thread(self._alpaca.close_all_positions)
                for r in results:
                    closed.append(f"{'✅' if r['success'] else '❌'} {r['symbol']} qty={r['qty']} PnL=${r['pnl']:.2f}")
                    logger.info("EOD Alpaca close: %s (success=%s)", r['symbol'], r['success'])
            except Exception:
                logger.exception("Error closing Alpaca positions at EOD")

        # 2. Close all Coinbase positions
        if self._coinbase:
            db_positions = self.position_mgr.get_open_positions()
            for pos in db_positions:
                if pos.asset_type in (AssetType.CRYPTO.value, 'crypto', AssetType.CRYPTO):
                    try:
                        order_result = await asyncio.to_thread(
                            self._coinbase.execute_exit, pos.ticker,
                        )
                        exit_price = order_result.get("filled_price", 0) if order_result else 0
                        pnl = (exit_price - pos.entry_price) * pos.current_quantity
                        total_pnl = pos.total_pnl + pnl
                        self.db.close_position(pos.id, total_pnl)
                        # Remove from cache
                        position_key = self.position_mgr._position_key(pos)
                        if position_key in self.position_mgr._positions_cache:
                            del self.position_mgr._positions_cache[position_key]
                        closed.append(f"✅ {pos.ticker} exit@${exit_price:.6f} PnL=${pnl:.2f}")
                        logger.info("EOD Coinbase close: %s @ $%.6f (PnL: $%.2f)", pos.ticker, exit_price, pnl)
                    except Exception:
                        logger.exception("Error closing %s at EOD", pos.ticker)
                        closed.append(f"❌ {pos.ticker} — sell failed")

        # 3. Close any remaining DB positions that weren't on an exchange
        remaining = self.position_mgr.get_open_positions()
        for pos in remaining:
            self.db.close_position(pos.id, pos.total_pnl)
            position_key = self.position_mgr._position_key(pos)
            if position_key in self.position_mgr._positions_cache:
                del self.position_mgr._positions_cache[position_key]
            closed.append(f"🗑️ {pos.ticker} (DB only, no exchange sell)")
            logger.info("EOD DB cleanup: %s closed in DB only", pos.ticker)

        # 4. Send summary alert
        if closed:
            summary = "\n".join(closed)
            await self.alerter.send_message(
                f"🧹 **EOD CLEANUP** — {len(closed)} positions closed:\n\n{summary}"
            )
        else:
            await self.alerter.send_message("🧹 **EOD CLEANUP** — No open positions to close.")

        logger.info("EOD cleanup complete — %d positions processed", len(closed))


# ── entry point ───────────────────────────────────────────────────

PID_FILE = os.path.join(os.path.dirname(__file__), "trading_bot.pid")


def _check_pid_lock():
    """Prevent multiple instances. Returns True if safe to start."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            # Check if old process is still running
            os.kill(old_pid, 0)  # Signal 0 = just check existence
            logger.error("Trading bot already running (PID %d). Exiting.", old_pid)
            return False
        except (ProcessLookupError, ValueError):
            # Old process is dead, safe to continue
            logger.info("Stale PID file found (PID %s dead). Cleaning up.", old_pid if 'old_pid' in dir() else '?')
        except PermissionError:
            logger.error("Another process owns PID in lock file. Exiting.")
            return False
    # Write our PID
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def _remove_pid_lock():
    """Remove PID lock file on clean shutdown."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def main():
    if not _check_pid_lock():
        sys.exit(1)

    bot = TradingBot()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Graceful shutdown on SIGINT / SIGTERM
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(bot.stop()))

    try:
        loop.run_until_complete(bot.start())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    finally:
        loop.run_until_complete(bot.stop())
        loop.close()
        _remove_pid_lock()


if __name__ == "__main__":
    main()
