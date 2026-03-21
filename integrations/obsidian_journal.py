"""Obsidian Trade Journal — writes trade notes to the vault.

Active trades:  Trading/Active Trades/Trade-{ID}-{TICKER}-{ANALYST}.md
Closed trades:  Trading/Closed Trades/Trade-{ID}-{TICKER}-{ANALYST}.md

Each trade note tracks the full lifecycle:
  - Entry signal + execution details
  - Trim history (each trim appended)
  - Exit details + final P&L
  - All linked Discord message IDs

On entry: create note in Active Trades/
On trim: append trim record to the note
On exit: append close record, move note to Closed Trades/
"""

import logging
import os
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

VAULT_BASE = "/Users/tray/Documents/Dolph & Tray/Trading"
ACTIVE_DIR = os.path.join(VAULT_BASE, "Active Trades")
CLOSED_DIR = os.path.join(VAULT_BASE, "Closed Trades")

# Ensure directories exist
os.makedirs(ACTIVE_DIR, exist_ok=True)
os.makedirs(CLOSED_DIR, exist_ok=True)


def _trade_filename(trade_id: int, ticker: str, analyst: str) -> str:
    """Generate consistent filename for a trade note."""
    clean_analyst = analyst.replace(" ", "-").title()
    return f"Trade-{trade_id}-{ticker.upper()}-{clean_analyst}.md"


def _format_price(price) -> str:
    """Format price for display, handling tiny crypto prices."""
    if price is None:
        return "N/A"
    price = float(price)
    if price < 0.01:
        return f"${price:.8f}"
    elif price < 1:
        return f"${price:.4f}"
    else:
        return f"${price:.2f}"


def open_trade(trade_id: int, analyst: str, ticker: str, asset_type: str,
               direction: str = None, strike: float = None, expiry: str = None,
               entry_price: float = None, executed_price: float = None,
               quantity: float = None, position_size: float = None,
               stop_price: float = None, target_prices: list = None,
               message_id: str = None, raw_message: str = None,
               confidence: float = None):
    """Create a new trade note in Active Trades/ on entry."""
    
    filename = _trade_filename(trade_id, ticker, analyst)
    filepath = os.path.join(ACTIVE_DIR, filename)
    
    # Build the note
    lines = []
    lines.append(f"# Trade {trade_id} — {ticker.upper()} ({analyst.title()})\n")
    
    # Status banner
    lines.append(f"**Status:** 🟢 OPEN")
    lines.append(f"**Opened:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Entry details
    lines.append("## Entry")
    lines.append(f"- **Ticker:** {ticker.upper()}")
    lines.append(f"- **Asset:** {asset_type}")
    if direction:
        lines.append(f"- **Direction:** {direction.upper()}")
    if strike:
        lines.append(f"- **Strike:** {strike}")
    if expiry:
        lines.append(f"- **Expiry:** {expiry}")
    if entry_price is not None:
        lines.append(f"- **Signal Price:** {_format_price(entry_price)}")
    if executed_price is not None:
        lines.append(f"- **Executed Price:** {_format_price(executed_price)}")
    if quantity is not None:
        lines.append(f"- **Quantity:** {quantity}")
    if position_size is not None:
        lines.append(f"- **Position Size:** ${position_size:.2f}")
    if confidence is not None:
        lines.append(f"- **Confidence:** {confidence:.2f}")
    if message_id:
        lines.append(f"- **Discord Message:** `{message_id}`")
    
    # Risk management
    if stop_price or target_prices:
        lines.append("\n## Risk Management")
        if stop_price:
            lines.append(f"- **Stop Loss:** {_format_price(stop_price)}")
        if target_prices:
            for i, tp in enumerate(target_prices, 1):
                lines.append(f"- **Target {i}:** {_format_price(tp)}")
    
    # Raw signal
    if raw_message:
        lines.append("\n## Original Signal")
        clean_msg = raw_message.replace('<@&697950067285295115>', '@alerts').strip()
        lines.append(f"```\n{clean_msg}\n```")
    
    # Trim history section (empty, will be appended)
    lines.append("\n## Trim History")
    lines.append("*No trims yet.*\n")
    
    # Footer
    lines.append("---")
    lines.append(f"*Analyst:* [[{analyst.title()}]]")
    lines.append(f"*Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
    lines.append(f"#trading #active-trade #{ticker.lower()} #{analyst.lower().replace(' ', '-')}")
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        logger.info("📝 Trade journal: opened %s", filename)
    except Exception:
        logger.exception("Failed to write trade journal: %s", filename)


def record_trim(trade_id: int, ticker: str, analyst: str,
                trim_number: int = 1, trim_qty: float = None,
                trim_price: float = None, trim_pnl: float = None,
                remaining_qty: float = None, message_id: str = None,
                raw_message: str = None):
    """Append a trim record to an active trade note."""
    
    filename = _trade_filename(trade_id, ticker, analyst)
    filepath = os.path.join(ACTIVE_DIR, filename)
    
    if not os.path.isfile(filepath):
        logger.warning("Trade journal not found for trim: %s", filename)
        return
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Build trim entry
        trim_entry = f"\n### Trim {trim_number} — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        if trim_price is not None:
            trim_entry += f"- **Price:** {_format_price(trim_price)}\n"
        if trim_qty is not None:
            trim_entry += f"- **Quantity Sold:** {trim_qty}\n"
        if remaining_qty is not None:
            trim_entry += f"- **Remaining:** {remaining_qty}\n"
        if trim_pnl is not None:
            emoji = "🟢" if trim_pnl >= 0 else "🔴"
            trim_entry += f"- **P&L:** {emoji} ${trim_pnl:.2f}\n"
        if message_id:
            trim_entry += f"- **Discord Message:** `{message_id}`\n"
        if raw_message:
            clean = raw_message.replace('<@&697950067285295115>', '@alerts').strip()
            trim_entry += f"\n```\n{clean}\n```\n"
        
        # Replace "No trims yet" placeholder, or insert before footer
        if "*No trims yet.*" in content:
            content = content.replace("*No trims yet.*", trim_entry)
        else:
            # Insert before the --- footer
            footer_idx = content.rfind("\n---\n")
            if footer_idx > 0:
                content = content[:footer_idx] + trim_entry + content[footer_idx:]
            else:
                content += trim_entry
        
        # Update timestamp
        content = _update_timestamp(content)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        logger.info("📝 Trade journal: trim %d on %s", trim_number, filename)
    except Exception:
        logger.exception("Failed to record trim: %s", filename)


def close_trade(trade_id: int, ticker: str, analyst: str,
                exit_price: float = None, total_pnl: float = None,
                exit_quantity: float = None, reason: str = "signal",
                message_id: str = None, raw_message: str = None):
    """Append close record to trade note and move to Closed Trades/."""
    
    filename = _trade_filename(trade_id, ticker, analyst)
    active_path = os.path.join(ACTIVE_DIR, filename)
    closed_path = os.path.join(CLOSED_DIR, filename)
    
    if not os.path.isfile(active_path):
        logger.warning("Trade journal not found for close: %s", filename)
        # Create a minimal closed note anyway
        _create_minimal_close(closed_path, trade_id, ticker, analyst, 
                             exit_price, total_pnl, reason)
        return
    
    try:
        with open(active_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Update status
        content = content.replace("**Status:** 🟢 OPEN", "**Status:** 🔴 CLOSED")
        
        # Add close section
        close_entry = f"\n## Exit\n"
        close_entry += f"- **Closed:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        if exit_price is not None:
            close_entry += f"- **Exit Price:** {_format_price(exit_price)}\n"
        if exit_quantity is not None:
            close_entry += f"- **Quantity Sold:** {exit_quantity}\n"
        close_entry += f"- **Reason:** {reason}\n"
        if total_pnl is not None:
            emoji = "🟢" if total_pnl >= 0 else "🔴"
            close_entry += f"- **Total P&L:** {emoji} ${total_pnl:.2f}\n"
        if message_id:
            close_entry += f"- **Discord Message:** `{message_id}`\n"
        if raw_message:
            clean = raw_message.replace('<@&697950067285295115>', '@alerts').strip()
            close_entry += f"\n```\n{clean}\n```\n"
        
        # Insert before footer
        footer_idx = content.rfind("\n---\n")
        if footer_idx > 0:
            content = content[:footer_idx] + close_entry + content[footer_idx:]
        else:
            content += close_entry
        
        # Update tags
        content = content.replace("#active-trade", "#closed-trade")
        content = _update_timestamp(content)
        
        # Write to closed location
        with open(closed_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # Remove from active
        os.remove(active_path)
        
        pnl_str = f"${total_pnl:.2f}" if total_pnl is not None else "N/A"
        logger.info("📝 Trade journal: closed %s (P&L: %s) → Closed Trades/", filename, pnl_str)
    except Exception:
        logger.exception("Failed to close trade journal: %s", filename)


def _create_minimal_close(filepath, trade_id, ticker, analyst, exit_price, total_pnl, reason):
    """Create a minimal closed trade note when no active note existed."""
    lines = []
    lines.append(f"# Trade {trade_id} — {ticker.upper()} ({analyst.title()})\n")
    lines.append(f"**Status:** 🔴 CLOSED (no active note found)")
    lines.append(f"**Closed:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append("## Exit")
    if exit_price is not None:
        lines.append(f"- **Exit Price:** {_format_price(exit_price)}")
    if total_pnl is not None:
        emoji = "🟢" if total_pnl >= 0 else "🔴"
        lines.append(f"- **Total P&L:** {emoji} ${total_pnl:.2f}")
    lines.append(f"- **Reason:** {reason}")
    lines.append("\n---")
    lines.append(f"*Analyst:* [[{analyst.title()}]]")
    lines.append(f"*Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
    lines.append(f"#trading #closed-trade #{ticker.lower()} #{analyst.lower().replace(' ', '-')}")
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    except Exception:
        logger.exception("Failed to create minimal close note")


def _update_timestamp(content: str) -> str:
    """Update the Last Updated timestamp in a note."""
    import re
    return re.sub(
        r'\*Last Updated:.*?\*',
        f"*Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        content
    )
