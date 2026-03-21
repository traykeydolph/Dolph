#!/usr/bin/env python3
"""Test SPX to SPY converter functionality."""

import sys
import os
sys.path.append(os.path.dirname(__file__))

from config import Config
from execution.spx_converter import SPXConverter
from execution.position_manager import PositionManager, Position
from storage.database import Database
from parsers.base import ParsedSignal, SignalAction, AssetType

def test_spx_conversion():
    """Test SPX to SPY conversion scenarios."""
    print("=== SPX Converter Test Suite ===")
    
    config = Config()
    db = Database(config.db_path)
    converter = SPXConverter()
    position_mgr = PositionManager(config, db)
    
    # Test 1: Entry signal with complete details
    print("\n1. Testing SPX entry signal (complete details)...")
    entry_signal = ParsedSignal(
        analyst="grizzlies",
        action=SignalAction.ENTRY,
        asset_type=AssetType.OPTION,
        ticker="SPX",
        direction="call",
        strike=5800.0,
        expiry="2026-02-21",
        entry_price=45.0,
        trim_fraction=None,
        confidence=0.8,
        raw_message="SPX 5800C entry @ 45.00",
        message_id="test123",
        timestamp="2026-02-17T10:00:00Z"
    )
    
    converted = converter.convert_signal(entry_signal, position_mgr)
    if converted:
        print(f"✓ Converted: {entry_signal.ticker} ${entry_signal.strike} → {converted.ticker} ${converted.strike}")
        print(f"  Entry price: ${entry_signal.entry_price} → ${converted.entry_price}")
    else:
        print("✗ Conversion failed")
    
    # Test 2: Check if SPX signal detection works
    print("\n2. Testing SPX signal detection...")
    is_spx = converter.is_spx_signal(entry_signal)
    print(f"✓ SPX detection: {is_spx}")
    
    # Test 3: Trim signal without complete details (simulating real scenario)
    print("\n3. Testing SPX trim signal (missing strike/expiry)...")
    trim_signal = ParsedSignal(
        analyst="grizzlies",
        action=SignalAction.TRIM,
        asset_type=AssetType.OPTION,
        ticker="SPX",
        direction=None,
        strike=None,  # Missing - should be filled from position
        expiry=None,  # Missing - should be filled from position
        entry_price=None,
        trim_fraction=0.5,
        confidence=0.8,
        raw_message="Trim SPX here",
        message_id="test456",
        timestamp="2026-02-17T11:00:00Z"
    )
    
    # Check if there's an existing SPX position we can use
    existing_position = position_mgr.find_position("SPX", "grizzlies")
    if existing_position:
        print(f"  Found existing SPX position: ${existing_position.strike} exp {existing_position.expiry}")
    else:
        print("  No existing SPX position found")
    
    converted_trim = converter.convert_signal(trim_signal, position_mgr)
    if converted_trim:
        print(f"✓ Converted trim: SPX → {converted_trim.ticker} ${converted_trim.strike}")
    else:
        print("✗ Trim conversion failed (expected if no existing position)")
    
    # Test 4: Non-SPX signal (should pass through unchanged)
    print("\n4. Testing non-SPX signal passthrough...")
    spy_signal = ParsedSignal(
        analyst="grizzlies",
        action=SignalAction.ENTRY,
        asset_type=AssetType.OPTION,
        ticker="SPY",
        direction="put",
        strike=450.0,
        expiry="2026-02-21",
        entry_price=25.0,
        trim_fraction=None,
        confidence=0.9,
        raw_message="SPY 450P entry",
        message_id="test789",
        timestamp="2026-02-17T12:00:00Z"
    )
    
    passthrough = converter.convert_signal(spy_signal, position_mgr)
    if passthrough and passthrough.ticker == "SPY":
        print("✓ SPY signal passed through unchanged")
    else:
        print("✗ SPY signal conversion error")
    
    print("\n=== Test Complete ===")

if __name__ == "__main__":
    test_spx_conversion()