#!/usr/bin/env python3
"""Test Alpaca API connection and basic functionality."""

import sys
import os
sys.path.append(os.path.dirname(__file__))

from config import Config
from execution.alpaca_client import AlpacaClient
from parsers.base import ParsedSignal, SignalAction, AssetType

def test_connection():
    """Test basic Alpaca connection."""
    print("Testing Alpaca connection...")
    
    try:
        config = Config()
        client = AlpacaClient(config)
        
        # Get account info
        account = client.get_account_info()
        if account:
            print(f"✓ Connected to Alpaca")
            print(f"  Account status: {account['status']}")
            print(f"  Buying power: ${account['buying_power']}")
            print(f"  Cash: ${account['cash']}")
            print(f"  Portfolio value: ${account['portfolio_value']}")
        else:
            print("✗ Failed to get account info")
            return False
            
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return False
        
    return True

def test_quote():
    """Test getting a stock quote."""
    print("\nTesting stock quote...")
    
    try:
        config = Config()
        client = AlpacaClient(config)
        
        # Test SPY quote (should always work)
        quote = client._get_stock_quote("SPY")
        if quote:
            print(f"✓ SPY quote: bid=${quote['bid']}, ask=${quote['ask']}, last=${quote['last']}")
        else:
            print("✗ Failed to get SPY quote")
            return False
            
    except Exception as e:
        print(f"✗ Quote test failed: {e}")
        return False
        
    return True

def test_option_symbol():
    """Test option symbol building."""
    print("\nTesting option symbol building...")
    
    try:
        config = Config()
        client = AlpacaClient(config)
        
        # Create a test signal
        signal = ParsedSignal(
            ticker="SPY",
            action=SignalAction.ENTRY,
            direction="call",
            strike=450.0,
            expiry="2026-02-20",
            asset_type=AssetType.OPTION,
            analyst="test",
            message_id="test123",
            confidence=0.8,
            raw_message="Test signal",
            entry_price=45.0,
            trim_fraction=None,
            timestamp="2026-02-17T10:00:00Z"
        )
        
        symbol = client._build_option_symbol(signal)
        if symbol:
            print(f"✓ Option symbol: {symbol}")
            # Format should be: SPY260220C00450000
        else:
            print("✗ Failed to build option symbol")
            return False
            
    except Exception as e:
        print(f"✗ Option symbol test failed: {e}")
        return False
        
    return True

def main():
    """Run all tests."""
    print("=== Alpaca API Test Suite ===")
    
    tests = [
        test_connection,
        test_quote,
        test_option_symbol,
    ]
    
    passed = 0
    for test in tests:
        if test():
            passed += 1
        print()
    
    print(f"Results: {passed}/{len(tests)} tests passed")
    
    if passed == len(tests):
        print("✓ All tests passed - Alpaca client looks good!")
    else:
        print("✗ Some tests failed - check the errors above")

if __name__ == "__main__":
    main()