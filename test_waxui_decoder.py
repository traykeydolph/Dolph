#!/usr/bin/env python3
"""Test Waxui obfuscated ticker decoding integration."""

import sys
import os
sys.path.append(os.path.dirname(__file__))

from config import Config
from signal_router import SignalRouter

def test_waxui_integration():
    """Test the full signal routing with Waxui obfuscated tickers."""
    print("=== Waxui Decoder Integration Test ===")
    
    config = Config()
    router = SignalRouter(config)
    
    # Test messages based on actual Waxui patterns found in database
    test_cases = [
        {
            'content': 'SPXXX\n5.40 - 9.20 ✅ 70%\nHolding runners only',
            'expected_ticker': 'SPX',
            'description': 'SPXXX obfuscated pattern'
        },
        {
            'content': 'SPXXXXXX\n5.40 - 10.50 ✅ 94%\nHolding last cons',
            'expected_ticker': 'SPX',
            'description': 'SPXXXXXX obfuscated pattern'
        },
        {
            'content': 'SPX XXX entry here\nLooking for breakout',
            'expected_ticker': 'SPX',
            'description': 'SPX XXX spaced pattern'
        },
        {
            'content': 'Regular SPY message\n450P entry',
            'expected_ticker': 'SPY',
            'description': 'Regular SPY (no obfuscation)'
        },
        {
            'content': 'S&P 500 calls looking good',
            'expected_ticker': 'SPX',
            'description': 'S&P 500 reference'
        }
    ]
    
    print(f"Testing with Waxui channel ID: {config.discord_channel_waxui}")
    print()
    
    for i, case in enumerate(test_cases, 1):
        print(f"{i}. {case['description']}")
        print(f"   Original: {case['content'][:50]}...")
        
        # Test ticker decoding directly
        decoded = router.ticker_decoder.decode_message(case['content'], config.discord_channel_waxui)
        print(f"   Decoded:  {decoded[:50]}...")
        
        # Test full signal routing (this would normally call Gemini)
        try:
            # Just test the preprocessing part without calling Gemini API
            is_obfuscated = router.ticker_decoder.is_obfuscated_message(case['content'])
            extracted_ticker = router.ticker_decoder.extract_ticker_from_message(case['content'])
            
            print(f"   Obfuscated: {is_obfuscated}")
            print(f"   Extracted ticker: {extracted_ticker}")
            print(f"   Expected ticker: {case['expected_ticker']}")
            
            if extracted_ticker == case['expected_ticker']:
                print("   ✓ SUCCESS")
            else:
                print("   ✗ FAILED")
            
        except Exception as e:
            print(f"   ✗ ERROR: {e}")
        
        print()
    
    # Test with actual database messages
    print("=== Testing with real database messages ===")
    from storage.database import Database
    
    db = Database(config.db_path)
    cursor = db.conn.execute('''
        SELECT message_id, content 
        FROM message_log 
        WHERE content LIKE '%SPXXX%' 
           OR content LIKE '%SPXXXXXX%'
        LIMIT 5
    ''')
    
    real_messages = cursor.fetchall()
    for msg_id, content in real_messages:
        print(f"Message {msg_id}:")
        print(f"  Original: {content[:100]}...")
        decoded = router.ticker_decoder.decode_message(content, config.discord_channel_waxui)
        print(f"  Decoded:  {decoded[:100]}...")
        ticker = router.ticker_decoder.extract_ticker_from_message(content)
        print(f"  Ticker:   {ticker}")
        print()


if __name__ == "__main__":
    test_waxui_integration()