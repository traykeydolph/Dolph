#!/usr/bin/env python3

import sqlite3
import json
from datetime import datetime, timedelta

# Connect to database
conn = sqlite3.connect('trading_bot.db')
cursor = conn.cursor()

# Get today's date
today = datetime.now().strftime('%Y-%m-%d')

# Get today's trades
cursor.execute('''
SELECT 
    id, ticker, action, quantity, entry_price, executed_price, 
    pnl, created_at, executed_at, analyst, asset_type,
    strike, expiry, direction, status
FROM trades 
WHERE DATE(created_at) = ? OR DATE(executed_at) = ?
ORDER BY created_at DESC
''', (today, today))

trades = cursor.fetchall()

# Get current positions
cursor.execute('''
SELECT ticker, current_quantity, entry_price, analyst, direction, strike, expiry, status
FROM positions 
WHERE current_quantity != 0 AND status = 'open'
ORDER BY id
''')

positions = cursor.fetchall()

print(f'=== TRADING BOT DAILY SUMMARY - {today} ===')
print(f'Time: {datetime.now().strftime("%H:%M:%S")}')
print()

print('TODAY\'S TRADES:')
if trades:
    total_pnl = 0
    for trade in trades:
        id, ticker, action, qty, entry_price, executed_price, pnl, created_at, executed_at, analyst, asset_type, strike, expiry, direction, status = trade
        
        if asset_type == 'option' and strike and expiry and direction:
            display_ticker = f'{ticker} {strike}{direction} {expiry}'
        else:
            display_ticker = ticker
            
        price = executed_price if executed_price else entry_price
        pnl_str = f'${pnl:.2f}' if pnl else status.upper()
        action_emoji = '🟢' if action == 'BUY' else '🔴'
        
        print(f'{action_emoji} {action} {qty}x {display_ticker} @ ${price} | {analyst} | {pnl_str}')
        if pnl:
            total_pnl += pnl
    
    print(f'\nTODAY\'S P&L: ${total_pnl:.2f}')
else:
    print('No trades today')

print('\nCURRENT POSITIONS:')
if positions:
    for pos in positions:
        ticker, qty, entry_price, analyst, direction, strike, expiry, status = pos
        
        if strike and expiry and direction:
            display_ticker = f'{ticker} {strike}{direction} {expiry}'
        else:
            display_ticker = ticker
            
        print(f'• {qty}x {display_ticker} @ ${entry_price} | {analyst}')
else:
    print('No open positions')

conn.close()