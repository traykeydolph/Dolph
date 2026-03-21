#!/usr/bin/env python3
"""
Kalshi Prediction Market Bot
Follows Grizzlies' BTC direction calls → places Kalshi bets
"""

import json
import time
import sqlite3
import requests
import base64
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

# ============================================================
# CONFIGURATION
# ============================================================
CONFIG_DIR = Path(__file__).parent / "config"
DB_PATH = Path(__file__).parent / "trading_bot.db"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
GRIZZLIES_CHANNEL = "1025803258691862678"

# Telegram alerts — load from .env or environment
def _load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())
_load_env()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Strategy settings
MAX_BET_FRACTION = 0.75       # Max % of balance per bet (leave 25% cash)
MIN_BET_AMOUNT = 100          # Minimum bet in cents ($1)
COMPOUND_RATE = 0.75          # Compound 75%, cash out 25%
COOLDOWN_HOURS = 4            # Min hours between bets on same direction
DRY_RUN = False               # 🔴 LIVE MODE

# ============================================================
# KALSHI API CLIENT
# ============================================================
class KalshiClient:
    def __init__(self):
        with open(CONFIG_DIR / "kalshi_credentials.json") as f:
            self.creds = json.load(f)
        with open(CONFIG_DIR / "kalshi_private_key.pem") as f:
            self.private_key = serialization.load_pem_private_key(
                f.read().encode(), password=None, backend=default_backend()
            )
        self.api_key = self.creds["api_key"]
    
    def _sign(self, method, path):
        timestamp = str(int(time.time() * 1000))
        message = timestamp + method.upper() + "/trade-api/v2" + path
        signature = self.private_key.sign(
            message.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return {
            'KALSHI-ACCESS-KEY': self.api_key,
            'KALSHI-ACCESS-SIGNATURE': base64.b64encode(signature).decode(),
            'KALSHI-ACCESS-TIMESTAMP': timestamp,
            'Content-Type': 'application/json'
        }
    
    def get(self, path, params=None):
        headers = self._sign("GET", path)
        resp = requests.get(KALSHI_BASE + path, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()
    
    def post(self, path, data=None):
        headers = self._sign("POST", path)
        resp = requests.post(KALSHI_BASE + path, headers=headers, json=data)
        resp.raise_for_status()
        return resp.json()
    
    def get_balance(self):
        """Returns balance in cents"""
        data = self.get("/portfolio/balance")
        return data.get("balance", 0)
    
    def get_positions(self):
        """Get open positions"""
        data = self.get("/portfolio/positions")
        return data.get("market_positions", [])
    
    def get_btc_markets(self, target_date=None):
        """
        Get BTC daily above/below markets.
        target_date: 'YYYY-MM-DD' or None for nearest available
        """
        data = self.get("/events", params={
            "status": "open",
            "series_ticker": "KXBTCD",
            "limit": 5
        })
        events = data.get("events", [])
        
        if not events:
            return None, []
        
        # Find best matching event
        best_event = None
        for e in events:
            title = e.get("title", "").lower()
            if target_date:
                # Try to match the date in the title
                # "Bitcoin price on Mar 13, 2026 at 5pm EDT?"
                if target_date in title or self._date_matches(title, target_date):
                    best_event = e
                    break
            else:
                # Use the nearest future event
                best_event = e
        
        if not best_event:
            best_event = events[0]
        
        event_ticker = best_event.get("event_ticker")
        
        # Get markets for this event
        mkt_data = self.get("/markets", params={
            "event_ticker": event_ticker,
            "limit": 75
        })
        markets = mkt_data.get("markets", [])
        
        return best_event, markets
    
    def _date_matches(self, title, target_date):
        """Check if a title contains the target date"""
        from datetime import datetime
        try:
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            month_abbr = dt.strftime("%b").lower()  # 'mar'
            day = str(dt.day)
            return month_abbr in title and f" {day}" in title
        except:
            return False
    
    def place_order(self, ticker, side, count, yes_price=None, no_price=None):
        """
        Place an order on Kalshi.
        side: 'yes' or 'no'
        count: number of contracts
        yes_price/no_price: limit price in cents (1-99)
        """
        order = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": count,
            "type": "market"  # market order for speed
        }
        
        # Use limit order if price specified
        if side == "yes" and yes_price:
            order["type"] = "limit"
            order["yes_price"] = yes_price
        elif side == "no" and no_price:
            order["type"] = "limit"
            order["no_price"] = no_price
        
        if DRY_RUN:
            print(f"  🧪 DRY RUN — would place: {json.dumps(order, indent=2)}")
            return {"dry_run": True, "order": order}
        
        return self.post("/portfolio/orders", order)


# ============================================================
# SIGNAL PARSER
# ============================================================
class GrizzliesParser:
    """Detect BTC direction signals from Grizzlies' Discord messages"""
    
    # BTC direction patterns
    BTC_LONG_PATTERNS = [
        r'btc\s+long',
        r'bitcoin\s+long', 
        r'long\s+btc',
        r'long\s+bitcoin',
    ]
    
    BTC_SHORT_PATTERNS = [
        r'btc\s+short',
        r'bitcoin\s+short',
        r'short\s+btc',
        r'short\s+bitcoin',
    ]
    
    # IBIT direction patterns (puts = SHORT, calls = LONG)
    IBIT_PUT_PATTERN = r'ibit\s+(\d+\.?\d*)\s*p\s+(\d+/\d+/\d+)'
    IBIT_CALL_PATTERN = r'ibit\s+(\d+\.?\d*)\s*c\s+(\d+/\d+/\d+)'
    
    # Skip patterns (TP updates, trims, report cards, etc)
    SKIP_PATTERNS = [
        r'tp\d?\s+hit',
        r'all\s+tp',
        r'report\s+card',
        r'trim',
        r'closed?\s+(my|the|here)',
        r'stopped?\s+out',
        r'still\s+holding',
        r'printing',
        r'up\s+\d+%',
        r'set\s+stops',
        r'hedging',  # Skip hedge signals per rules
    ]
    
    def parse(self, content):
        """
        Parse a Grizzlies message for BTC direction signal.
        Returns: {
            'direction': 'LONG' or 'SHORT',
            'source': 'BTC' or 'IBIT',
            'expiry': 'YYYY-MM-DD' or None,
            'entry_price': float or None,
            'confidence': float
        } or None
        """
        cl = content.lower().strip()
        
        # Skip non-signal messages
        for pattern in self.SKIP_PATTERNS:
            if re.search(pattern, cl):
                return None
        
        # Check for structured BTC entry (highest confidence)
        if 'entry:' in cl:
            for pattern in self.BTC_LONG_PATTERNS:
                if re.search(pattern, cl):
                    entry_price = self._extract_entry_price(cl)
                    return {
                        'direction': 'LONG',
                        'source': 'BTC',
                        'expiry': None,  # Use nearest daily market
                        'entry_price': entry_price,
                        'confidence': 1.0
                    }
            for pattern in self.BTC_SHORT_PATTERNS:
                if re.search(pattern, cl):
                    entry_price = self._extract_entry_price(cl)
                    return {
                        'direction': 'SHORT',
                        'source': 'BTC',
                        'expiry': None,
                        'entry_price': entry_price,
                        'confidence': 1.0
                    }
        
        # Check for IBIT options (direction + expiry)
        put_match = re.search(self.IBIT_PUT_PATTERN, cl)
        if put_match:
            strike = float(put_match.group(1))
            expiry = self._parse_expiry(put_match.group(2))
            entry_price = self._extract_option_price(cl)
            return {
                'direction': 'SHORT',
                'source': 'IBIT',
                'expiry': expiry,
                'strike': strike,
                'entry_price': entry_price,
                'confidence': 0.9
            }
        
        call_match = re.search(self.IBIT_CALL_PATTERN, cl)
        if call_match:
            strike = float(call_match.group(1))
            expiry = self._parse_expiry(call_match.group(2))
            entry_price = self._extract_option_price(cl)
            return {
                'direction': 'LONG',
                'source': 'IBIT',
                'expiry': expiry,
                'strike': strike,
                'entry_price': entry_price,
                'confidence': 0.9
            }
        
        # Simple direction mentions without structured entry (lower confidence)
        for pattern in self.BTC_SHORT_PATTERNS:
            if re.search(pattern, cl):
                return {
                    'direction': 'SHORT',
                    'source': 'BTC',
                    'expiry': None,
                    'entry_price': None,
                    'confidence': 0.7
                }
        
        for pattern in self.BTC_LONG_PATTERNS:
            if re.search(pattern, cl):
                return {
                    'direction': 'LONG',
                    'source': 'BTC',
                    'expiry': None,
                    'entry_price': None,
                    'confidence': 0.7
                }
        
        return None
    
    def _extract_entry_price(self, text):
        """Extract first entry price from structured signal"""
        match = re.search(r'entry:?\s*\n?\s*1\)\s*(\d+[\d,]*)', text)
        if match:
            return float(match.group(1).replace(',', ''))
        return None
    
    def _extract_option_price(self, text):
        """Extract @ price from options entry"""
        match = re.search(r'@\s*(\d+\.?\d*)', text)
        if match:
            return float(match.group(1))
        return None
    
    def _parse_expiry(self, date_str):
        """Parse M/D/YY to YYYY-MM-DD"""
        try:
            parts = date_str.split('/')
            month, day, year = int(parts[0]), int(parts[1]), int(parts[2])
            if year < 100:
                year += 2000
            return f"{year}-{month:02d}-{day:02d}"
        except:
            return None


# ============================================================
# MARKET SELECTOR
# ============================================================
class MarketSelector:
    """Pick the optimal Kalshi contract given a direction signal"""
    
    def __init__(self, client):
        self.client = client
    
    def select(self, signal):
        """
        Given a parsed signal, find the best Kalshi market to bet on.
        Returns: {
            'ticker': str,
            'side': 'yes' or 'no',
            'title': str,
            'price': int (cents),
            'payout_ratio': float,
            'event_title': str,
            'target_date': str
        } or None
        """
        direction = signal['direction']
        expiry = signal.get('expiry')
        
        # Get BTC markets for the target date
        event, markets = self.client.get_btc_markets(target_date=expiry)
        
        if not markets:
            print("  ⚠️ No BTC markets available")
            return None
        
        # Filter to tradeable markets (has bids/asks)
        tradeable = [m for m in markets if m.get('yes_bid') and m.get('yes_ask')]
        
        if not tradeable:
            print("  ⚠️ No tradeable markets (no liquidity)")
            return None
        
        # Strategy:
        # SHORT → buy NO on a strike just ABOVE current implied price (sweet spot: 35-55¢ yes price)
        # LONG → buy YES on a strike just BELOW current implied price (sweet spot: 45-65¢ yes price)
        
        if direction == 'SHORT':
            # We want: high-probability NO contracts
            # Look for YES prices in 35-55¢ range (meaning NO costs 45-65¢)
            # This gives us 1.5x-2.2x payout
            candidates = [m for m in tradeable if 30 <= m.get('yes_bid', 0) <= 60]
            if not candidates:
                candidates = [m for m in tradeable if 20 <= m.get('yes_bid', 0) <= 70]
            if not candidates:
                print("  ⚠️ No good SHORT candidates in price range")
                return None
            
            # Pick the one closest to 45¢ YES (55¢ NO) — balanced risk/reward
            candidates.sort(key=lambda m: abs(45 - m.get('yes_bid', 0)))
            best = candidates[0]
            
            no_price = 100 - best.get('yes_bid', 50)
            return {
                'ticker': best['ticker'],
                'side': 'no',
                'title': best.get('subtitle', '') or best.get('title', ''),
                'price': no_price,
                'yes_price': best.get('yes_bid', 0),
                'payout_ratio': round(100 / no_price, 2),
                'event_title': event.get('title', ''),
                'target_date': expiry or 'nearest'
            }
        
        else:  # LONG
            # We want: high-probability YES contracts
            # Look for YES prices in 45-65¢ range
            candidates = [m for m in tradeable if 40 <= m.get('yes_bid', 0) <= 70]
            if not candidates:
                candidates = [m for m in tradeable if 30 <= m.get('yes_bid', 0) <= 80]
            if not candidates:
                print("  ⚠️ No good LONG candidates in price range")
                return None
            
            # Pick closest to 55¢ YES
            candidates.sort(key=lambda m: abs(55 - m.get('yes_bid', 0)))
            best = candidates[0]
            
            yes_price = best.get('yes_ask', best.get('yes_bid', 50))
            return {
                'ticker': best['ticker'],
                'side': 'yes',
                'title': best.get('subtitle', '') or best.get('title', ''),
                'price': yes_price,
                'yes_price': yes_price,
                'payout_ratio': round(100 / yes_price, 2),
                'event_title': event.get('title', ''),
                'target_date': expiry or 'nearest'
            }


# ============================================================
# BET TRACKER
# ============================================================
class BetTracker:
    """Track bets, P&L, and enforce rules"""
    
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self._init_db()
    
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kalshi_bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                signal_source TEXT,
                signal_direction TEXT,
                signal_content TEXT,
                kalshi_ticker TEXT,
                kalshi_side TEXT,
                kalshi_title TEXT,
                num_contracts INTEGER,
                cost_cents INTEGER,
                price_per_contract INTEGER,
                payout_if_win INTEGER,
                result TEXT DEFAULT 'open',
                profit_cents INTEGER DEFAULT 0,
                dry_run BOOLEAN DEFAULT 1,
                notes TEXT
            )
        """)
        conn.commit()
        conn.close()
    
    def record_bet(self, signal, market, num_contracts, cost_cents, dry_run=True):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO kalshi_bets 
            (timestamp, signal_source, signal_direction, signal_content,
             kalshi_ticker, kalshi_side, kalshi_title, num_contracts,
             cost_cents, price_per_contract, payout_if_win, dry_run)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            signal.get('source', ''),
            signal.get('direction', ''),
            signal.get('raw_content', '')[:500],
            market['ticker'],
            market['side'],
            market['title'],
            num_contracts,
            cost_cents,
            market['price'],
            num_contracts * 100,  # each contract pays $1 (100¢) if right
            dry_run
        ))
        conn.commit()
        conn.close()
    
    def get_last_bet_time(self):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT timestamp FROM kalshi_bets ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return datetime.fromisoformat(row[0])
        return None
    
    def get_open_bets(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT * FROM kalshi_bets WHERE result = 'open'"
        ).fetchall()
        conn.close()
        return rows
    
    def get_stats(self):
        conn = sqlite3.connect(self.db_path)
        stats = {}
        stats['total'] = conn.execute("SELECT COUNT(*) FROM kalshi_bets").fetchone()[0]
        stats['wins'] = conn.execute("SELECT COUNT(*) FROM kalshi_bets WHERE result='win'").fetchone()[0]
        stats['losses'] = conn.execute("SELECT COUNT(*) FROM kalshi_bets WHERE result='loss'").fetchone()[0]
        stats['open'] = conn.execute("SELECT COUNT(*) FROM kalshi_bets WHERE result='open'").fetchone()[0]
        stats['total_profit'] = conn.execute("SELECT COALESCE(SUM(profit_cents), 0) FROM kalshi_bets WHERE result != 'open'").fetchone()[0]
        conn.close()
        return stats


# ============================================================
# TELEGRAM ALERTS
# ============================================================
def send_telegram(message):
    """Send alert via Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"  📱 [No TG config] {message}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception as e:
        print(f"  ⚠️ Telegram error: {e}")


# ============================================================
# MAIN BOT
# ============================================================
class KalshiBot:
    def __init__(self):
        self.client = KalshiClient()
        self.parser = GrizzliesParser()
        self.selector = MarketSelector(self.client)
        self.tracker = BetTracker()
        self.last_processed_id = self._get_last_processed_id()
    
    def _get_last_processed_id(self):
        """Get the last message ID we processed for Kalshi"""
        try:
            with open(CONFIG_DIR / "kalshi_state.json") as f:
                state = json.load(f)
                return state.get("last_processed_ts", "")
        except FileNotFoundError:
            return ""
    
    def _save_state(self, last_ts):
        with open(CONFIG_DIR / "kalshi_state.json", "w") as f:
            json.dump({"last_processed_ts": last_ts}, f)
    
    def check_new_signals(self):
        """Check for new Grizzlies BTC signals in the trading bot DB"""
        conn = sqlite3.connect(DB_PATH)
        
        # Get messages newer than last processed
        rows = conn.execute("""
            SELECT content, processed_at, parsed_as
            FROM message_log
            WHERE channel_id = ?
            AND processed_at > ?
            ORDER BY processed_at ASC
        """, (GRIZZLIES_CHANNEL, self.last_processed_id)).fetchall()
        
        conn.close()
        
        signals = []
        for content, ts, parsed_as in rows:
            signal = self.parser.parse(content)
            if signal:
                signal['raw_content'] = content
                signal['timestamp'] = ts
                signals.append(signal)
            self.last_processed_id = ts
        
        if rows:
            self._save_state(self.last_processed_id)
        
        return signals
    
    def execute_signal(self, signal):
        """Process a signal → select market → place bet"""
        direction = signal['direction']
        source = signal['source']
        
        print(f"\n{'='*60}")
        print(f"🎯 SIGNAL: {source} {direction}")
        print(f"   Time: {signal.get('timestamp')}")
        if signal.get('expiry'):
            print(f"   Expiry: {signal['expiry']}")
        print(f"   Confidence: {signal.get('confidence', 0)}")
        print(f"{'='*60}")
        
        # Check cooldown
        last_bet = self.tracker.get_last_bet_time()
        if last_bet:
            hours_since = (datetime.now() - last_bet).total_seconds() / 3600
            if hours_since < COOLDOWN_HOURS:
                msg = f"  ⏳ Cooldown: {hours_since:.1f}h since last bet (need {COOLDOWN_HOURS}h)"
                print(msg)
                return None
        
        # Skip low confidence signals
        if signal.get('confidence', 0) < 0.7:
            print(f"  ⚠️ Low confidence ({signal.get('confidence')}), skipping")
            return None
        
        # Select market
        market = self.selector.select(signal)
        if not market:
            print("  ❌ No suitable market found")
            return None
        
        print(f"  📊 Market: {market['title']}")
        print(f"     Event: {market['event_title']}")
        print(f"     Side: {market['side'].upper()}")
        print(f"     Price: {market['price']}¢ per contract")
        print(f"     Payout: {market['payout_ratio']}x if correct")
        
        # Calculate position size
        balance = self.client.get_balance()
        bet_amount = int(balance * MAX_BET_FRACTION)
        bet_amount = max(bet_amount, MIN_BET_AMOUNT)
        bet_amount = min(bet_amount, balance)  # Can't bet more than we have
        
        num_contracts = bet_amount // market['price']
        if num_contracts < 1:
            print(f"  ❌ Insufficient balance ({balance}¢) for minimum bet")
            return None
        
        cost = num_contracts * market['price']
        potential_payout = num_contracts * 100
        potential_profit = potential_payout - cost
        
        print(f"\n  💰 Position:")
        print(f"     Balance: ${balance/100:.2f}")
        print(f"     Bet: {num_contracts} contracts × {market['price']}¢ = ${cost/100:.2f}")
        print(f"     If WIN: ${potential_payout/100:.2f} (+${potential_profit/100:.2f}, +{potential_profit/cost*100:.0f}%)")
        print(f"     If LOSE: -${cost/100:.2f}")
        
        # Place order (Kalshi requires limit orders with price in cents)
        price_kwarg = {}
        if market['side'] == 'yes':
            price_kwarg['yes_price'] = market['price']
        else:
            price_kwarg['no_price'] = market['price']
        
        result = self.client.place_order(
            ticker=market['ticker'],
            side=market['side'],
            count=num_contracts,
            **price_kwarg
        )
        
        # Record bet
        self.tracker.record_bet(signal, market, num_contracts, cost, dry_run=DRY_RUN)
        
        # Send alert
        mode = "🧪 PAPER" if DRY_RUN else "🔴 LIVE"
        alert = (
            f"{mode} KALSHI BET PLACED\n\n"
            f"Signal: Grizzlies {source} {direction}\n"
            f"Market: {market['title']}\n"
            f"Side: {market['side'].upper()}\n"
            f"Contracts: {num_contracts} × {market['price']}¢\n"
            f"Cost: ${cost/100:.2f}\n"
            f"Payout if win: ${potential_payout/100:.2f} (+{potential_profit/cost*100:.0f}%)\n"
            f"Balance: ${balance/100:.2f}"
        )
        print(f"\n{alert}")
        send_telegram(alert)
        
        return result
    
    def run_once(self):
        """Single check cycle"""
        signals = self.check_new_signals()
        
        if not signals:
            return 0
        
        print(f"\n📡 Found {len(signals)} new BTC direction signal(s)")
        
        executed = 0
        for signal in signals:
            result = self.execute_signal(signal)
            if result is not None:
                executed += 1
        
        return executed
    
    def status(self):
        """Print current status"""
        balance = self.client.get_balance()
        positions = self.client.get_positions()
        stats = self.tracker.get_stats()
        
        print(f"\n{'='*60}")
        print(f"📊 KALSHI BOT STATUS")
        print(f"{'='*60}")
        print(f"  Balance: ${balance/100:.2f}")
        print(f"  Mode: {'🧪 DRY RUN' if DRY_RUN else '🔴 LIVE'}")
        print(f"  Open positions: {len(positions)}")
        print(f"\n  Bet History:")
        print(f"    Total: {stats['total']}")
        print(f"    Wins: {stats['wins']}")
        print(f"    Losses: {stats['losses']}")
        print(f"    Open: {stats['open']}")
        print(f"    P&L: ${stats['total_profit']/100:.2f}")
        print(f"{'='*60}")


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    import sys
    
    bot = KalshiBot()
    
    if len(sys.argv) < 2:
        print("Usage: python kalshi_bot.py [status|check|backtest|live]")
        print("  status   - Show balance and bet history")
        print("  check    - Check for new signals (dry run)")
        print("  backtest - Replay all historical signals")
        print("  live     - Enable live trading and check")
        sys.exit(0)
    
    cmd = sys.argv[1]
    
    if cmd == "status":
        bot.status()
    
    elif cmd == "check":
        bot.run_once()
    
    elif cmd == "backtest":
        # Replay ALL historical Grizzlies messages
        print("📜 BACKTESTING all historical Grizzlies signals...")
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT content, processed_at
            FROM message_log
            WHERE channel_id = ?
            ORDER BY processed_at ASC
        """, (GRIZZLIES_CHANNEL,)).fetchall()
        conn.close()
        
        parser = GrizzliesParser()
        signals = []
        for content, ts in rows:
            signal = parser.parse(content)
            if signal:
                signal['raw_content'] = content[:100].replace('\n', ' ')
                signal['timestamp'] = ts
                signals.append(signal)
        
        print(f"\nFound {len(signals)} signals in {len(rows)} messages\n")
        for i, s in enumerate(signals):
            exp = f" (exp: {s['expiry']})" if s.get('expiry') else ""
            print(f"  {i+1:2d}. {s['timestamp']} | {s['source']:4s} {s['direction']:5s} | conf: {s['confidence']}{exp}")
            print(f"      {s['raw_content'][:80]}")
    
    elif cmd == "live" or cmd == "monitor":
        # Continuous monitoring mode — polls every 30s for new signals
        print("🔴 KALSHI BOT — LIVE MONITORING")
        print(f"   Balance: ${bot.client.get_balance()/100:.2f}")
        print(f"   Polling every 30s for Grizzlies BTC signals...")
        print(f"   Ctrl+C to stop\n")
        
        sys.stdout.flush()
        send_telegram("🔴 Kalshi Bot LIVE — monitoring Grizzlies for BTC signals")
        
        check_count = 0
        try:
            while True:
                executed = bot.run_once()
                check_count += 1
                if executed:
                    print(f"  ✅ Executed {executed} bet(s)")
                elif check_count % 60 == 0:  # Status every ~30 min
                    bal = bot.client.get_balance()
                    print(f"  💤 [{datetime.now().strftime('%H:%M')}] No new signals. Balance: ${bal/100:.2f}")
                time.sleep(30)
        except KeyboardInterrupt:
            print("\n⏹️ Kalshi bot stopped.")
        except Exception as e:
            print(f"  ⚠️ Fatal error: {e}")
            send_telegram(f"⚠️ Kalshi Bot crashed: {e}")
    
    else:
        print(f"Unknown command: {cmd}")
