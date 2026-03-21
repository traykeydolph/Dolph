#!/usr/bin/env python3
"""Quick position reconciliation check"""

import sqlite3
import sys
from pathlib import Path

# Add the trading directory to Python path
sys.path.append(str(Path(__file__).parent))

from config import Config

try:
    # Check database positions
    conn = sqlite3.connect('trading_bot.db')
    db_positions = conn.execute('SELECT * FROM positions WHERE status="open"').fetchall()
    conn.close()
    
    print("=== DATABASE POSITIONS ===")
    for pos in db_positions:
        print(f"ID: {pos[0]}, Source: {pos[1]}, Symbol: {pos[2]}, Type: {pos[3]}, Strike: {pos[4]}, Expiry: {pos[5]}, Entry: ${pos[6]}, Qty: {pos[7]}, Status: {pos[11]}")
    
    # Check if we can access Alpaca
    try:
        from alpaca_trade_api import REST
        
        config = Config()
        api = REST(
            config.alpaca_api_key,
            config.alpaca_secret_key,
            base_url=config.alpaca_base_url
        )
        
        print("\n=== ALPACA POSITIONS ===")
        positions = api.list_positions()
        if positions:
            for pos in positions:
                print(f"Symbol: {pos.symbol}, Qty: {pos.qty}, Entry: ${pos.avg_entry_price}, P&L: ${pos.unrealized_pl}")
        else:
            print("No open positions in Alpaca")
            
    except Exception as e:
        print(f"\n❌ Could not connect to Alpaca: {e}")
        
except Exception as e:
    print(f"Error: {e}")