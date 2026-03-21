"""Enhanced Market-specific parser enhancements and post-processing."""

import logging
import re
import json
from typing import Optional, Dict, Any

from parsers.base import ParsedSignal, SignalAction, AssetType

logger = logging.getLogger(__name__)


class EnhancedMarketParser:
    """Enhanced Market-specific parsing logic and validation."""
    
    # Enhanced Market uses structured Discord embeds
    ENTRY_INDICATORS = ['entry', 'buy', 'long', 'call', 'put']
    EXIT_INDICATORS = ['exit', 'sell', 'close', 'take profit', 'stop loss']
    
    @staticmethod
    def enhance_prompt(base_prompt: str, message: str) -> str:
        """Add Enhanced Market-specific context to parsing prompt."""
        
        em_context = """
ENHANCED MARKET ANALYST SPECIFIC RULES:
- Uses structured Discord embeds converted to text format
- Signal format: "Title: 🟢 ENTERING $TICKER STRIKE[C/P] MM/DD Description: N Contracts @ PRICE"
- Exit format: "Title: 🔴 EXITING $TICKER STRIKE[C/P] MM/DD Description: N Contracts @ PRICE"
- Trim format: "Title: ✂️ TRIMMING $TICKER..." or partial exit descriptions

ENTRY DETECTION:
- "🟢 ENTERING" or "ENTERING" in title = ENTRY signal (action: "entry")
- "$TICKER" format — extract ticker after the $ sign
- "280C" = strike 280, direction call; "410P" = strike 410, direction put
- "02/06" = expiry 2026-02-06
- "380 Contracts @ 1.05" = entry_price is 1.05

EXIT DETECTION:
- "🔴 EXITING" or "EXITING" in title = EXIT signal
- "🔴 SOLD" in title = EXIT signal — ticker is the word AFTER "SOLD" (e.g., "SOLD META 620P" → ticker META, NOT "SOLD")
- "CLOSED" = EXIT signal
- CRITICAL: "SOLD" is NOT a ticker — it's an action word. The ticker follows it.

TRIM DETECTION:
- "✂️ TRIMMING" or partial sells = TRIM signal
- "Took profits" / "Trimmed" = TRIM signal
- trim_fraction: 0.2 per trim step

- Confidence should be HIGH (0.9+) for structured embed signals
- These are the most machine-readable signals of all three analysts
"""
        
        return base_prompt + em_context

    @staticmethod
    def post_process_signal(signal: ParsedSignal, original_message: str) -> ParsedSignal:
        """Apply Enhanced Market-specific post-processing and validation."""
        
        if not signal:
            return signal
            
        # Enhanced Market typically provides cleaner signals
        # Boost confidence for well-structured signals
        if EnhancedMarketParser._is_structured_signal(original_message):
            signal.confidence = min(signal.confidence + 0.1, 1.0)
            
        # Extract additional details from structured content
        structured_data = EnhancedMarketParser._parse_structured_content(original_message)
        if structured_data:
            # Override with structured data if available
            if 'entry_price' in structured_data and structured_data['entry_price']:
                signal.entry_price = structured_data['entry_price']
            if 'ticker' in structured_data and structured_data['ticker']:
                signal.ticker = structured_data['ticker'].upper()
            if 'action' in structured_data and structured_data['action']:
                signal.action = structured_data['action']
                
        return signal
    
    @staticmethod
    def _is_structured_signal(message: str) -> bool:
        """Detect if this is a structured embed signal."""
        # Look for embed-like structure indicators
        indicators = ['Title:', 'Description:', 'Entry:', 'Exit:', 'Stop:']
        return any(indicator in message for indicator in indicators)
    
    @staticmethod
    def _parse_structured_content(message: str) -> Dict[str, Any]:
        """Parse structured embed content for Enhanced Market."""
        data = {}
        
        try:
            # Parse line by line for structured fields
            lines = message.split('\n')
            
            for line in lines:
                line = line.strip()
                
                # Extract title (ticker)
                if line.startswith('Title:'):
                    title = line.replace('Title:', '').strip()
                    # Action words that are NOT tickers
                    action_words = {'ENTERING', 'EXITING', 'SOLD', 'TRIMMING', 'BOUGHT', 'CLOSED', 'TRIM', 'BUY', 'SELL'}
                    # Find all uppercase words and take the first one that's not an action/emoji
                    ticker_matches = re.findall(r'\b([A-Z]{1,5})\b', title)
                    for match in ticker_matches:
                        if match not in action_words:
                            data['ticker'] = match
                            break
                    
                    # Detect action from title
                    title_upper = title.upper()
                    if 'SOLD' in title_upper or 'EXITING' in title_upper or 'CLOSED' in title_upper:
                        data['action'] = SignalAction.EXIT
                    elif 'ENTERING' in title_upper or 'BOUGHT' in title_upper:
                        data['action'] = SignalAction.ENTRY
                    elif 'TRIMMING' in title_upper or 'TRIM' in title_upper:
                        data['action'] = SignalAction.TRIM
                
                # Extract entry price
                elif 'entry' in line.lower() and ':' in line:
                    price_match = re.search(r'([0-9]+(?:\.[0-9]+)?)', line)
                    if price_match:
                        data['entry_price'] = float(price_match.group(1))
                
                # Extract exit price  
                elif 'exit' in line.lower() and ':' in line:
                    price_match = re.search(r'([0-9]+(?:\.[0-9]+)?)', line)
                    if price_match:
                        data['exit_price'] = float(price_match.group(1))
                
                # Extract stop loss
                elif 'stop' in line.lower() and ':' in line:
                    price_match = re.search(r'([0-9]+(?:\.[0-9]+)?)', line)
                    if price_match:
                        data['stop_loss'] = float(price_match.group(1))
            
            # Determine action based on content
            message_lower = message.lower()
            if any(indicator in message_lower for indicator in EnhancedMarketParser.ENTRY_INDICATORS):
                data['action'] = SignalAction.ENTRY
            elif any(indicator in message_lower for indicator in EnhancedMarketParser.EXIT_INDICATORS):
                data['action'] = SignalAction.EXIT
                
        except Exception:
            logger.exception("Failed to parse structured content")
            
        return data
    
    @staticmethod
    def extract_embed_signals(embed_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract trading signal from Discord embed data structure."""
        try:
            signal_data = {}
            
            # Extract from embed title
            if 'title' in embed_data and embed_data['title']:
                title = embed_data['title']
                ticker_match = re.search(r'\b([A-Z]{1,5})\b', title)
                if ticker_match:
                    signal_data['ticker'] = ticker_match.group(1)
                    
                # Detect action from title
                title_lower = title.lower()
                if any(word in title_lower for word in ['buy', 'long', 'entry']):
                    signal_data['action'] = SignalAction.ENTRY
                elif any(word in title_lower for word in ['sell', 'short', 'exit']):
                    signal_data['action'] = SignalAction.EXIT
            
            # Extract from embed description
            if 'description' in embed_data and embed_data['description']:
                desc = embed_data['description']
                # Look for price information
                prices = re.findall(r'\$?([0-9]+(?:\.[0-9]+)?)', desc)
                if prices:
                    signal_data['entry_price'] = float(prices[0])
            
            # Extract from embed fields
            if 'fields' in embed_data and embed_data['fields']:
                for field in embed_data['fields']:
                    name = field.get('name', '').lower()
                    value = field.get('value', '')
                    
                    if 'entry' in name or 'price' in name:
                        price_match = re.search(r'([0-9]+(?:\.[0-9]+)?)', value)
                        if price_match:
                            signal_data['entry_price'] = float(price_match.group(1))
                    
                    elif 'ticker' in name or 'symbol' in name:
                        ticker_match = re.search(r'\b([A-Z]{1,5})\b', value)
                        if ticker_match:
                            signal_data['ticker'] = ticker_match.group(1)
                    
                    elif 'action' in name or 'direction' in name:
                        value_lower = value.lower()
                        if any(word in value_lower for word in ['buy', 'long', 'call']):
                            signal_data['action'] = SignalAction.ENTRY
                            signal_data['direction'] = 'long'
                        elif any(word in value_lower for word in ['sell', 'short', 'put']):
                            signal_data['action'] = SignalAction.ENTRY  
                            signal_data['direction'] = 'short'
            
            return signal_data if signal_data else None
            
        except Exception:
            logger.exception("Failed to extract embed signals")
            return None