"""Obsidian Signal Library Matcher — Tier 1 (exact) and Tier 2 (fuzzy) matching.

Reads validated signals from the Obsidian vault's Signal Library folder.
Each analyst has 4 files: Entry.md, Trim.md, Exit.md, Noise.md
Each file contains signal entries with raw content in code blocks.

The folder structure IS the classification:
  - Found in Entry.md → ENTRY
  - Found in Trim.md → TRIM
  - Found in Exit.md → EXIT
  - Found in Noise.md → NOISE
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

SIGNAL_LIBRARY_PATH = "/Users/tray/Documents/Dolph & Tray/Trading/Signal Library"

# Map analyst names in config to folder names in vault
ANALYST_FOLDER_MAP = {
    "grizzlies": "Grizzlies",
    "waxui": "Waxui",
    "enhanced_market": "Enhanced Market",
    "ecs": "ECS",
    "eva": "Eva",
    "nando": "Nando",
    "zabes": "Zabes",
}

SIGNAL_TYPES = ["Entry", "Trim", "Exit", "Noise"]


@dataclass
class LibraryMatch:
    signal_type: str        # ENTRY, TRIM, EXIT, NOISE
    score: float            # 1.0 = exact, 0.0-0.99 = fuzzy
    matched_content: str    # The library entry that matched
    tier: int               # 1 = exact, 2 = fuzzy


def normalize(text: str) -> str:
    """Normalize message text for matching.
    
    Strips Discord mentions, extra whitespace, role pings,
    and lowercases for comparison.
    """
    if not text:
        return ""
    # Remove Discord role/user mentions
    text = re.sub(r'<@[&!]?\d+>', '', text)
    # Remove @alerts placeholder
    text = text.replace('@alerts', '')
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text.lower()


def tokenize(text: str) -> set:
    """Tokenize normalized text into word set for fuzzy matching."""
    normalized = normalize(text)
    # Split on non-alphanumeric, keep meaningful tokens
    tokens = re.findall(r'[a-z0-9]+(?:\.[0-9]+)?', normalized)
    # Filter out very short tokens (1 char) except known ones
    return {t for t in tokens if len(t) > 1 or t in ('c', 'p')}


def load_library(analyst: str) -> dict[str, list[str]]:
    """Load all signal entries from an analyst's library folder.
    
    Returns: {"ENTRY": [content1, content2, ...], "TRIM": [...], ...}
    """
    folder = ANALYST_FOLDER_MAP.get(analyst)
    if not folder:
        logger.warning("No library folder mapping for analyst: %s", analyst)
        return {}
    
    base_path = os.path.join(SIGNAL_LIBRARY_PATH, folder)
    if not os.path.isdir(base_path):
        logger.warning("Library folder not found: %s", base_path)
        return {}
    
    library = {}
    for signal_type in SIGNAL_TYPES:
        filepath = os.path.join(base_path, f"{signal_type}.md")
        if not os.path.isfile(filepath):
            library[signal_type.upper()] = []
            continue
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract all code blocks (raw signal content)
            entries = re.findall(r'```\n(.*?)\n```', content, re.DOTALL)
            library[signal_type.upper()] = [e.strip() for e in entries if e.strip()]
            
        except Exception:
            logger.exception("Failed to read library file: %s", filepath)
            library[signal_type.upper()] = []
    
    total = sum(len(v) for v in library.values())
    logger.info("Loaded %d library entries for %s (%s)", 
                total, analyst,
                ", ".join(f"{k}:{len(v)}" for k, v in library.items() if v))
    
    return library


# Cache loaded libraries to avoid re-reading files every signal
_library_cache: dict[str, dict[str, list[str]]] = {}
_library_cache_mtime: dict[str, float] = {}


def get_library(analyst: str) -> dict[str, list[str]]:
    """Get library with simple file-mtime caching.
    
    Reloads if any library file has been modified since last load.
    This means Tray can edit Obsidian and the bot picks up changes.
    """
    folder = ANALYST_FOLDER_MAP.get(analyst, "")
    base_path = os.path.join(SIGNAL_LIBRARY_PATH, folder)
    
    # Check if any file changed
    current_mtime = 0
    for signal_type in SIGNAL_TYPES:
        filepath = os.path.join(base_path, f"{signal_type}.md")
        try:
            current_mtime = max(current_mtime, os.path.getmtime(filepath))
        except OSError:
            pass
    
    cached_mtime = _library_cache_mtime.get(analyst, 0)
    if analyst in _library_cache and current_mtime <= cached_mtime:
        return _library_cache[analyst]
    
    # Reload
    library = load_library(analyst)
    _library_cache[analyst] = library
    _library_cache_mtime[analyst] = current_mtime
    return library


def tier1_exact_match(message: str, analyst: str) -> Optional[LibraryMatch]:
    """Tier 1: Exact match against library entries.
    
    Normalizes both sides (lowercase, strip mentions/whitespace) and compares.
    """
    library = get_library(analyst)
    if not library:
        return None
    
    normalized_msg = normalize(message)
    if not normalized_msg:
        return None
    
    for signal_type, entries in library.items():
        for entry in entries:
            if normalize(entry) == normalized_msg:
                logger.info("Tier 1 EXACT match: %s → %s", 
                           message[:80], signal_type)
                return LibraryMatch(
                    signal_type=signal_type,
                    score=1.0,
                    matched_content=entry,
                    tier=1,
                )
    
    return None


def tier2_fuzzy_match(message: str, analyst: str, 
                      threshold: float = 0.65) -> Optional[LibraryMatch]:
    """Tier 2: Fuzzy token overlap matching.
    
    Computes Jaccard similarity between token sets.
    Returns the best match above threshold, with the signal type
    determined by which file it came from.
    """
    library = get_library(analyst)
    if not library:
        return None
    
    msg_tokens = tokenize(message)
    if not msg_tokens:
        return None
    
    best_match: Optional[LibraryMatch] = None
    best_score = 0.0
    
    for signal_type, entries in library.items():
        for entry in entries:
            entry_tokens = tokenize(entry)
            if not entry_tokens:
                continue
            
            # Jaccard similarity
            intersection = msg_tokens & entry_tokens
            union = msg_tokens | entry_tokens
            score = len(intersection) / len(union) if union else 0
            
            if score > best_score:
                best_score = score
                best_match = LibraryMatch(
                    signal_type=signal_type,
                    score=score,
                    matched_content=entry,
                    tier=2,
                )
    
    if best_match and best_match.score >= threshold:
        logger.info("Tier 2 FUZZY match (%.2f): %s → %s", 
                    best_match.score, message[:80], best_match.signal_type)
        return best_match
    
    return None


def match(message: str, analyst: str) -> Optional[LibraryMatch]:
    """Run the full matching cascade: Tier 1 → Tier 2.
    
    Returns LibraryMatch if found, None if Tier 3 (Gemini) is needed.
    """
    # Tier 1: Exact match
    result = tier1_exact_match(message, analyst)
    if result:
        return result
    
    # Tier 2: Fuzzy match
    result = tier2_fuzzy_match(message, analyst)
    if result:
        return result
    
    # No match — caller should fall through to Tier 3 (Gemini)
    logger.debug("No library match for %s message: %s", analyst, message[:80])
    return None


def append_to_library(analyst: str, signal_type: str, content: str):
    """Append a new signal to the library (auto-learning from Tier 3).
    
    Called after Gemini classifies a signal — adds it to the correct
    analyst/type file so next time it's an exact match.
    """
    folder = ANALYST_FOLDER_MAP.get(analyst)
    if not folder:
        logger.warning("Cannot append: no folder mapping for %s", analyst)
        return
    
    signal_type_title = signal_type.title()
    if signal_type_title not in SIGNAL_TYPES:
        logger.warning("Invalid signal type for append: %s", signal_type)
        return
    
    filepath = os.path.join(SIGNAL_LIBRARY_PATH, folder, f"{signal_type_title}.md")
    
    # Clean content for storage
    clean = content.replace('<@&697950067285295115>', '@alerts').strip()
    
    try:
        # Read current count to get next signal number
        count = 0
        if os.path.isfile(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                count = len(re.findall(r'^## Signal \d+', f.read(), re.MULTILINE))
        
        # Build the new entry
        from datetime import datetime
        entry = f"\n## Signal {count + 1} — {signal_type.upper()} (auto-learned)\n"
        entry += f"- **Time:** {datetime.now().isoformat()}\n"
        entry += f"- **Source:** Tier 3 (Gemini Flash)\n"
        entry += f"\n```\n{clean}\n```\n"
        
        # Find insertion point (before the --- footer)
        with open(filepath, 'r', encoding='utf-8') as f:
            file_content = f.read()
        
        # Insert before the last "---" divider
        last_divider = file_content.rfind('\n---\n')
        if last_divider > 0:
            new_content = file_content[:last_divider] + entry + file_content[last_divider:]
        else:
            new_content = file_content + entry
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        # Invalidate cache
        if analyst in _library_cache:
            del _library_cache[analyst]
        
        logger.info("Appended new %s signal to %s/%s library", 
                    signal_type, analyst, signal_type_title)
        
    except Exception:
        logger.exception("Failed to append to library: %s/%s", analyst, signal_type_title)
