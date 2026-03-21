"""Ticker Decoder — handles obfuscated tickers from Discord channels."""

import re
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class TickerDecoder:
    """Decodes obfuscated ticker symbols used to avoid Discord bots/scrapers."""
    
    # Mapping of obfuscated patterns to real tickers
    OBFUSCATED_PATTERNS = {
        # Waxui SPX obfuscation patterns - match exact patterns
        r'\bSPX{3,}\b': 'SPX',           # SPXXX, SPXXXX, SPXXXXX, etc. (word boundaries)
        r'\bSPX\s+X{2,}\b': 'SPX',      # SPX XXX, SPX XXXX, etc.
        r'\bSP\s+X{3,}\b': 'SPX',       # SP XXX, SP XXXX, etc.
        r'\bS\s*P\s*X{3,}\b': 'SPX',    # S P XXX, S P XXXX, etc.
        
        # Common obfuscation patterns for other tickers
        r'\bSPY{2,}\b': 'SPY',          # SPYYY, SPYYYY, etc.
        r'\bQQQ{2,}\b': 'QQQ',          # QQQQ, QQQQQ, etc.
        r'\bIWM{2,}\b': 'IWM',          # IWMM, IWMMM, etc.
        
        # S&P 500 variations
        r'\bS&P\s*5?0?0?\b': 'SPX',     # S&P, S&P 500, S&P500, etc.
        r'\bSP\s*500\b': 'SPX',         # SP 500, SP500
    }
    
    def __init__(self):
        # Compile regex patterns for performance
        self._compiled_patterns = {}
        for pattern, ticker in self.OBFUSCATED_PATTERNS.items():
            try:
                self._compiled_patterns[re.compile(pattern, re.IGNORECASE)] = ticker
            except re.error:
                logger.warning("Invalid regex pattern: %s", pattern)
    
    def decode_message(self, content: str, channel_id: str = None) -> str:
        """Decode obfuscated tickers in message content."""
        
        if not content or not content.strip():
            return content
        
        decoded_content = content
        replacements_made = []
        
        # Apply all obfuscation patterns
        for regex, real_ticker in self._compiled_patterns.items():
            matches = regex.findall(decoded_content)
            if matches:
                for match in matches:
                    # Replace the obfuscated ticker with the real one
                    decoded_content = regex.sub(real_ticker, decoded_content, count=1)
                    replacements_made.append(f"{match} → {real_ticker}")
        
        # Log replacements if any were made
        if replacements_made:
            logger.info("Decoded tickers in message %s: %s", 
                       channel_id or "unknown", ", ".join(replacements_made))
            
        return decoded_content
    
    def extract_ticker_from_message(self, content: str) -> Optional[str]:
        """Extract the primary ticker from a decoded message."""
        
        # First decode the message
        decoded = self.decode_message(content)
        
        # Common ticker patterns (after decoding)
        ticker_patterns = [
            r'\b(SPX|SPY|QQQ|IWM|AAPL|MSFT|TSLA|NVDA|AMZN|GOOGL|META|NFLX)\b',  # Major tickers
            r'\b([A-Z]{2,5})\b',  # Generic 2-5 letter tickers
        ]
        
        for pattern in ticker_patterns:
            matches = re.findall(pattern, decoded, re.IGNORECASE)
            if matches:
                # Return the first match (uppercase)
                ticker = matches[0].upper()
                if len(ticker) >= 2:  # Valid ticker length
                    return ticker
        
        return None
    
    def is_obfuscated_message(self, content: str) -> bool:
        """Check if a message contains obfuscated tickers."""
        
        if not content:
            return False
            
        for regex in self._compiled_patterns.keys():
            if regex.search(content):
                return True
                
        return False
    
    def get_decoding_stats(self) -> Dict[str, int]:
        """Get statistics on decoding patterns used."""
        
        # This would be enhanced to track usage stats
        return {
            "patterns_loaded": len(self._compiled_patterns),
            "spx_patterns": sum(1 for ticker in self.OBFUSCATED_PATTERNS.values() if ticker == 'SPX')
        }


# Test function for development
def test_ticker_decoder():
    """Test the ticker decoder with sample messages."""
    
    decoder = TickerDecoder()
    
    test_messages = [
        "SPXXX 5.40 - 9.20 ✅ 70%",
        "SPXXXXXX 5.40 - 10.50 ✅ 94%", 
        "SPX XXX entry here",
        "SP XXX calls looking good",
        "S&P 500 puts",
        "Regular SPY message",
        "QQQ entry here",
    ]
    
    print("=== Ticker Decoder Test ===")
    for msg in test_messages:
        decoded = decoder.decode_message(msg)
        ticker = decoder.extract_ticker_from_message(msg)
        is_obf = decoder.is_obfuscated_message(msg)
        
        print(f"Original: {msg}")
        print(f"Decoded:  {decoded}")
        print(f"Ticker:   {ticker}")
        print(f"Obfuscated: {is_obf}")
        print()


if __name__ == "__main__":
    test_ticker_decoder()