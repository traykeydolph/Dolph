"""SPX to SPY option converter — converts SPX options to equivalent SPY options."""

import logging
from datetime import datetime
from typing import Optional, Dict, Any
import math

from parsers.base import ParsedSignal

logger = logging.getLogger(__name__)


class SPXConverter:
    """Converts SPX options to equivalent SPY options for Alpaca execution."""
    
    # SPX typically trades 10x higher than SPY
    # Example: SPX at 5000, SPY at 500 (approximate 10:1 ratio)
    TYPICAL_SPX_SPY_RATIO = 10.0
    
    def __init__(self):
        self._cached_ratio: Optional[float] = None
        self._ratio_cache_time: Optional[datetime] = None
        self._cache_ttl_minutes = 5  # Cache ratio for 5 minutes
    
    def convert_signal(self, signal: ParsedSignal, position_manager=None) -> Optional[ParsedSignal]:
        """Convert SPX signal to equivalent SPY signal."""
        
        if signal.ticker != "SPX":
            return signal  # No conversion needed
        
        try:
            # Get current SPX/SPY ratio
            ratio = self._get_spx_spy_ratio()
            
            # For entry signals, we need strike/expiry from the signal
            if signal.action == "entry" and not signal.strike:
                logger.warning("SPX entry signal missing strike price, cannot convert")
                return None
            
            # For trim/exit signals, try to get details from existing position
            strike = signal.strike
            expiry = signal.expiry
            direction = signal.direction
            
            if not strike and position_manager and signal.action in ["trim", "exit", "stop_hit"]:
                # Look up existing position — it's stored as SPY (already converted)
                position = position_manager.find_position("SPY", signal.analyst)
                if not position:
                    # Also try SPX in case position was stored before conversion was added
                    position = position_manager.find_position("SPX", signal.analyst)
                if position:
                    strike = position.strike
                    expiry = position.expiry
                    direction = position.direction
                    logger.info("Found position for SPX conversion: ticker=%s, strike=%.0f, expiry=%s", 
                              position.ticker, strike or 0, expiry or "None")
                else:
                    logger.warning("No SPY/SPX position found for %s %s signal", signal.analyst, signal.action)
            
            if not strike:
                logger.warning("Cannot convert SPX signal without strike price")
                return None
            
            # Convert strike price
            spy_strike = self._convert_strike(strike, ratio)
            
            # Create new signal with SPY details
            converted_signal = ParsedSignal(
                analyst=signal.analyst,
                action=signal.action,
                asset_type=signal.asset_type,
                ticker="SPY",  # Convert SPX to SPY
                direction=direction,
                strike=spy_strike,
                expiry=expiry,
                entry_price=self._convert_price(signal.entry_price, ratio) if signal.entry_price else None,
                trim_fraction=signal.trim_fraction,
                confidence=signal.confidence,
                raw_message=signal.raw_message + " [SPX→SPY converted]",
                message_id=signal.message_id,
                timestamp=signal.timestamp
            )
            
            logger.info("Converted SPX to SPY: %s %.0f → %s %.0f (ratio %.2f)", 
                       "SPX", strike, converted_signal.ticker, 
                       spy_strike, ratio)
            
            return converted_signal
            
        except Exception:
            logger.exception("Failed to convert SPX signal to SPY")
            return None
    
    def _get_spx_spy_ratio(self) -> float:
        """Get current SPX/SPY price ratio with caching."""
        
        now = datetime.now()
        
        # Check if we have a cached ratio that's still valid
        if (self._cached_ratio and 
            self._ratio_cache_time and 
            (now - self._ratio_cache_time).total_seconds() < self._cache_ttl_minutes * 60):
            return self._cached_ratio
        
        try:
            # TODO: In production, fetch real prices from data provider
            # For now, use typical ratio as fallback
            ratio = self._fetch_current_ratio()
            
            # Cache the ratio
            self._cached_ratio = ratio
            self._ratio_cache_time = now
            
            return ratio
            
        except Exception:
            logger.exception("Failed to fetch SPX/SPY ratio, using typical ratio")
            return self.TYPICAL_SPX_SPY_RATIO
    
    def _fetch_current_ratio(self) -> float:
        """Fetch current SPX/SPY ratio from market data."""
        
        # TODO: Implement real market data fetch
        # This would query current SPX and SPY prices and calculate ratio
        # For MVP, return typical ratio
        
        # Placeholder for actual implementation:
        # spx_price = get_current_price("SPX") 
        # spy_price = get_current_price("SPY")
        # ratio = spx_price / spy_price
        
        # For now, use historical average
        ratio = self.TYPICAL_SPX_SPY_RATIO
        
        logger.debug("Using SPX/SPY ratio: %.2f", ratio)
        return ratio
    
    def _convert_strike(self, spx_strike: float, ratio: float) -> float:
        """Convert SPX strike to SPY strike using ratio."""
        
        spy_strike = spx_strike / ratio
        
        # Round to nearest $0.50 increment (typical SPY option strikes)
        spy_strike = round(spy_strike * 2) / 2
        
        return spy_strike
    
    def _convert_price(self, spx_price: Optional[float], ratio: float) -> Optional[float]:
        """Convert SPX option price to equivalent SPY option price."""
        
        if spx_price is None:
            return None
        
        # Option prices generally scale with underlying, but it's complex
        # For simplicity, divide by ratio (this is approximate)
        spy_price = spx_price / ratio
        
        return spy_price
    
    def validate_conversion(self, original_signal: ParsedSignal, 
                          converted_signal: ParsedSignal) -> bool:
        """Validate that conversion makes sense."""
        
        if original_signal.ticker != "SPX" or converted_signal.ticker != "SPY":
            return False
        
        if not original_signal.strike or not converted_signal.strike:
            return False
        
        # Check that SPY strike is reasonable (should be ~10x smaller than SPX)
        expected_spy_strike = original_signal.strike / self.TYPICAL_SPX_SPY_RATIO
        actual_spy_strike = converted_signal.strike
        
        # Allow 20% variance from expected ratio
        ratio_variance = abs(actual_spy_strike - expected_spy_strike) / expected_spy_strike
        
        if ratio_variance > 0.2:
            logger.warning("SPX→SPY conversion ratio seems off: SPX %.0f → SPY %.0f (%.1f%% variance)", 
                          original_signal.strike, actual_spy_strike, ratio_variance * 100)
            return False
        
        return True
    
    def get_quantity_adjustment(self, spx_quantity: int, ratio: float) -> int:
        """Calculate SPY quantity adjustment due to different contract values."""
        
        # SPX options represent $100 per point like SPY options
        # But since SPX is ~10x higher price, each SPX contract represents ~10x more value
        # So we might need more SPY contracts to match the same dollar exposure
        
        # For simplicity in MVP, use 1:1 contract ratio
        # In production, this would consider:
        # - Dollar delta exposure
        # - Contract multipliers
        # - Position sizing preferences
        
        return spx_quantity
    
    def is_spx_signal(self, signal: ParsedSignal) -> bool:
        """Check if signal is for SPX options that need conversion."""
        # Convert any SPX signal regardless of asset_type
        # Trim/exit signals may not have asset_type set, but SPX always needs conversion
        return signal.ticker == "SPX"
    
    def get_conversion_info(self, original_strike: float) -> Dict[str, Any]:
        """Get information about the conversion for logging/alerts."""
        
        ratio = self._get_spx_spy_ratio()
        spy_strike = self._convert_strike(original_strike, ratio)
        
        return {
            'original_ticker': 'SPX',
            'converted_ticker': 'SPY',
            'original_strike': original_strike,
            'converted_strike': spy_strike,
            'conversion_ratio': ratio,
            'cache_age_minutes': (datetime.now() - self._ratio_cache_time).total_seconds() / 60 
                if self._ratio_cache_time else None
        }