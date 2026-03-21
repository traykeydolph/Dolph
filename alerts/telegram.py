"""Telegram alerter — sends formatted trade alerts via Telegram Bot API."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiohttp

from config import Config
from parsers.base import ParsedSignal, SignalAction

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def _sanitize_html(text: str) -> str:
    """Escape characters that break Telegram HTML parsing, including Discord tags."""
    import re
    # Remove Discord role/user/channel mentions that break Telegram HTML
    text = re.sub(r'<@[&!#]?\d+>', '', text)  # <@&role>, <@!user>, <@user>, <#channel>
    # Standard HTML escaping
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


class TelegramAlerter:
    """Sends formatted alerts to Telegram using the Bot HTTP API."""

    def __init__(self, config: Config):
        self.bot_token = config.telegram_bot_token
        self.chat_id = config.telegram_chat_id
        self._base_url = f"{TELEGRAM_API}/bot{self.bot_token}"
        self._session: Optional[aiohttp.ClientSession] = None

    # ── lifecycle ──────────────────────────────────────────────────

    async def start(self):
        """Create the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ── core send ─────────────────────────────────────────────────

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a raw text message to the configured chat."""
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram not configured — skipping alert")
            return False

        await self.start()

        # Sanitize all outgoing text to prevent HTML parsing errors
        sanitized_text = _sanitize_html(text)

        url = f"{self._base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": sanitized_text,
            "parse_mode": parse_mode,
        }

        try:
            async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return True
                body = await resp.text()
                logger.error("Telegram API %d: %s", resp.status, body[:300])
                return False
        except Exception:
            logger.exception("Failed to send Telegram message")
            return False

    # ── formatted alerts ──────────────────────────────────────────

    async def alert_new_entry(self, signal: ParsedSignal, order_result: Dict[str, Any],
                              quantity: int, position_size: float) -> bool:
        """Send NEW ENTRY alert."""
        strike_line = ""
        if signal.strike and signal.expiry and signal.direction:
            strike_line = f"{signal.ticker} {signal.strike}{signal.direction[0].upper()} {signal.expiry}"
        else:
            strike_line = f"{signal.ticker} {signal.direction or 'long'}"

        filled_price = order_result.get("filled_price", signal.entry_price or 0)

        text = (
            f"<b>NEW ENTRY</b> — {signal.analyst}\n"
            f"{strike_line} @ ${filled_price:.2f}\n"
            f"Size: ${position_size:.0f} ({quantity} contracts)\n"
            f"Confidence: {signal.confidence:.2f}"
        )
        return await self.send_message(text)

    async def alert_trim(self, signal: ParsedSignal, trim_result: Dict[str, Any]) -> bool:
        """Send TRIM alert."""
        trim_num = trim_result.get("trim_count", "?")
        total_trims = 5  # Waxui standard
        remaining = trim_result.get("remaining_quantity", 0)
        trim_qty = trim_result.get("trim_quantity", 0)
        trim_pnl = trim_result.get("trim_pnl", 0)
        entry_price = trim_result.get("entry_price", 0)
        trim_price = trim_result.get("trim_price", 0)

        pct = (trim_price / entry_price - 1) * 100 if entry_price else 0

        strike_line = ""
        if signal.strike and signal.expiry and signal.direction:
            strike_line = f"{signal.ticker} {signal.strike}{signal.direction[0].upper()} {signal.expiry}"
        else:
            strike_line = signal.ticker

        text = (
            f"<b>TRIM {trim_num}/{total_trims}</b> — {signal.analyst}\n"
            f"{strike_line}\n"
            f"Sold {trim_qty} @ ${trim_price:.2f} ({pct:+.0f}%)\n"
            f"Remaining: {remaining} contracts"
        )
        return await self.send_message(text)

    async def alert_exit(self, signal: ParsedSignal, close_result: Dict[str, Any]) -> bool:
        """Send EXIT alert."""
        exit_qty = close_result.get("exit_quantity", 0)
        exit_price = close_result.get("exit_price", 0)
        total_pnl = close_result.get("total_pnl", 0)
        entry_price = close_result.get("entry_price", 0)
        pct = (exit_price / entry_price - 1) * 100 if entry_price else 0

        strike_line = ""
        if signal.strike and signal.expiry and signal.direction:
            strike_line = f"{signal.ticker} {signal.strike}{signal.direction[0].upper()} {signal.expiry}"
        else:
            strike_line = signal.ticker

        text = (
            f"<b>EXIT</b> — {signal.analyst}\n"
            f"{strike_line}\n"
            f"Closed {exit_qty} @ ${exit_price:.2f}\n"
            f"P&L: ${total_pnl:+.2f} ({pct:+.0f}%)"
        )
        return await self.send_message(text)

    async def alert_stop_hit(self, signal: ParsedSignal, close_result: Dict[str, Any]) -> bool:
        """Send STOP HIT alert."""
        exit_price = close_result.get("exit_price", 0)
        entry_price = close_result.get("entry_price", 0)
        total_pnl = close_result.get("total_pnl", 0)
        pct = (exit_price / entry_price - 1) * 100 if entry_price else 0

        if signal.asset_type == "crypto":
            desc = f"{signal.ticker} {signal.direction or 'long'} from ${entry_price:,.2f}"
        else:
            desc = signal.ticker
            if signal.strike and signal.expiry and signal.direction:
                desc = f"{signal.ticker} {signal.strike}{signal.direction[0].upper()} {signal.expiry}"

        text = (
            f"<b>STOP HIT</b> — {signal.analyst}\n"
            f"{desc}\n"
            f"Exit: ${exit_price:,.2f} ({pct:+.1f}%)\n"
            f"P&L: ${total_pnl:+.2f}"
        )
        return await self.send_message(text)

    async def alert_error(self, context: str, error: str) -> bool:
        """Send ERROR alert."""
        text = (
            f"<b>ERROR</b>\n"
            f"{context}\n"
            f"<code>{error[:500]}</code>"
        )
        return await self.send_message(text)

    async def alert_status_summary(self, summary: Dict[str, Any]) -> bool:
        """Send periodic status summary."""
        open_pos = summary.get("open_positions", 0)
        total_value = summary.get("total_value", 0)
        total_pnl = summary.get("total_pnl", 0)
        utilization = summary.get("account_utilization", 0)

        text = (
            f"<b>STATUS SUMMARY</b>\n"
            f"Open positions: {open_pos}\n"
            f"Total value: ${total_value:,.2f}\n"
            f"Total P&L: ${total_pnl:+,.2f}\n"
            f"Account utilization: {utilization:.0%}"
        )
        return await self.send_message(text)

    async def alert_startup(self) -> bool:
        """Send startup banner."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        text = (
            f"<b>TRADING BOT ONLINE</b>\n"
            f"Started at {now}\n"
            f"Monitoring: Grizzlies, Waxui, Enhanced Market\n"
            f"Execution: Alpaca (paper)\n"
            f"Ready to copy-trade."
        )
        return await self.send_message(text)

    async def alert_shutdown(self) -> bool:
        """Send shutdown notification."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        text = (
            f"<b>TRADING BOT OFFLINE</b>\n"
            f"Stopped at {now}"
        )
        return await self.send_message(text)

    async def alert_skipped(self, signal: ParsedSignal, reason: str) -> bool:
        """Alert when a trade is skipped."""
        text = (
            f"<b>SKIPPED</b> — {signal.analyst}\n"
            f"{signal.ticker} {signal.action}\n"
            f"Reason: {reason}"
        )
        return await self.send_message(text)
