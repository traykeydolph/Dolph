"""Position Manager — tracks positions, calculates sizing, manages P&L and risk."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from config import Config
from parsers.base import ParsedSignal, SignalAction, AssetType
from storage.database import Database

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open trading position."""
    id: int
    analyst: str
    ticker: str
    direction: str  # "long" | "short" | "call" | "put"
    asset_type: str  # AssetType value
    strike: Optional[float] = None
    expiry: Optional[str] = None
    entry_price: float = 0.0
    current_quantity: int = 0
    original_quantity: int = 0
    position_size: float = 0.0  # Dollar value
    trim_count: int = 0
    total_pnl: float = 0.0
    opened_at: Optional[str] = None
    stop_price: Optional[float] = None
    target_prices: Optional[str] = None  # JSON string of target price list


class PositionManager:
    """Manages all trading positions, sizing, and risk calculations."""
    
    def __init__(self, config: Config, database: Database):
        self.config = config
        self.db = database
        self._positions_cache: Dict[str, Position] = {}
        self._load_positions()
    
    def calculate_position_size(self, signal: ParsedSignal) -> float:
        """Calculate position size based on signal and risk rules.
        
        For options, we use per-analyst contract targets rather than dollar sizing.
        The actual dollar amount is: contracts * estimated_premium * 100.
        We return a dollar value large enough to buy the target number of contracts.
        """
        
        # Base position size from config
        if signal.asset_type == AssetType.CRYPTO:
            base_size = self.config.position_size_crypto
        elif signal.asset_type == AssetType.OPTION:
            # Per-analyst contract targets — return dollar size based on target contracts
            # The alpaca_client divides by (mid_price * 100) to get quantity
            # So we set position_size = target_contracts * estimated_premium * 100
            target_contracts = self._get_target_contracts(signal.analyst)
            estimated_premium = signal.entry_price or 3.0  # Default $3 if unknown
            base_size = target_contracts * estimated_premium * 100
            logger.info("Per-analyst sizing: %s → %d contracts × $%.2f × 100 = $%.2f",
                       signal.analyst, target_contracts, estimated_premium, base_size)
        elif self._is_lotto_signal(signal):
            base_size = self.config.position_size_lotto
        else:
            base_size = self.config.position_size_standard
            
        # Check account size constraints
        account_size = self.config.account_size
        if base_size > account_size * 0.15:  # Never risk more than 15% per trade
            base_size = account_size * 0.15
            logger.warning("Position size capped at 15%% of account: $%.2f", base_size)
        
        # Check max open positions
        open_count = len(self.get_open_positions())
        if open_count >= self.config.max_open_positions:
            logger.warning("Max open positions reached (%d), rejecting new position", 
                         self.config.max_open_positions)
            return 0.0
            
        return base_size
    
    def open_position(self, signal: ParsedSignal, executed_price: float, 
                     quantity: int) -> Optional[Position]:
        """Open a new position from executed trade."""
        import json as _json
        
        position_size = executed_price * quantity
        
        # Serialize target prices list to JSON string for DB
        target_prices_json = _json.dumps(signal.target_prices) if signal.target_prices else None
        
        position_id = self.db.open_position(
            analyst=signal.analyst,
            ticker=signal.ticker,
            direction=signal.direction,
            asset_type=signal.asset_type,
            strike=signal.strike,
            expiry=signal.expiry,
            entry_price=executed_price,
            current_quantity=quantity,
            original_quantity=quantity,
            position_size=position_size,
            stop_price=signal.stop_price,
            target_prices=target_prices_json,
        )
        
        position = Position(
            id=position_id,
            analyst=signal.analyst,
            ticker=signal.ticker,
            direction=signal.direction,
            asset_type=signal.asset_type,
            strike=signal.strike,
            expiry=signal.expiry,
            entry_price=executed_price,
            current_quantity=quantity,
            original_quantity=quantity,
            position_size=position_size,
            opened_at=datetime.now(timezone.utc).isoformat(),
            stop_price=signal.stop_price,
            target_prices=target_prices_json,
        )
        
        # Add to cache
        position_key = self._position_key(position)
        self._positions_cache[position_key] = position
        
        logger.info("Opened position: %s %s %d shares @ $%.2f", 
                   position.analyst, position.ticker, quantity, executed_price)
        
        return position
    
    def trim_position(self, signal: ParsedSignal, trim_price: float) -> Optional[Dict[str, Any]]:
        """Trim an existing position based on signal."""
        
        position = self.find_position(signal.ticker, signal.analyst)
        if not position:
            logger.warning("No position found to trim: %s %s", signal.analyst, signal.ticker)
            return None
            
        if position.current_quantity <= 0:
            logger.warning("Position already closed: %s %s", signal.analyst, signal.ticker)
            return None
        
        # Calculate trim quantity
        trim_fraction = signal.trim_fraction or 0.2  # Default 20%
        trim_quantity = max(1, int(position.current_quantity * trim_fraction))
        
        # Don't trim more than what we have
        trim_quantity = min(trim_quantity, position.current_quantity)
        
        # Calculate P&L for this trim (options × 100, stocks/crypto × 1)
        multiplier = 100 if position.asset_type in (AssetType.OPTION.value, AssetType.OPTION, 'option') else 1
        trim_pnl = (trim_price - position.entry_price) * trim_quantity * multiplier
        
        # Update position
        new_quantity = position.current_quantity - trim_quantity
        new_trim_count = position.trim_count + 1
        new_total_pnl = position.total_pnl + trim_pnl
        
        self.db.update_position(
            position.id,
            current_quantity=new_quantity,
            trim_count=new_trim_count,
            total_pnl=new_total_pnl
        )
        
        # Update cache
        position.current_quantity = new_quantity
        position.trim_count = new_trim_count
        position.total_pnl = new_total_pnl
        
        # Close position if fully trimmed
        if new_quantity == 0:
            self.db.close_position(position.id, new_total_pnl)
            position_key = self._position_key(position)
            if position_key in self._positions_cache:
                del self._positions_cache[position_key]
        
        logger.info("Trimmed position: %s %s -%d shares @ $%.2f (P&L: $%.2f)", 
                   position.analyst, position.ticker, trim_quantity, trim_price, trim_pnl)
        
        return {
            'position_id': position.id,
            'trim_quantity': trim_quantity,
            'remaining_quantity': new_quantity,
            'trim_pnl': trim_pnl,
            'total_pnl': new_total_pnl,
            'fully_closed': new_quantity == 0
        }
    
    def close_position(self, signal: ParsedSignal, exit_price: float) -> Optional[Dict[str, Any]]:
        """Fully close an existing position."""
        
        position = self.find_position(signal.ticker, signal.analyst)
        if not position:
            logger.warning("No position found to close: %s %s", signal.analyst, signal.ticker)
            return None
        
        if position.current_quantity <= 0:
            logger.warning("Position already closed: %s %s", signal.analyst, signal.ticker)
            return None
        
        # Calculate final P&L (options × 100, stocks/crypto × 1)  
        multiplier = 100 if position.asset_type in (AssetType.OPTION.value, AssetType.OPTION, 'option') else 1
        final_pnl = (exit_price - position.entry_price) * position.current_quantity * multiplier
        total_pnl = position.total_pnl + final_pnl
        
        # Close in database
        self.db.close_position(position.id, total_pnl)
        
        # Remove from cache
        position_key = self._position_key(position)
        if position_key in self._positions_cache:
            del self._positions_cache[position_key]
        
        logger.info("Closed position: %s %s %d shares @ $%.2f (Total P&L: $%.2f)", 
                   position.analyst, position.ticker, position.current_quantity, 
                   exit_price, total_pnl)
        
        return {
            'position_id': position.id,
            'exit_quantity': position.current_quantity,
            'exit_price': exit_price,
            'final_pnl': final_pnl,
            'total_pnl': total_pnl
        }
    
    def find_position(self, ticker: str, analyst: str = None) -> Optional[Position]:
        """Find open position by ticker and optionally analyst."""
        
        # Try cache first
        for position in self._positions_cache.values():
            if position.ticker == ticker:
                if analyst is None or position.analyst == analyst:
                    return position
        
        # Fallback to database
        db_position = self.db.get_position_by_ticker(ticker, analyst)
        if db_position:
            position = Position(**db_position)
            position_key = self._position_key(position)
            self._positions_cache[position_key] = position
            return position
        
        return None
    
    def get_open_positions(self) -> List[Position]:
        """Get all open positions."""
        return list(self._positions_cache.values())
    
    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get portfolio-wide summary statistics."""
        positions = self.get_open_positions()
        
        total_value = sum(pos.position_size for pos in positions)
        total_pnl = sum(pos.total_pnl for pos in positions)
        
        return {
            'open_positions': len(positions),
            'total_value': total_value,
            'total_pnl': total_pnl,
            'account_utilization': total_value / self.config.account_size if self.config.account_size > 0 else 0
        }
    
    def _load_positions(self):
        """Load open positions from database into cache."""
        db_positions = self.db.get_open_positions()
        
        # Position dataclass fields (filter out DB-only columns like status/closed_at)
        import dataclasses
        valid_fields = {f.name for f in dataclasses.fields(Position)}
        
        for db_pos in db_positions:
            # Filter to only fields Position expects, add defaults for missing
            filtered = {k: v for k, v in db_pos.items() if k in valid_fields}
            if 'asset_type' not in filtered or not filtered.get('asset_type'):
                filtered['asset_type'] = 'crypto'  # Default for loaded positions
            position = Position(**filtered)
            position_key = self._position_key(position)
            self._positions_cache[position_key] = position
        
        logger.info("Loaded %d open positions from database", len(self._positions_cache))
    
    def _position_key(self, position: Position) -> str:
        """Generate unique key for position caching."""
        return f"{position.analyst}_{position.ticker}_{position.strike or 'spot'}_{position.expiry or 'none'}"
    
    def _get_target_contracts(self, analyst: str) -> int:
        """Get target contract count per analyst."""
        mapping = {
            'enhanced_market': self.config.contracts_enhanced_market,
            'grizzlies': self.config.contracts_grizzlies,
            'waxui': self.config.contracts_waxui,
            'eva': self.config.contracts_eva,
            'nando': self.config.contracts_nando,
            'zabes': self.config.contracts_zabes,
        }
        return mapping.get(analyst, 1)  # Default 1 contract

    def _is_lotto_signal(self, signal: ParsedSignal) -> bool:
        """Detect if this is a high-risk 'lotto' signal."""
        message_lower = signal.raw_message.lower()
        lotto_keywords = ['lotto', 'degen', 'yolo', 'gamble', 'risky']
        return any(keyword in message_lower for keyword in lotto_keywords)