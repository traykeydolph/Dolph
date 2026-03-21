"""Drawdown-aware risk manager for prop firm accounts.

Reads PnL from the positions DB — no duplicate state.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Tuple

from config import Config
from storage.database import Database

logger = logging.getLogger(__name__)


class DrawdownManager:
    """Enforces daily/total drawdown limits and dynamic position sizing."""

    def __init__(self, config: Config, database: Database):
        self.config = config
        self.db = database

        # Risk params from config
        self.account_balance = float(getattr(config, "account_balance_risk", 100.0))
        self.max_daily_dd_pct = float(getattr(config, "max_daily_drawdown_pct", 5.0))
        self.max_total_dd_pct = float(getattr(config, "max_total_drawdown_pct", 10.0))
        self.max_position_size_pct = float(getattr(config, "max_position_size_pct", 2.0))
        self.max_open_positions = int(getattr(config, "max_open_positions_risk", 15))
        self.risk_per_trade_pct = float(getattr(config, "risk_per_trade_pct", 1.0))

        # Cached flag — once breached we stop until manual reset
        self._halted = False
        self._halt_reason = ""

    # ── PnL queries (DB-driven) ──────────────────────────────────

    def get_daily_pnl(self) -> float:
        """Sum of total_pnl for positions closed today (UTC)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self.db.conn.execute(
            "SELECT COALESCE(SUM(total_pnl), 0) as pnl FROM positions "
            "WHERE status = 'closed' AND DATE(closed_at) = ?",
            (today,),
        ).fetchone()
        return float(row["pnl"]) if row else 0.0

    def get_total_pnl(self) -> float:
        """Sum of total_pnl for ALL closed positions."""
        row = self.db.conn.execute(
            "SELECT COALESCE(SUM(total_pnl), 0) as pnl FROM positions WHERE status = 'closed'"
        ).fetchone()
        return float(row["pnl"]) if row else 0.0

    def get_current_exposure(self) -> float:
        """Total dollar value of open positions."""
        row = self.db.conn.execute(
            "SELECT COALESCE(SUM(position_size), 0) as exposure FROM positions WHERE status = 'open'"
        ).fetchone()
        return float(row["exposure"]) if row else 0.0

    def get_open_position_count(self) -> int:
        row = self.db.conn.execute(
            "SELECT COUNT(*) as cnt FROM positions WHERE status = 'open'"
        ).fetchone()
        return int(row["cnt"]) if row else 0

    # ── Drawdown checks ──────────────────────────────────────────

    def is_daily_drawdown_breached(self) -> bool:
        daily_pnl = self.get_daily_pnl()
        max_loss = self.account_balance * (self.max_daily_dd_pct / 100.0)
        return daily_pnl <= -max_loss

    def is_total_drawdown_breached(self) -> bool:
        total_pnl = self.get_total_pnl()
        max_loss = self.account_balance * (self.max_total_dd_pct / 100.0)
        return total_pnl <= -max_loss

    def check_risk_limits(self) -> Tuple[bool, str]:
        """Master gate — returns (safe_to_trade, reason_if_not)."""
        if self._halted:
            return False, f"HALTED: {self._halt_reason}"

        if self.is_daily_drawdown_breached():
            self._halted = True
            self._halt_reason = (
                f"Daily drawdown breached: PnL ${self.get_daily_pnl():.2f} "
                f"exceeds -{self.max_daily_dd_pct}% of ${self.account_balance:.2f}"
            )
            return False, self._halt_reason

        if self.is_total_drawdown_breached():
            self._halted = True
            self._halt_reason = (
                f"Total drawdown breached: PnL ${self.get_total_pnl():.2f} "
                f"exceeds -{self.max_total_dd_pct}% of ${self.account_balance:.2f}"
            )
            return False, self._halt_reason

        if self.get_open_position_count() >= self.max_open_positions:
            return False, f"Max open positions ({self.max_open_positions}) reached"

        return True, "OK"

    # ── Position sizing ──────────────────────────────────────────

    def can_open_position(self, proposed_size: float) -> Tuple[bool, str]:
        """Check if a specific proposed dollar size is allowed."""
        safe, reason = self.check_risk_limits()
        if not safe:
            return False, reason

        max_size = self.account_balance * (self.max_position_size_pct / 100.0)
        if proposed_size > max_size:
            return False, (
                f"Position ${proposed_size:.2f} exceeds max "
                f"{self.max_position_size_pct}% of account (${max_size:.2f})"
            )

        # Check remaining daily budget
        daily_pnl = self.get_daily_pnl()
        max_daily_loss = self.account_balance * (self.max_daily_dd_pct / 100.0)
        remaining = max_daily_loss + daily_pnl  # daily_pnl is negative when losing
        if remaining < proposed_size * 0.5:
            return False, (
                f"Insufficient daily risk budget: ${remaining:.2f} remaining, "
                f"proposed ${proposed_size:.2f}"
            )

        return True, "OK"

    def calculate_position_size(
        self,
        entry_price: float,
        stop_price: float | None,
        account_balance: float | None = None,
    ) -> float:
        """Risk-based position sizing.

        If stop_price is provided: size = risk_amount / |entry - stop|
        Returns a dollar value (quantity * entry_price).
        If no stop, returns flat risk amount.
        """
        bal = account_balance or self.account_balance
        risk_amount = bal * (self.risk_per_trade_pct / 100.0)

        if stop_price and entry_price and stop_price != entry_price:
            distance = abs(entry_price - stop_price)
            risk_per_unit = distance
            quantity = risk_amount / risk_per_unit
            size = quantity * entry_price
        else:
            # No stop — use flat risk amount as position size
            size = risk_amount

        # Cap at max position size
        max_size = bal * (self.max_position_size_pct / 100.0)
        size = min(size, max_size)

        return round(size, 2)

    # ── Summary / reset ──────────────────────────────────────────

    def get_risk_summary(self) -> dict:
        daily_pnl = self.get_daily_pnl()
        total_pnl = self.get_total_pnl()
        max_daily = self.account_balance * (self.max_daily_dd_pct / 100.0)
        max_total = self.account_balance * (self.max_total_dd_pct / 100.0)

        return {
            "account_balance": self.account_balance,
            "daily_pnl": daily_pnl,
            "daily_drawdown_limit": -max_daily,
            "daily_remaining": max_daily + daily_pnl,
            "total_pnl": total_pnl,
            "total_drawdown_limit": -max_total,
            "total_remaining": max_total + total_pnl,
            "open_positions": self.get_open_position_count(),
            "max_open_positions": self.max_open_positions,
            "current_exposure": self.get_current_exposure(),
            "halted": self._halted,
            "halt_reason": self._halt_reason,
        }

    def reset_daily(self):
        """Reset daily halt flag (call at market open). Total halt stays."""
        if self._halted and "Daily" in self._halt_reason:
            logger.info("Daily drawdown reset — resuming trading")
            self._halted = False
            self._halt_reason = ""

    def force_resume(self):
        """Manual override to resume trading after halt."""
        logger.warning("MANUAL RESUME — drawdown halt cleared")
        self._halted = False
        self._halt_reason = ""
