"""Shadow / log-only observation record.

Writes one JSON object per observed message to logs/<analyst>_shadow.jsonl.
This file is the dataset behind the later Tastytrade-vs-not decision: it
records what each message parsed to and, critically, whether the instrument
is executable on Alpaca at all.

Nothing in this module can place an order — it only reads a ParsedSignal and
appends a line to disk.
"""

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from parsers.base import ParsedSignal

logger = logging.getLogger(__name__)

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# Cash-settled index products. Alpaca does NOT support these — they are the
# whole reason the Tastytrade question exists. SPY/QQQ/IWM are ETFs and DO
# trade on Alpaca; only the index itself is blocked.
INDEX_TICKERS = frozenset({"SPX", "SPXW", "XSP", "NDX", "RUT", "VIX", "VIXW", "DJX"})


def classify_instrument(signal: Optional[ParsedSignal]) -> dict:
    """Work out what instrument this is and whether Alpaca could trade it."""
    if signal is None or not signal.ticker:
        return {"instrument": None, "is_index": False, "executable_on_alpaca": False,
                "not_executable_reason": "no ticker parsed"}

    ticker = signal.ticker.upper()
    asset_type = getattr(signal.asset_type, "value", signal.asset_type)

    if ticker in INDEX_TICKERS:
        return {
            "instrument": f"{ticker} (cash-settled index)",
            "is_index": True,
            "executable_on_alpaca": False,
            "not_executable_reason": "cash-settled index option — not supported by Alpaca",
        }

    instrument = f"{ticker} {asset_type}"
    if asset_type == "crypto":
        return {"instrument": instrument, "is_index": False,
                "executable_on_alpaca": False,
                "not_executable_reason": "crypto — routed to Coinbase, not Alpaca"}

    return {"instrument": instrument, "is_index": False,
            "executable_on_alpaca": True, "not_executable_reason": None}


def _signal_fields(signal: Optional[ParsedSignal]) -> dict:
    if signal is None:
        return {"parsed": False, "action": None, "ticker": None, "strike": None,
                "expiry": None, "direction": None, "price": None,
                "asset_type": None, "confidence": None}
    return {
        "parsed": True,
        "action": getattr(signal.action, "value", signal.action),
        "ticker": signal.ticker,
        "strike": signal.strike,
        "expiry": signal.expiry,
        "direction": signal.direction,
        "price": signal.entry_price,
        "asset_type": getattr(signal.asset_type, "value", signal.asset_type),
        "confidence": signal.confidence,
    }


def log_shadow_observation(analyst: str, channel_id: str, message_id: str,
                           content: str, timestamp: str,
                           signal: Optional[ParsedSignal], tier: str,
                           log_dir: str = None) -> Optional[str]:
    """Append one observation. Returns the path written, or None on failure.

    Never raises — a logging problem must not disturb the polling loop.
    """
    record = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "message_timestamp": timestamp,
        "analyst": analyst,
        "channel_id": channel_id,
        "message_id": message_id,
        "raw_text": content,
        "tier": tier,
        "executed": False,          # invariant: the shadow path never trades
        **_signal_fields(signal),
        **classify_instrument(signal),
    }

    directory = log_dir or LOG_DIR
    path = os.path.join(directory, f"{analyst}_shadow.jsonl")
    try:
        os.makedirs(directory, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
    except Exception:
        logger.exception("Failed to write shadow observation for %s", message_id)
        return None


def format_shadow_alert(analyst: str, content: str, signal: Optional[ParsedSignal],
                        tier: str) -> str:
    """Telegram body. Always [SHADOW]-prefixed so it can never be read as a
    real Eva/Ace trade."""
    preview = (content or "").strip()[:150] or "(empty message)"
    header = f"[SHADOW] 👁 <b>{analyst.replace('_', ' ').title()}</b> — log only, NO ORDER"

    if signal is None:
        return f"{header}\nTier: {tier} → no signal\n{preview}"

    info = classify_instrument(signal)
    action = getattr(signal.action, "value", signal.action)
    contract = signal.ticker or "?"
    if signal.strike:
        contract += f" {signal.strike}{'C' if signal.direction == 'call' else 'P'}"
    if signal.expiry:
        contract += f" {signal.expiry}"

    tradability = "✅ executable on Alpaca" if info["executable_on_alpaca"] \
        else f"🚫 NOT executable — {info['not_executable_reason']}"

    return (f"{header}\n"
            f"Tier: {tier} → <b>{action}</b> {contract}"
            f"{f' @ {signal.entry_price}' if signal.entry_price else ''}\n"
            f"{tradability}\n{preview}")
