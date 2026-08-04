"""Alpaca API client — handles options and stock order execution."""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import time
import requests as _requests

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError

from config import Config
from parsers.base import ParsedSignal, SignalAction, AssetType

logger = logging.getLogger(__name__)


class AlpacaClient:
    """Alpaca API wrapper for order execution and account management."""
    
    def __init__(self, config: Config):
        self.config = config
        self.api = tradeapi.REST(
            key_id=config.alpaca_api_key,
            secret_key=config.alpaca_secret_key,
            base_url=config.alpaca_base_url,
            api_version='v2'
        )
        
        # Verify connection
        try:
            account = self.api.get_account()
            logger.info("Connected to Alpaca: account %s, %s, $%s equity, $%s buying power",
                       account.account_number, account.status,
                       account.equity, account.buying_power)
        except Exception:
            logger.exception("Failed to connect to Alpaca API")
            raise
    
    def force_close_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Force close a position using Alpaca's native close-position API. Works with any symbol including options."""
        try:
            response = self.api.close_position(symbol)
            logger.info("Force-closed position %s via Alpaca API", symbol)
            return {
                'order_id': getattr(response, 'id', None),
                'symbol': symbol,
                'status': getattr(response, 'status', 'submitted'),
                'filled_price': float(response.filled_avg_price) if getattr(response, 'filled_avg_price', None) else 0,
                'filled_qty': int(response.filled_qty) if getattr(response, 'filled_qty', None) else 0
            }
        except APIError as e:
            logger.error("Alpaca force-close failed for %s: %s", symbol, str(e))
            return None
        except Exception:
            logger.exception("Unexpected error force-closing %s", symbol)
            return None

    def close_all_positions(self) -> list:
        """Close all open positions on Alpaca. Returns list of results."""
        results = []
        try:
            positions = self.api.list_positions()
            for pos in positions:
                result = self.force_close_by_symbol(pos.symbol)
                results.append({
                    'symbol': pos.symbol,
                    'qty': pos.qty,
                    'pnl': float(pos.unrealized_pl),
                    'success': result is not None
                })
            return results
        except Exception:
            logger.exception("Error closing all Alpaca positions")
            return results

    def list_open_positions(self) -> list:
        """List all open Alpaca positions."""
        try:
            return self.api.list_positions()
        except Exception:
            logger.exception("Error listing Alpaca positions")
            return []

    def execute_entry_order(self, signal: ParsedSignal, position_size: float) -> Optional[Dict[str, Any]]:
        """Execute entry order based on parsed signal."""
        
        try:
            if signal.asset_type == AssetType.OPTION:
                return self._execute_option_entry(signal, position_size)
            elif signal.asset_type == AssetType.STOCK:
                return self._execute_stock_entry(signal, position_size)
            else:
                logger.error("Unsupported asset type for Alpaca: %s", signal.asset_type)
                return None
                
        except APIError as e:
            logger.error("Alpaca API error executing entry: %s", str(e))
            return None
        except Exception:
            logger.exception("Unexpected error executing entry order")
            return None
    
    def execute_exit_order(self, signal: ParsedSignal, quantity: int) -> Optional[Dict[str, Any]]:
        """Execute exit order to close position."""
        
        try:
            if signal.asset_type == AssetType.OPTION:
                return self._execute_option_exit(signal, quantity)
            elif signal.asset_type == AssetType.STOCK:
                return self._execute_stock_exit(signal, quantity)
            else:
                logger.error("Unsupported asset type for Alpaca: %s", signal.asset_type)
                return None
                
        except APIError as e:
            logger.error("Alpaca API error executing exit: %s", str(e))
            return None
        except Exception:
            logger.exception("Unexpected error executing exit order")
            return None
    
    def _execute_option_entry(self, signal: ParsedSignal, position_size: float) -> Optional[Dict[str, Any]]:
        """Execute options entry order."""
        
        # Build option symbol (with fallback resolution)
        option_symbol = self._build_option_symbol(signal)
        if not option_symbol:
            logger.warning("Primary symbol build failed for %s — trying options chain resolve", signal.ticker)
            option_symbol = self._resolve_option_symbol(signal)
        if not option_symbol:
            logger.error("Failed to build/resolve option symbol for %s", signal.ticker)
            return None
        
        # Get current option price for quantity calculation
        quote = self._get_option_quote(option_symbol)
        if not quote:
            logger.error("Failed to get quote for %s", option_symbol)
            return None
        
        # Calculate quantity based on position size
        mid_price = (quote['bid'] + quote['ask']) / 2 if quote['bid'] and quote['ask'] else quote.get('last', 0)
        if mid_price <= 0:
            logger.error("Invalid option price for %s: %s", option_symbol, mid_price)
            return None
            
        # Options are quoted per share but represent 100 shares
        quantity = max(1, int(position_size / (mid_price * 100)))
        
        # Place order — use limit at ask to avoid wide spread slippage
        side = 'buy' if signal.direction in ['call', 'put', 'long'] else 'sell'
        step = getattr(self.config, "fill_step_timeout", 3)

        remaining = quantity
        fills = []
        fill_stage = None
        last_order = None

        # Rung 1 — marketable limit at the ask.
        limit_price = round(quote['ask'], 2) if quote['ask'] else round(mid_price * 1.02, 2)
        last_order = self.api.submit_order(
            symbol=option_symbol, qty=remaining, side=side, type='limit',
            limit_price=limit_price, time_in_force='day',
            client_order_id=f"{signal.analyst}_{signal.message_id}")
        logger.info("Submitted option entry limit (rung 1): %s %d %s @ $%.2f",
                   side, remaining, option_symbol, limit_price)
        self._wait_for_fill(last_order.id, timeout=step)
        # Settle definitively before escalating — never resubmit over a raced fill.
        fq, fp = self._cancel_and_settle(last_order.id)
        fill_stage = 'limit'
        if fq > 0:
            fills.append((fq, fp)); remaining -= fq

        # Rung 2 — capped marketable limit at ask + cap, for the unfilled remainder.
        # NEVER a market order: skipping an entry costs nothing.
        if remaining > 0:
            q2 = self._get_option_quote(option_symbol) or quote
            ask2 = q2.get('ask') or quote.get('ask') or mid_price
            cap_price = round(ask2 + self._slippage(ask2), 2)
            logger.warning("Entry %s unfilled in %.1fs — repricing to capped limit $%.2f (rung 2)",
                          option_symbol, step, cap_price)
            last_order = self.api.submit_order(
                symbol=option_symbol, qty=remaining, side=side, type='limit',
                limit_price=cap_price, time_in_force='day',
                client_order_id=f"{signal.analyst}_{signal.message_id}_c")
            self._wait_for_fill(last_order.id, timeout=step)
            fq, fp = self._cancel_and_settle(last_order.id)
            fill_stage = 'capped'
            if fq > 0:
                fills.append((fq, fp)); remaining -= fq

        total_qty = sum(q for q, _ in fills)
        if total_qty == 0:
            logger.warning("Entry %s NOT filled — SKIPPING (entries never escalate to market)",
                          option_symbol)
            return {
                'order_id': last_order.id if last_order else None,
                'symbol': option_symbol, 'quantity': quantity, 'side': side,
                'status': 'skipped', 'fill_stage': 'skipped',
                'escalation': "entry SKIPPED — unfilled at capped limit",
                'filled_price': 0, 'filled_qty': 0,
            }

        avg_price = sum(q * p for q, p in fills) / total_qty
        return {
            'order_id': last_order.id if last_order else None,
            'symbol': option_symbol,
            'quantity': quantity,
            'side': side,
            'status': 'filled' if total_qty >= quantity else 'partially_filled',
            'fill_stage': fill_stage,
            'escalation': None,
            'filled_price': avg_price,
            'filled_qty': total_qty,
        }
    
    def _execute_stock_entry(self, signal: ParsedSignal, position_size: float) -> Optional[Dict[str, Any]]:
        """Execute stock entry order."""
        
        # Get current stock price
        quote = self._get_stock_quote(signal.ticker)
        if not quote:
            logger.error("Failed to get quote for %s", signal.ticker)
            return None
        
        current_price = quote.get('last', quote.get('close', 0))
        if current_price <= 0:
            logger.error("Invalid stock price for %s: %s", signal.ticker, current_price)
            return None
        
        # Calculate quantity
        quantity = max(1, int(position_size / current_price))
        
        # Place order
        side = 'buy' if signal.direction == 'long' else 'sell'
        
        order = self.api.submit_order(
            symbol=signal.ticker,
            qty=quantity,
            side=side,
            type='market',
            time_in_force='day',
            client_order_id=f"{signal.analyst}_{signal.message_id}"
        )
        
        logger.info("Submitted stock order: %s %d %s @ market", 
                   side, quantity, signal.ticker)
        
        # Wait for fill
        filled_order = self._wait_for_fill(order.id, timeout=30)
        
        return {
            'order_id': order.id,
            'symbol': signal.ticker,
            'quantity': quantity,
            'side': side,
            'status': filled_order.status if filled_order else 'pending',
            'filled_price': float(filled_order.filled_avg_price) if filled_order and filled_order.filled_avg_price else current_price,
            'filled_qty': int(filled_order.filled_qty) if filled_order else 0
        }
    
    def _execute_option_exit(self, signal: ParsedSignal, quantity: int) -> Optional[Dict[str, Any]]:
        """Execute options exit order."""
        
        option_symbol = self._build_option_symbol(signal)
        if not option_symbol:
            # Try position lookup first (most reliable for exits)
            logger.warning("Primary symbol build failed for exit %s — checking open positions", signal.ticker)
            option_symbol = self._find_option_symbol_from_positions(signal)
        if not option_symbol:
            # Final fallback: options chain resolve
            logger.warning("Position lookup failed for exit %s — trying options chain resolve", signal.ticker)
            option_symbol = self._resolve_option_symbol(signal)
        if not option_symbol:
            logger.error("Failed to build/resolve option symbol for exit: %s", signal.ticker)
            return None
        
        # Exit is opposite of entry — sell to close.
        side = 'sell'  # Assuming we're closing long positions
        step = getattr(self.config, "fill_step_timeout", 3)
        emerg_pct = getattr(self.config, "emergency_slippage_pct", 0.20)

        quote = self._get_option_quote(option_symbol)
        base = f"{signal.analyst}_{signal.message_id}_exit"

        # Build the ladder. Unlike entries, an unfilled exit is a live risk
        # (an open, unhedged position — the Blocker-1 scenario), so the tail
        # ends in a true market order to GUARANTEE the position goes flat.
        # Rungs 1-3 are bounded limits; only the final rung surrenders the price.
        if quote and quote.get('bid') and quote['bid'] > 0:
            bid = quote['bid']
            rungs = [
                ('limit', round(bid, 2), 'limit', base),
                ('limit', max(0.01, round(bid - self._slippage(bid), 2)), 'capped', base + "_c"),
                ('limit', max(0.01, round(bid - self._slippage(bid, pct=emerg_pct), 2)), 'emergency', base + "_e"),
                ('market', None, 'market', base + "_mkt"),
            ]
        else:
            # No bid to anchor a limit — go straight to market (as before).
            rungs = [('market', None, 'market', base + "_mkt")]

        last_order = None
        fill_stage = None
        escalation = None
        remaining = quantity
        fills = []            # (qty, avg_price) accumulated across rungs
        n = len(rungs)

        for i, (otype, price, label, coid) in enumerate(rungs):
            if remaining <= 0:
                break
            is_last = (i == n - 1)
            kwargs = dict(symbol=option_symbol, qty=remaining, side=side,
                          type=otype, time_in_force='day', client_order_id=coid)
            if otype == 'limit':
                kwargs['limit_price'] = price
                logger.info("Submitted option exit %s: %s %d %s @ $%.2f",
                           label, side, remaining, option_symbol, price)
            else:
                logger.warning("Option exit MARKET BACKSTOP fired for %s — all bounded "
                              "limits unfilled; taking any price to go flat", option_symbol)
            last_order = self.api.submit_order(**kwargs)
            filled_order = self._wait_for_fill(last_order.id, timeout=(10 if otype == 'market' else step))
            fill_stage = label

            if is_last:
                # Guaranteed-fill backstop — take whatever filled; never cancel it.
                fq, fp = self._order_fill(filled_order) if filled_order else (0, 0.0)
                if fq > 0:
                    fills.append((fq, fp)); remaining -= fq
                break

            # Not the last rung: settle definitively (the fill may have raced the
            # cancel) and escalate only the unfilled remainder — never resubmit
            # the full qty, or we double-fill.
            fq, fp = self._cancel_and_settle(last_order.id)
            if fq > 0:
                fills.append((fq, fp)); remaining -= fq
                logger.info("Exit rung '%s' settled with %d filled @ $%.2f — %d remaining",
                           label, fq, fp, remaining)
            if remaining > 0:
                logger.warning("Exit rung '%s' left %d unfilled — escalating", label, remaining)

        total_qty = sum(q for q, _ in fills)
        avg_price = (sum(q * p for q, p in fills) / total_qty) if total_qty else 0.0
        status = ('filled' if total_qty >= quantity
                  else 'partially_filled' if total_qty > 0 else 'pending')
        if fill_stage in ('emergency', 'market'):
            escalation = f"exit filled via {fill_stage} escalation (bounded limits unfilled)"

        return {
            'order_id': last_order.id if last_order else None,
            'symbol': option_symbol,
            'quantity': quantity,
            'side': side,
            'status': status,
            'fill_stage': fill_stage,
            'escalation': escalation,
            'filled_price': avg_price,
            'filled_qty': total_qty,
        }
    
    def _execute_stock_exit(self, signal: ParsedSignal, quantity: int) -> Optional[Dict[str, Any]]:
        """Execute stock exit order."""
        
        # Exit is opposite of entry
        side = 'sell' if signal.direction == 'long' else 'buy'
        
        order = self.api.submit_order(
            symbol=signal.ticker,
            qty=quantity,
            side=side,
            type='market',
            time_in_force='day',
            client_order_id=f"{signal.analyst}_{signal.message_id}_exit"
        )
        
        logger.info("Submitted stock exit: %s %d %s @ market", 
                   side, quantity, signal.ticker)
        
        filled_order = self._wait_for_fill(order.id, timeout=30)
        
        return {
            'order_id': order.id,
            'symbol': signal.ticker,
            'quantity': quantity,
            'side': side,
            'status': filled_order.status if filled_order else 'pending',
            'filled_price': float(filled_order.filled_avg_price) if filled_order and filled_order.filled_avg_price else 0,
            'filled_qty': int(filled_order.filled_qty) if filled_order else 0
        }
    
    def _build_option_symbol(self, signal: ParsedSignal) -> Optional[str]:
        """Build Alpaca option symbol from signal data."""
        
        if not all([signal.ticker, signal.expiry, signal.strike, signal.direction]):
            logger.error("Missing required option data: %s", signal)
            return None
        
        try:
            # Parse expiry date
            if 'T' in signal.expiry:
                expiry_date = datetime.fromisoformat(signal.expiry.replace('Z', '+00:00')).date()
            else:
                expiry_date = datetime.fromisoformat(signal.expiry).date()
            
            # Safety: if expiry is in the past, fix year to current year
            today = datetime.now(timezone.utc).date()
            if expiry_date < today:
                expiry_date = expiry_date.replace(year=today.year)
                logger.warning("Fixed stale expiry year → %s", expiry_date)
            
            # Format: TICKER + YYMMDD + C/P + strike*1000 (padded to 8 digits)
            # Example: SPY260221C00450000 (SPY, Feb 21 2026, Call, $450)
            
            date_str = expiry_date.strftime('%y%m%d')
            option_type = 'C' if signal.direction == 'call' else 'P'
            strike_str = f"{int(signal.strike * 1000):08d}"
            
            option_symbol = f"{signal.ticker}{date_str}{option_type}{strike_str}"
            
            return option_symbol
            
        except Exception:
            logger.exception("Failed to build option symbol from %s", signal)
            return None
    
    # ------------------------------------------------------------------
    # Options-chain fallback: resolve missing strike / expiry / direction
    # ------------------------------------------------------------------

    def _resolve_option_symbol(self, signal: ParsedSignal) -> Optional[str]:
        """Fallback: query Alpaca options chain to fill in missing fields and return an OCC symbol."""
        ticker = signal.ticker
        if not ticker:
            return None

        direction = signal.direction
        # Default direction when missing
        if direction in (None, 'long'):
            direction = 'call'
        elif direction == 'short':
            direction = 'put'
        option_type = 'call' if direction == 'call' else 'put'

        strike = signal.strike
        expiry_str = signal.expiry  # may be None

        headers = {
            'APCA-API-KEY-ID': self.config.alpaca_api_key,
            'APCA-API-SECRET-KEY': self.config.alpaca_secret_key,
        }
        today = datetime.now(timezone.utc).date()

        try:
            # --- Build query params ---
            params: Dict[str, Any] = {
                'underlying_symbols': ticker.upper(),
                'status': 'active',
                'type': option_type,
                'limit': 500,
            }

            if expiry_str:
                # We have an expiry – use exact date filter
                if 'T' in expiry_str:
                    exp_date = datetime.fromisoformat(expiry_str.replace('Z', '+00:00')).date()
                else:
                    exp_date = datetime.fromisoformat(expiry_str).date()
                if exp_date < today:
                    exp_date = exp_date.replace(year=today.year)
                params['expiration_date'] = exp_date.isoformat()
            else:
                # No expiry – get nearest available
                params['expiration_date_gte'] = today.isoformat()

            if strike is not None:
                params['strike_price_gte'] = str(strike)
                params['strike_price_lte'] = str(strike)

            resp = _requests.get(
                f'{self.config.alpaca_base_url}/v2/options/contracts',
                headers=headers,
                params=params,
                timeout=10,
            )
            resp.raise_for_status()
            contracts = resp.json().get('option_contracts', [])

            if not contracts:
                logger.warning("No contracts found for resolve query: %s", params)
                return None

            # --- Pick best contract ---
            if strike is None:
                # Need ATM – get current stock price
                stock_quote = self._get_stock_quote(ticker)
                current_price = stock_quote.get('last', 0) if stock_quote else 0
                if current_price <= 0:
                    logger.error("Cannot determine ATM – no stock price for %s", ticker)
                    return None
                # Pick contract with strike closest to current price
                contracts.sort(key=lambda c: abs(float(c.get('strike_price', 0)) - current_price))

            if not expiry_str:
                # Pick nearest expiry among candidates
                contracts.sort(key=lambda c: c.get('expiration_date', '9999-99-99'))

            chosen = contracts[0]
            symbol = chosen.get('symbol')
            logger.info(
                "Resolved option symbol via chain lookup: %s (strike=%s, expiry=%s, type=%s) for signal %s",
                symbol,
                chosen.get('strike_price'),
                chosen.get('expiration_date'),
                option_type,
                signal.ticker,
            )
            return symbol

        except Exception:
            logger.exception("_resolve_option_symbol failed for %s", ticker)
            return None

    def _find_option_symbol_from_positions(self, signal: ParsedSignal) -> Optional[str]:
        """For exits: look up the option symbol from open Alpaca positions matching this signal's ticker."""
        try:
            positions = self.api.list_positions()
            ticker_upper = signal.ticker.upper() if signal.ticker else ''
            matches = []
            for pos in positions:
                sym = pos.symbol
                # OCC symbols start with underlying ticker
                if sym.startswith(ticker_upper) and len(sym) > len(ticker_upper):
                    # Optionally filter by direction (C/P)
                    if signal.direction in ('call', None, 'long') and 'C' in sym[len(ticker_upper):len(ticker_upper)+7]:
                        matches.append(pos)
                    elif signal.direction in ('put', 'short') and 'P' in sym[len(ticker_upper):len(ticker_upper)+7]:
                        matches.append(pos)
                    elif signal.direction is None:
                        matches.append(pos)
            if len(matches) == 1:
                logger.info("Found option position for exit: %s", matches[0].symbol)
                return matches[0].symbol
            elif len(matches) > 1:
                # If strike is known, narrow down
                if signal.strike:
                    for m in matches:
                        strike_part = m.symbol[-8:]
                        try:
                            pos_strike = int(strike_part) / 1000
                            if abs(pos_strike - signal.strike) < 0.01:
                                logger.info("Matched exit position by strike: %s", m.symbol)
                                return m.symbol
                        except ValueError:
                            pass
                # Return first match as fallback
                logger.info("Multiple position matches for %s exit, using first: %s", ticker_upper, matches[0].symbol)
                return matches[0].symbol
            return None
        except Exception:
            logger.exception("Error looking up positions for exit symbol")
            return None

    def _get_option_quote(self, option_symbol: str) -> Optional[Dict[str, Any]]:
        """Get current option quote via Alpaca options data API with contract fallback."""
        try:
            import requests
            
            # Options use a separate data API endpoint (not the stock endpoint)
            headers = {
                'APCA-API-KEY-ID': self.config.alpaca_api_key,
                'APCA-API-SECRET-KEY': self.config.alpaca_secret_key,
            }
            
            # TRY METHOD 1: Direct quote API (known to fail with "invalid symbol")
            try:
                resp = requests.get(
                    'https://data.alpaca.markets/v1beta1/options/quotes/latest',
                    headers=headers,
                    params={'symbols': option_symbol},
                    timeout=10
                )
                resp.raise_for_status()
                data = resp.json()
                
                quote_data = data.get('quotes', {}).get(option_symbol)
                if quote_data:
                    bid = float(quote_data.get('bp', 0))
                    ask = float(quote_data.get('ap', 0))
                    
                    # Try to get last trade price
                    last_price = 0
                    try:
                        trade_resp = requests.get(
                            'https://data.alpaca.markets/v1beta1/options/trades/latest',
                            headers=headers,
                            params={'symbols': option_symbol},
                            timeout=10
                        )
                        if trade_resp.status_code == 200:
                            trade_data = trade_resp.json()
                            trade_info = trade_data.get('trades', {}).get(option_symbol)
                            if trade_info:
                                last_price = float(trade_info.get('p', 0))
                    except Exception:
                        pass
                    
                    if not last_price and bid and ask:
                        last_price = (bid + ask) / 2
                    
                    logger.debug("Quote API success for %s: bid=%.2f ask=%.2f last=%.2f", 
                               option_symbol, bid, ask, last_price)
                    return {
                        'bid': bid,
                        'ask': ask,
                        'last': last_price
                    }
            except Exception as quote_error:
                logger.warning("Quote API failed for %s: %s - trying contract fallback", 
                             option_symbol, str(quote_error))
            
            # METHOD 2: Contract endpoint fallback (bypass quotes entirely)
            logger.info("Using contract endpoint fallback for %s", option_symbol)
            
            # Parse the underlying symbol from option_symbol (e.g., QQQ260218P00593000 -> QQQ)
            # Find where the date portion starts (first digit after letters)
            underlying = ''
            for i, ch in enumerate(option_symbol):
                if ch.isdigit():
                    underlying = option_symbol[:i]
                    break
            if not underlying:
                underlying = option_symbol[:3]
            
            today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            
            contract_resp = requests.get(
                f'{self.config.alpaca_base_url}/v2/options/contracts',
                headers=headers,
                params={
                    'underlying_symbols': underlying,
                    'expiration_date_gte': today_str,
                    'limit': 1000
                },
                timeout=10
            )
            contract_resp.raise_for_status()
            contract_data = contract_resp.json()
            
            # Find our specific contract
            contracts = contract_data.get('option_contracts', [])
            target_contract = None
            for contract in contracts:
                if contract.get('symbol') == option_symbol:
                    target_contract = contract
                    break
            
            if not target_contract:
                logger.error("Contract not found in API response for %s", option_symbol)
                return None
            
            # Extract pricing from contract data (contracts include mark prices)
            mark_price = target_contract.get('close_price', 0) or target_contract.get('mark_price', 0)
            
            if not mark_price:
                logger.error("No pricing data in contract for %s", option_symbol)
                return None
            
            # Estimate bid/ask from mark (typical 5% spread for options)
            spread_pct = 0.05
            spread = max(0.01, mark_price * spread_pct)  # Min $0.01 spread
            bid = max(0.01, mark_price - spread/2)
            ask = mark_price + spread/2
            
            logger.info("Contract fallback success for %s: mark=%.2f (bid=%.2f ask=%.2f)", 
                       option_symbol, mark_price, bid, ask)
            
            return {
                'bid': bid,
                'ask': ask,
                'last': mark_price
            }
            
        except Exception as e:
            logger.error("All quote methods failed for %s: %s", option_symbol, str(e))
            return None
    
    def _get_stock_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get current stock quote."""
        try:
            quote = self.api.get_latest_quote(symbol)
            
            # Get last trade price separately
            last_price = 0
            try:
                trade = self.api.get_latest_trade(symbol)
                last_price = float(trade.price) if trade.price else 0
            except Exception:
                # Fallback to latest bar close price
                try:
                    bars = self.api.get_bars(symbol, tradeapi.rest.TimeFrame.Minute, limit=1)
                    if bars and len(bars) > 0:
                        last_price = float(bars[0].c)  # close price
                except Exception:
                    # Final fallback to midpoint
                    if quote.bid_price and quote.ask_price:
                        last_price = (float(quote.bid_price) + float(quote.ask_price)) / 2
            
            return {
                'bid': float(quote.bid_price) if quote.bid_price else 0,
                'ask': float(quote.ask_price) if quote.ask_price else 0,
                'last': last_price
            }
        except Exception:
            logger.exception("Failed to get stock quote for %s", symbol)
            return None
    
    def _wait_for_fill(self, order_id: str, timeout: float = 30,
                       poll_interval: Optional[float] = None) -> Optional[Any]:
        """Wait for order to fill with timeout, polling every ``poll_interval`` s
        (default from config — 0.5s, so a marketable limit's fill is detected
        fast and we don't sit through dead time before escalating)."""

        if poll_interval is None:
            poll_interval = getattr(self.config, "fill_poll_interval", 0.5)
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                order = self.api.get_order(order_id)

                if order.status in ['filled', 'partially_filled']:
                    logger.info("Order %s filled: %s shares @ $%s",
                               order_id, order.filled_qty, order.filled_avg_price)
                    return order
                elif order.status in ['cancelled', 'rejected']:
                    logger.error("Order %s failed: %s", order_id, order.status)
                    return order

                time.sleep(poll_interval)

            except Exception:
                logger.exception("Error checking order status for %s", order_id)
                break

        logger.warning("Order %s timeout after %.1f seconds", order_id, timeout)
        return None

    # ------------------------------------------------------------------
    # Blocker 3 helpers — bounded fill ladder (no naked market orders)
    # ------------------------------------------------------------------

    @staticmethod
    def _filled_ok(order: Optional[Any]) -> bool:
        return order is not None and getattr(order, "status", None) in ("filled", "partially_filled")

    def _slippage(self, price: float, pct: Optional[float] = None) -> float:
        """How far past the quote we'll pay: max(pct·price, abs floor). The abs
        floor lets a cheap option cross a spread that a bare % would round away."""
        if pct is None:
            pct = getattr(self.config, "slippage_cap_pct", 0.05)
        abs_floor = getattr(self.config, "slippage_cap_abs", 0.03)
        return max(round(price * pct, 2), abs_floor)

    def _cancel_quietly(self, order_id: str) -> None:
        try:
            self.api.cancel_order(order_id)
        except Exception:
            pass

    def _cancel_and_settle(self, order_id: str) -> tuple:
        """Cancel a rung and return what it ACTUALLY filled: (filled_qty, avg_price).

        A cancel can lose a race with a fill — the order fills in the moment
        between our last poll and the cancel landing. If we ignored that and
        resubmitted the full quantity for the next rung, we'd execute twice and
        end up in an unintended opposite position (the SPY 720P double-fill on
        08-03: sold 2, held 1, went short). So after cancelling we read the
        order's terminal state and report any real fill, so the ladder only ever
        escalates the *unfilled remainder*. Returns (0, 0.0) if truly unfilled."""
        try:
            self.api.cancel_order(order_id)
        except Exception:
            pass  # already filled/cancelled — the terminal read below is truth
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                o = self.api.get_order(order_id)
            except Exception:
                break
            if getattr(o, "status", None) in (
                "canceled", "cancelled", "filled", "rejected", "expired", "done_for_day"
            ):
                return self._order_fill(o)
            time.sleep(0.2)
        try:
            return self._order_fill(self.api.get_order(order_id))
        except Exception:
            return 0, 0.0

    @staticmethod
    def _order_fill(o: Any) -> tuple:
        """(filled_qty, filled_avg_price) from an order, defaulting to (0, 0.0)."""
        fq = int(getattr(o, "filled_qty", 0) or 0)
        fp = float(o.filled_avg_price) if getattr(o, "filled_avg_price", None) else 0.0
        return fq, fp
    
    def get_account_info(self) -> Optional[Dict[str, Any]]:
        """Get account information and buying power."""
        try:
            account = self.api.get_account()
            return {
                'buying_power': float(account.buying_power),
                'cash': float(account.cash),
                'portfolio_value': float(account.portfolio_value),
                'status': account.status
            }
        except Exception:
            logger.exception("Failed to get account info")
            return None