"""Coinbase Advanced Trade execution client for crypto spot trading."""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from coinbase.rest import RESTClient

from parsers.base import ParsedSignal, SignalAction

logger = logging.getLogger(__name__)


class CoinbaseClient:
    """Executes crypto spot trades via Coinbase Advanced Trade API."""

    def __init__(self, key_file: str = "config/coinbase_credentials.json"):
        self._key_file = key_file
        self._client: Optional[RESTClient] = None
        self._connected = False

    # ── lifecycle ──────────────────────────────────────────────────

    def connect(self) -> bool:
        """Initialize the Coinbase REST client."""
        try:
            import json
            with open(self._key_file) as f:
                keys = json.load(f)
            self._client = RESTClient(
                api_key=keys['name'],
                api_secret=keys['privateKey'],
                timeout=10
            )
            # Test connection
            accounts = self._client.get_accounts()
            usd_balance = 0
            for acc in accounts.accounts:
                if acc.currency == 'USD':
                    usd_balance = float(acc.available_balance['value'])
            self._connected = True
            logger.info("Coinbase connected — USD balance: $%.2f", usd_balance)
            return True
        except Exception:
            logger.exception("Failed to connect to Coinbase")
            self._connected = False
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── account info ──────────────────────────────────────────────

    def get_usd_balance(self) -> float:
        """Get available USD balance."""
        try:
            accounts = self._client.get_accounts()
            for acc in accounts.accounts:
                if acc.currency == 'USD':
                    return float(acc.available_balance['value'])
            return 0.0
        except Exception:
            logger.exception("Failed to get USD balance")
            return 0.0

    def get_crypto_balance(self, currency: str) -> float:
        """Get available balance for a specific crypto."""
        try:
            accounts = self._client.get_accounts()
            for acc in accounts.accounts:
                if acc.currency == currency.upper():
                    return float(acc.available_balance['value'])
            return 0.0
        except Exception:
            logger.exception("Failed to get %s balance", currency)
            return 0.0

    def get_current_price(self, ticker: str) -> Optional[float]:
        """Get current price for a crypto pair (e.g., BTC → BTC-USD)."""
        try:
            product_id = self._to_product_id(ticker)
            product = self._client.get_product(product_id)
            return float(product.price)
        except Exception:
            logger.exception("Failed to get price for %s", ticker)
            return None

    # ── order execution ───────────────────────────────────────────

    def execute_entry_order(self, signal: ParsedSignal, position_size_usd: float) -> Optional[Dict[str, Any]]:
        """Execute a market buy order for crypto.
        
        Args:
            signal: Parsed trading signal
            position_size_usd: Dollar amount to spend (e.g., $10)
            
        Returns:
            Order result dict or None on failure
        """
        if not self._connected:
            logger.error("Coinbase not connected")
            return None

        product_id = self._to_product_id(signal.ticker)
        client_order_id = str(uuid.uuid4())

        try:
            # Ensure minimum order size ($1 on Coinbase, we enforce $5)
            if position_size_usd < 5.0:
                logger.warning("Position size $%.2f below $5 minimum — adjusting to $5", position_size_usd)
                position_size_usd = 5.0

            # Check available balance
            usd_available = self.get_usd_balance()
            if position_size_usd > usd_available:
                logger.error("Insufficient USD: need $%.2f, have $%.2f", position_size_usd, usd_available)
                return None

            # Get current price for fill estimate
            current_price = self.get_current_price(signal.ticker)

            # Market buy — quote_size is USD amount
            order = self._client.market_order_buy(
                client_order_id=client_order_id,
                product_id=product_id,
                quote_size=str(round(position_size_usd, 2))
            )

            order_dict = order.to_dict()
            success = order_dict.get('success', False)

            if success:
                order_id = order_dict.get('success_response', {}).get('order_id', 'unknown')

                # Poll for actual fill price/qty
                fill_data = self._poll_fill(order_id) if order_id != 'unknown' else None
                filled_price = fill_data['filled_price'] if fill_data else (current_price or 0)
                filled_qty = fill_data['filled_qty'] if fill_data else (position_size_usd / current_price if current_price else 0)

                logger.info("Coinbase BUY filled: %s qty=%.6f @ $%.2f (order %s)",
                           product_id, filled_qty, filled_price, order_id)
                return {
                    'order_id': order_id,
                    'filled_qty': filled_qty,
                    'filled_price': filled_price,
                    'quantity': filled_qty,
                    'side': 'buy',
                    'product_id': product_id,
                    'quote_size': position_size_usd,
                    'status': 'filled'
                }
            else:
                error_msg = order_dict.get('error_response', {}).get('message', 'Unknown error')
                logger.error("Coinbase BUY failed for %s: %s", product_id, error_msg)
                return None

        except Exception:
            logger.exception("Failed to execute Coinbase buy for %s", product_id)
            return None

    def execute_sell_order(self, ticker: str, quantity: Optional[float] = None,
                          fraction: float = 1.0) -> Optional[Dict[str, Any]]:
        """Execute a market sell order for crypto.
        
        Args:
            ticker: Crypto ticker (e.g., BTC, ETH, SOL)
            quantity: Specific quantity to sell. If None, sells fraction of holdings.
            fraction: Fraction of total holdings to sell (0.0-1.0). Used if quantity is None.
            
        Returns:
            Order result dict or None on failure
        """
        if not self._connected:
            logger.error("Coinbase not connected")
            return None

        product_id = self._to_product_id(ticker)
        client_order_id = str(uuid.uuid4())

        try:
            # Get current holdings
            holdings = self.get_crypto_balance(ticker)
            if holdings <= 0:
                logger.warning("No %s holdings to sell", ticker)
                return None

            sell_qty = quantity if quantity else holdings * fraction

            # Round to 8 decimal places (Coinbase precision limit)
            # For very small quantities, ensure we're not sending dust
            sell_qty = round(sell_qty, 8)
            if sell_qty <= 0:
                logger.warning("Sell quantity rounds to 0 for %s — selling all instead", ticker)
                sell_qty = round(holdings, 8)

            # Get current price
            current_price = self.get_current_price(ticker)

            # Market sell — base_size is crypto quantity
            order = self._client.market_order_sell(
                client_order_id=client_order_id,
                product_id=product_id,
                base_size=str(sell_qty)
            )

            order_dict = order.to_dict()
            success = order_dict.get('success', False)

            if success:
                order_id = order_dict.get('success_response', {}).get('order_id', 'unknown')

                # Poll for actual fill price/qty
                fill_data = self._poll_fill(order_id) if order_id != 'unknown' else None
                filled_price = fill_data['filled_price'] if fill_data else (current_price or 0)
                filled_qty = fill_data['filled_qty'] if fill_data else sell_qty
                usd_value = filled_qty * filled_price

                logger.info("Coinbase SELL filled: %s %.6f @ $%.2f ($%.2f) (order %s)",
                           product_id, filled_qty, filled_price, usd_value, order_id)
                return {
                    'order_id': order_id,
                    'filled_qty': filled_qty,
                    'filled_price': filled_price,
                    'quantity': filled_qty,
                    'side': 'sell',
                    'product_id': product_id,
                    'usd_value': usd_value,
                    'status': 'filled'
                }
            else:
                error_msg = order_dict.get('error_response', {}).get('message', 'Unknown error')
                logger.error("Coinbase SELL failed for %s: %s", product_id, error_msg)
                return None

        except Exception:
            logger.exception("Failed to execute Coinbase sell for %s", product_id)
            return None

    def execute_trim(self, ticker: str, trim_fraction: float = 0.25) -> Optional[Dict[str, Any]]:
        """Trim a crypto position by selling a fraction."""
        return self.execute_sell_order(ticker, fraction=trim_fraction)

    def execute_exit(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Exit a crypto position entirely."""
        return self.execute_sell_order(ticker, fraction=1.0)

    # ── helpers ────────────────────────────────────────────────────

    def _poll_fill(self, order_id: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
        """Poll Coinbase for actual fill price/qty after order submission."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                order = self._client.get_order(order_id)
                order_data = order if isinstance(order, dict) else order.to_dict() if hasattr(order, 'to_dict') else {}
                status = order_data.get('status') or order_data.get('order', {}).get('status', '')
                if status in ('FILLED', 'filled'):
                    filled = order_data.get('order', order_data)
                    avg_price = filled.get('average_filled_price') or filled.get('filled_value', 0)
                    filled_size = filled.get('filled_size', 0)
                    if avg_price and filled_size:
                        return {
                            'filled_price': float(avg_price),
                            'filled_qty': float(filled_size),
                        }
                    return None
            except Exception:
                logger.debug("Poll for order %s failed, retrying", order_id)
            time.sleep(1)
        return None

    @staticmethod
    def _to_product_id(ticker: str) -> str:
        """Convert a ticker to Coinbase product ID format.
        
        Examples:
            BTC → BTC-USD
            ETH → ETH-USD
            BTCUSD → BTC-USD
        """
        ticker = ticker.upper().strip()
        
        # Already in product_id format
        if '-USD' in ticker:
            return ticker
        
        # Strip trailing USD/USDT/USDC
        for suffix in ['USDT', 'USDC', 'USD']:
            if ticker.endswith(suffix) and len(ticker) > len(suffix):
                ticker = ticker[:-len(suffix)]
                break
        
        return f"{ticker}-USD"

    def get_supported_pairs(self) -> list:
        """Get list of supported trading pairs."""
        try:
            products = self._client.get_products()
            pairs = []
            for p in products.products:
                if p.quote_currency_id == 'USD' and p.status == 'online':
                    pairs.append(p.product_id)
            return sorted(pairs)
        except Exception:
            logger.exception("Failed to get supported pairs")
            return []

    def validate_ticker(self, ticker: str) -> bool:
        """Check if a ticker is tradeable on Coinbase."""
        product_id = self._to_product_id(ticker)
        try:
            product = self._client.get_product(product_id)
            return product.status == 'online'
        except Exception:
            return False
