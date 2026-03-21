"""
Google Sheets integration for the Trading Bot.

Provides a SheetsSync class that mirrors trade data to Google Sheets
as a display/reporting layer on top of the SQLite source of truth.

=== INTEGRATION HOOKS (add to main.py) ===

1. At top of main.py:
    from integrations.google_sheets import SheetsSync
    sheets = SheetsSync()

2. After _handle_entry succeeds:
    await asyncio.to_thread(sheets.log_trade, trade_data)
    await asyncio.to_thread(sheets.update_open_positions, open_positions_list)

3. After _handle_trim succeeds:
    await asyncio.to_thread(sheets.log_trade, trade_data)
    await asyncio.to_thread(sheets.update_open_positions, open_positions_list)
    await asyncio.to_thread(sheets.update_analyst_scoreboard)

4. After _handle_exit succeeds:
    await asyncio.to_thread(sheets.log_trade, trade_data)
    await asyncio.to_thread(sheets.update_open_positions, open_positions_list)
    await asyncio.to_thread(sheets.update_analyst_scoreboard)

5. After auto-stop fires:
    await asyncio.to_thread(sheets.log_trade, trade_data)
    await asyncio.to_thread(sheets.update_open_positions, open_positions_list)

6. Daily summary cron:
    await asyncio.to_thread(sheets.append_daily_summary, summary_dict)

All calls are fire-and-forget safe — they log errors but never raise.
=========================================
"""

import os
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Column definitions
TRADE_LOG_HEADERS = [
    "Timestamp", "Analyst", "Ticker", "Action", "Direction", "Asset Type",
    "Strike", "Expiry", "Entry Price", "Executed Price", "Quantity",
    "PnL", "Confidence", "Status", "Raw Message"
]

OPEN_POSITIONS_HEADERS = [
    "Analyst", "Ticker", "Direction", "Entry Price", "Current Qty",
    "Stop Price", "Targets", "Trim Count", "Opened At", "Position Size"
]

SCOREBOARD_HEADERS = [
    "Analyst", "Total Trades", "Wins", "Losses", "Win Rate",
    "Total PnL", "Avg PnL", "Best Trade", "Worst Trade"
]

DAILY_SUMMARY_HEADERS = [
    "Date", "Total Trades", "Entries", "Trims", "Exits",
    "Stops Hit", "Total PnL", "Win Rate"
]


class SheetsSync:
    """Sync trading data to Google Sheets. All methods are sync and exception-safe."""

    def __init__(self):
        self._client = None
        self._spreadsheet = None
        self._key_file = os.environ.get("GOOGLE_SHEETS_KEY_FILE", "")
        self._sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")

    def is_configured(self) -> bool:
        """Return True if the required env vars are set."""
        return bool(self._key_file and self._sheet_id)

    def _connect(self):
        """Lazily connect to Google Sheets. Raises on failure."""
        if self._spreadsheet is not None:
            return
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(self._key_file, scopes=scopes)
        self._client = gspread.authorize(creds)
        self._spreadsheet = self._client.open_by_key(self._sheet_id)

    def _get_or_create_sheet(self, title: str, headers: list[str]):
        """Get a worksheet by title, creating it with headers if missing."""
        try:
            ws = self._spreadsheet.worksheet(title)
        except Exception:
            ws = self._spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
            ws.append_row(headers)
            # Bold the header row
            ws.format("1", {"textFormat": {"bold": True}})
        return ws

    # ------------------------------------------------------------------
    # Public methods — all fire-and-forget safe
    # ------------------------------------------------------------------

    def log_trade(self, trade_data: dict) -> None:
        """Append a single trade row to the Signal Log sheet."""
        if not self.is_configured():
            return
        try:
            self._connect()
            ws = self._get_or_create_sheet("Signal Log", TRADE_LOG_HEADERS)
            raw_msg = str(trade_data.get("raw_message", ""))[:120]
            row = [
                trade_data.get("timestamp", datetime.now().isoformat()),
                trade_data.get("analyst", ""),
                trade_data.get("ticker", ""),
                trade_data.get("action", ""),
                trade_data.get("direction", ""),
                trade_data.get("asset_type", ""),
                trade_data.get("strike", ""),
                trade_data.get("expiry", ""),
                trade_data.get("entry_price", ""),
                trade_data.get("exit_price", ""),
                trade_data.get("quantity", ""),
                trade_data.get("pnl", ""),
                trade_data.get("confidence", ""),
                trade_data.get("status", ""),
                raw_msg,
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")
            logger.info("Logged trade to Sheets: %s %s", trade_data.get("ticker"), trade_data.get("action"))
        except Exception:
            logger.exception("Failed to log trade to Google Sheets")

    def update_open_positions(self, positions: list[dict]) -> None:
        """Clear and rewrite the Open Positions sheet."""
        if not self.is_configured():
            return
        try:
            self._connect()
            ws = self._get_or_create_sheet("Open Positions", OPEN_POSITIONS_HEADERS)
            ws.clear()
            ws.append_row(OPEN_POSITIONS_HEADERS)
            ws.format("1", {"textFormat": {"bold": True}})
            rows = []
            for p in positions:
                targets = p.get("targets", "")
                if isinstance(targets, (list, tuple)):
                    targets = ", ".join(str(t) for t in targets)
                rows.append([
                    p.get("analyst", ""),
                    p.get("ticker", ""),
                    p.get("direction", ""),
                    p.get("entry_price", ""),
                    p.get("current_qty", ""),
                    p.get("stop_price", ""),
                    str(targets),
                    p.get("trim_count", 0),
                    p.get("opened_at", ""),
                    p.get("position_size", ""),
                ])
            if rows:
                ws.append_rows(rows, value_input_option="USER_ENTERED")
            logger.info("Updated open positions: %d rows", len(rows))
        except Exception:
            logger.exception("Failed to update open positions in Google Sheets")

    def update_analyst_scoreboard(self) -> None:
        """Read Trade Log, compute per-analyst stats, write to Analyst Scoreboard."""
        if not self.is_configured():
            return
        try:
            self._connect()
            trade_ws = self._get_or_create_sheet("Signal Log", TRADE_LOG_HEADERS)
            all_rows = trade_ws.get_all_records()

            # Aggregate per analyst
            stats: dict[str, dict] = {}
            for row in all_rows:
                analyst = row.get("Analyst", "Unknown")
                if analyst not in stats:
                    stats[analyst] = {
                        "total": 0, "wins": 0, "losses": 0,
                        "total_pnl": 0.0, "best": float("-inf"), "worst": float("inf"),
                    }
                s = stats[analyst]
                # Only count exits/stops as completed trades for win/loss
                action = str(row.get("Action", "")).lower()
                if action not in ("exit", "stop_hit"):
                    continue
                pnl = 0.0
                try:
                    pnl = float(row.get("PnL", 0) or 0)
                except (ValueError, TypeError):
                    pass
                s["total"] += 1
                s["total_pnl"] += pnl
                if pnl > 0:
                    s["wins"] += 1
                elif pnl < 0:
                    s["losses"] += 1
                s["best"] = max(s["best"], pnl)
                s["worst"] = min(s["worst"], pnl)

            sb_ws = self._get_or_create_sheet("Analyst Scoreboard", SCOREBOARD_HEADERS)
            sb_ws.clear()
            sb_ws.append_row(SCOREBOARD_HEADERS)
            sb_ws.format("1", {"textFormat": {"bold": True}})

            rows = []
            for analyst, s in sorted(stats.items()):
                total = s["total"]
                win_rate = f"{s['wins'] / total * 100:.1f}%" if total > 0 else "N/A"
                avg_pnl = round(s["total_pnl"] / total, 2) if total > 0 else 0
                best = s["best"] if s["best"] != float("-inf") else "N/A"
                worst = s["worst"] if s["worst"] != float("inf") else "N/A"
                rows.append([
                    analyst, total, s["wins"], s["losses"], win_rate,
                    round(s["total_pnl"], 2), avg_pnl, best, worst,
                ])
            if rows:
                sb_ws.append_rows(rows, value_input_option="USER_ENTERED")
            logger.info("Updated analyst scoreboard: %d analysts", len(rows))
        except Exception:
            logger.exception("Failed to update analyst scoreboard in Google Sheets")

    def append_daily_summary(self, summary: dict) -> None:
        """Append a row to the Daily Summary sheet."""
        if not self.is_configured():
            return
        try:
            self._connect()
            ws = self._get_or_create_sheet("Daily Summary", DAILY_SUMMARY_HEADERS)
            row = [
                summary.get("date", datetime.now().strftime("%Y-%m-%d")),
                summary.get("total_trades", 0),
                summary.get("entries", 0),
                summary.get("trims", 0),
                summary.get("exits", 0),
                summary.get("stops_hit", 0),
                summary.get("total_pnl", 0),
                summary.get("win_rate", "N/A"),
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")
            logger.info("Appended daily summary for %s", row[0])
        except Exception:
            logger.exception("Failed to append daily summary to Google Sheets")
