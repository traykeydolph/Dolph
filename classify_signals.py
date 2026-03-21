#!/usr/bin/env python3
"""Classify all 700 analyst signals using pattern matching + heuristics."""

import csv
import re
import os

INPUT = os.path.expanduser("~/Desktop/Analyst Signal Audit.csv")
OUTPUT = os.path.expanduser("~/Desktop/Analyst Signal Audit.csv")

def classify_grizzlies(row):
    content = row['content'] or ''
    ctx = row['reply_context'] or ''
    is_reply = row['is_reply'] == 'YES'
    cl = content.lower()
    
    # Crypto entry pattern: "Crypto play\n\nXxx long/short\n\nEntry:"
    if re.search(r'(crypto play|entry:?\s*\n)', cl) and not is_reply:
        ticker_match = re.search(r'(btc|eth|sol|ton|xrp|ada|doge|bnb|avax|link|dot|matic|sui|near|apt)\s+(long|short)', cl)
        if ticker_match:
            ticker = ticker_match.group(1).upper()
            direction = ticker_match.group(2).upper()
            return 'ENTRY', 'BTO' if direction == 'LONG' else 'STO', ticker, 'CRYPTO', '', '', 'HIGH', f'Crypto {direction.lower()} entry with targets'
    
    # Options entry: "TICKER STRIKE DATE @ PRICE"
    opt_match = re.search(r'([A-Z]{2,5})\s+(\d+[cp])\s+(\d+/\d+(?:/\d+)?)\s*\n?\s*@?\s*\$?(\d+\.?\d*)', cl, re.IGNORECASE)
    if opt_match and not is_reply:
        ticker = opt_match.group(1)
        strike = opt_match.group(2).upper()
        expiry = opt_match.group(3)
        conv = 'HIGH' if 'swing' in cl else 'MEDIUM'
        return 'ENTRY', 'BTO', ticker, 'OPTION', strike, expiry, conv, f'Options entry @${opt_match.group(4)}'
    
    # TP hit (reply to entry)
    if is_reply and re.search(r'tp\s*\d|bang', cl, re.IGNORECASE):
        # Try to extract ticker from reply context
        ticker = extract_ticker_from_context(ctx)
        asset = 'CRYPTO' if any(c in ctx.lower() for c in ['crypto play', 'btc', 'eth', 'sol']) else 'OPTION'
        return 'TRIM', 'STC', ticker, asset, '', '', 'HIGH', f'Take profit hit'
    
    # Stopped out
    if re.search(r'stopped?\s+(out|on entry)', cl, re.IGNORECASE):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'EXIT', 'STC', ticker, 'OPTION', '', '', 'HIGH', 'Stopped out'
    
    # "Bought more" (reply = add to position)
    if re.search(r'bought\s+more|avg\s+price|added', cl, re.IGNORECASE):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'ENTRY', 'BTO', ticker, 'OPTION', '', '', 'MEDIUM', 'Adding to position'
    
    # Trimming/taking gains
    if re.search(r'trimm?ing|taking gains|setting\s+stops', cl, re.IGNORECASE):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        asset = 'CRYPTO' if any(c in ctx.lower() for c in ['crypto play', 'btc', 'eth', 'sol']) else 'OPTION'
        return 'TRIM', 'STC', ticker, asset, '', '', 'MEDIUM', 'Trimming/taking gains'
    
    # ALL TPs hit
    if re.search(r'all\s+tps?\s+hit', cl, re.IGNORECASE):
        ticker = extract_ticker_from_context(ctx)
        return 'EXIT', 'STC', ticker, 'CRYPTO', '', '', 'HIGH', 'All targets hit - full exit'
    
    # "If anyone still holding" - update
    if re.search(r'if anyone|still holding|contracts?.+sitting', cl, re.IGNORECASE) and not re.search(r'entry|@\s*\d', cl):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'UPDATE', '', ticker, '', '', '', 'LOW', 'Position update/commentary'
    
    # Done for the day / monthly recap
    if re.search(r'done for (the day|today)|best of|on the (day|week)', cl, re.IGNORECASE):
        return 'NOISE', '', '', '', '', '', '', 'Daily recap/sign-off'
    
    # "Make sure" - advisory
    if re.search(r'make sure|be careful|watch out', cl, re.IGNORECASE):
        return 'ALERT', '', '', '', '', '', 'LOW', 'General advisory'
    
    # Emoji-only or very short
    if len(content.strip()) < 10 or re.match(r'^[\s\W]*$', content):
        return 'NOISE', '', '', '', '', '', '', 'Emoji/reaction only'
    
    # Default - check for any ticker mention
    ticker = extract_ticker_from_content(content)
    if ticker:
        return 'DISCUSSION', '', ticker, '', '', '', 'LOW', 'Ticker mentioned but no clear signal'
    
    return 'NOISE', '', '', '', '', '', '', 'General chat/commentary'

def classify_waxui(row):
    content = row['content'] or ''
    cl = content.lower()
    is_reply = row['is_reply'] == 'YES'
    
    # SPX/SPY entry: "SPX here\n DATE STRIKE\n Avg. PRICE"
    entry_match = re.search(r'(spy|spx|spyyy?|spxx+)\s+here\s*\n\s*(\d+/\d+)\s+(\d+[CP])\s*\n\s*avg\.?\s*,?\s*\$?(\d+\.?\d*)', cl, re.IGNORECASE)
    if entry_match:
        ticker = 'SPX' if 'spx' in entry_match.group(1).lower() else 'SPY'
        strike = entry_match.group(3).upper()
        expiry = entry_match.group(2)
        risk = 'HIGH' if 'high risk' in cl or 'lotto' in cl else 'MEDIUM'
        risk_note = ' (HIGH RISK)' if 'high risk' in cl else (' (LOTTO)' if 'lotto' in cl else '')
        return 'ENTRY', 'BTO', ticker, 'OPTION', strike, expiry, risk, f'Entry @${entry_match.group(4)}{risk_note}'
    
    # Stock entry: "MU/ARM/etc Day Trade idea"
    stock_idea = re.search(r'\*\*([A-Z]{2,5})\*\*,?\s*(day trade|swing|lotto)\s*idea', cl, re.IGNORECASE)
    if stock_idea:
        return 'ALERT', '', stock_idea.group(1), 'OPTION', '', '', 'MEDIUM', f'{stock_idea.group(2).title()} idea/watchlist'
    
    # Trim pattern: "TICKER\n ENTRY - EXIT ✅ XX%"
    trim_match = re.search(r'(spy|spx|spyyy?x*|[A-Z]{2,5})\s*\n?\s*(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\s*✅\s*(\d+)%', content, re.IGNORECASE)
    if trim_match:
        ticker = trim_match.group(1).upper()
        if 'SPX' in ticker or 'SPY' in ticker or ticker.startswith('SP'):
            ticker = 'SPX' if len(ticker) > 3 and 'X' in ticker.upper() else 'SPY'
        pct = trim_match.group(4)
        holding = ''
        if 'holding most' in cl: holding = 'holding most'
        elif 'holding majority' in cl: holding = 'holding majority'
        elif 'holding 1/2' in cl: holding = 'holding half'
        elif 'holding runner' in cl: holding = 'holding runners'
        elif 'holding last' in cl: holding = 'holding last cons'
        return 'TRIM', 'STC', ticker, 'OPTION', '', '', 'HIGH', f'+{pct}% trim, {holding}'
    
    # Closed pattern
    if re.search(r'closed?\s+(spy|spx|[a-z]{2,5})\s+here', cl, re.IGNORECASE):
        ticker_m = re.search(r'closed?\s+([a-z]{2,5})\s+here', cl, re.IGNORECASE)
        ticker = ticker_m.group(1).upper() if ticker_m else ''
        if ticker in ('SPX', 'SPXXX'): ticker = 'SPX'
        return 'EXIT', 'STC', ticker, 'OPTION', '', '', 'HIGH', 'Full exit'
    
    # Stopped out
    if re.search(r'stopped?\s+out', cl, re.IGNORECASE):
        ticker_m = re.search(r'stopped?\s+out\s+of\s+([a-z]{2,5})', cl, re.IGNORECASE)
        ticker = ticker_m.group(1).upper() if ticker_m else ''
        return 'EXIT', 'STC', ticker, 'OPTION', '', '', 'HIGH', 'Stopped out'
    
    # Added to position
    if re.search(r'added to\s+(spy|spx)', cl, re.IGNORECASE):
        ticker_m = re.search(r'added to\s+([a-z]{2,5})', cl, re.IGNORECASE)
        ticker = ticker_m.group(1).upper() if ticker_m else 'SPY'
        return 'ENTRY', 'BTO', ticker, 'OPTION', '', '', 'MEDIUM', 'Adding to position'
    
    # Trail stops
    if re.search(r'trail\s+stop|pillow\s+secured|reduced\s+risk', cl, re.IGNORECASE):
        return 'UPDATE', '', '', 'OPTION', '', '', 'LOW', 'Risk management update'
    
    # Done for the day
    if re.search(r'done for (the day|today)', cl, re.IGNORECASE):
        return 'NOISE', '', '', '', '', '', '', 'Daily recap/sign-off'
    
    # MISINPUT
    if 'misinput' in cl:
        return 'NOISE', '', '', '', '', '', '', 'Correction/misinput'
    
    # Planned plays / bull printer
    if re.search(r'planned plays|perform your own dd|bullprinter', cl, re.IGNORECASE):
        return 'NOISE', '', '', '', '', '', '', 'Daily intro/disclaimer'
    
    # Market commentary
    if re.search(r'/es\s|vix\s|bulls?|bears?|market|balance', cl, re.IGNORECASE) and not re.search(r'here\n|avg', cl):
        ticker = extract_ticker_from_content(content)
        return 'DISCUSSION', '', ticker or '', '', '', '', 'LOW', 'Market commentary'
    
    # Image-only
    if not content.strip() or content.strip().startswith('http'):
        if row['has_image'] == 'YES':
            return 'NOISE', '', '', '', '', '', '', 'Chart image (no text signal)'
        return 'NOISE', '', '', '', '', '', '', 'Link/empty'
    
    # Eyes on / watching
    if re.search(r'eyes on|watching|👀', cl):
        ticker = extract_ticker_from_content(content)
        return 'ALERT', '', ticker or '', '', '', '', 'LOW', 'Watchlist alert'
    
    return 'NOISE', '', '', '', '', '', '', 'General commentary'

def classify_enhanced_market(row):
    content = row['content'] or ''
    embed = row['embed_text'] or ''
    cl = content.lower()
    el = embed.lower()
    is_reply = row['is_reply'] == 'YES'
    
    # Embed-based entry: "🟢 ENTERING $TICKER STRIKE DATE"
    entry_match = re.search(r'🟢\s*ENTERING\s+\$?([A-Z]{2,5})\s+(\d+[CP])\s+(\d+/\d+)', embed, re.IGNORECASE)
    if entry_match:
        return 'ENTRY', 'BTO', entry_match.group(1), 'OPTION', entry_match.group(2).upper(), entry_match.group(3), 'HIGH', f'Embed entry signal'
    
    # Embed-based exit: "🔴 SOLD TICKER STRIKE DATE"
    exit_match = re.search(r'🔴\s*SOLD\s+\$?([A-Z]{2,5})\s+(\d+[CP])\s+(\d+/\d+)', embed, re.IGNORECASE)
    if exit_match:
        # Check gain/loss
        gl_match = re.search(r'Gain/Loss:?\s*\+?\$?([\-\d,]+)', embed)
        pct_match = re.search(r'\(([+-]?\d+)%\)', embed)
        remaining_match = re.search(r'Remaining:?\s*(\d+)', embed)
        remaining = int(remaining_match.group(1)) if remaining_match else 0
        signal_type = 'TRIM' if remaining > 0 else 'EXIT'
        pct = pct_match.group(1) if pct_match else ''
        return signal_type, 'STC', exit_match.group(1), 'OPTION', exit_match.group(2).upper(), exit_match.group(3), 'HIGH', f'{pct}% {"partial" if remaining > 0 else "full"} exit, {remaining} remaining'
    
    # Daily reminder
    if 'daily reminder' in cl or 'trading strategy and risk tolerance' in cl:
        return 'NOISE', '', '', '', '', '', '', 'Daily disclaimer'
    
    # Weekly recap
    if re.search(r'recap (on|of) the week', cl, re.IGNORECASE):
        return 'NOISE', '', '', '', '', '', '', 'Weekly recap'
    
    # Image only
    if not content.strip() and row['has_image'] == 'YES':
        return 'NOISE', '', '', '', '', '', '', 'Chart image'
    
    return 'NOISE', '', '', '', '', '', '', 'General commentary'

def classify_ecs(row):
    content = row['content'] or ''
    embed = row['embed_text'] or ''
    cl = content.lower()
    el = embed.lower()
    is_reply = row['is_reply'] == 'YES'
    ctx = row['reply_context'] or ''
    
    # ECS CRYPTO ENTRY: "#TICKER/USDT ... Entry around: PRICE ... Targets ... Stop"
    crypto_entry = re.search(r'#([A-Z]+)/USDT', content, re.IGNORECASE)
    if crypto_entry and re.search(r'entry\s+around', cl):
        ticker = crypto_entry.group(1).upper()
        direction = 'STO' if 'short/sell' in cl else 'BTO'
        leverage_match = re.search(r'leverage\s*-?\s*(\d+)x', cl)
        leverage = leverage_match.group(1) if leverage_match else ''
        duration_match = re.search(r'expected duration:\s*(.+)', cl, re.IGNORECASE)
        duration = duration_match.group(1).strip() if duration_match else ''
        signal_type = 'ENTRY'
        notes = f'Crypto {"short" if direction == "STO" else "long"}'
        if leverage: notes += f' {leverage}x leverage'
        if duration: notes += f', {duration}'
        return signal_type, direction, ticker, 'CRYPTO', '', '', 'HIGH', notes
    
    # ECS TP HIT: "TICKER went above/below PRICE ... reached the Nth Profit Target"
    tp_match = re.search(r'#([A-Z]+)USDT has reached the (\d+)\w+ Profit Target', content, re.IGNORECASE)
    if tp_match:
        ticker = tp_match.group(1).upper()
        tp_num = tp_match.group(2)
        return 'TRIM', 'STC', ticker, 'CRYPTO', '', '', 'HIGH', f'TP{tp_num} hit (automated alert)'
    
    # Swing Trade entry: "Swing Trade: BTO TICKER STRIKE DATE @ PRICE"
    swing_match = re.search(r'(?:swing\s+trade:?\s*)?BTO\s+\d*\s*\$?([A-Z]{2,5})\s+(\d+\.?\d*[CP])\s+(\d+/\d+)\s+@\s*\$?(\d+\.?\d*)', content, re.IGNORECASE)
    if swing_match:
        return 'ENTRY', 'BTO', swing_match.group(1), 'OPTION', swing_match.group(2).upper(), swing_match.group(3), 'HIGH', f'Swing entry @${swing_match.group(4)}'
    
    # Direct entry: "$TICKER DATE STRIKE at $PRICE" 
    direct_match = re.search(r'\$([A-Z]{2,5})\s+(\d+/\d+(?:/\d+)?)\s+\$?(\d+\.?\d*[CP])\s+(?:at|@)\s+\$?(\d+\.?\d*)', content, re.IGNORECASE)
    if direct_match:
        conv = 'HIGH'
        if 'risky' in cl or 'riskier' in cl or 'high risk' in cl or 'lotto' in cl: conv = 'LOW'
        return 'ENTRY', 'BTO', direct_match.group(1), 'OPTION', direct_match.group(3).upper(), direct_match.group(2), conv, f'Entry @${direct_match.group(4)}'
    
    # Alternate: "TICKER DATE $STRIKEP/C at/@ $PRICE"
    alt_match = re.search(r'\$?([A-Z]{2,5})\s+(\d+/\d+(?:/\d+)?)\s+\$?(\d+\.?\d*[CP])\s+(?:at|@)\s*\$?(\d+\.?\d*)', content, re.IGNORECASE)
    if alt_match and not is_reply:
        conv = 'HIGH'
        if 'risky' in cl or 'lotto' in cl or 'high risk' in cl: conv = 'LOW'
        return 'ENTRY', 'BTO', alt_match.group(1), 'OPTION', alt_match.group(3).upper(), alt_match.group(2), conv, f'Entry @${alt_match.group(4)}'
    
    # Added to position
    if re.search(r'added|accumulating|grabbing.+shares', cl, re.IGNORECASE) and re.search(r'\$?([A-Z]{2,5})', content):
        ticker = extract_ticker_from_content(content)
        # Check if shares vs options
        if 'shares' in cl:
            return 'ENTRY', 'BTO', ticker, 'STOCK', '', '', 'MEDIUM', 'Adding shares'
        return 'ENTRY', 'BTO', ticker, 'OPTION', '', '', 'MEDIUM', 'Adding to position'
    
    # Trim: "Trimmed at $X" or "Trim at $X"
    trim_match = re.search(r'trimm?(?:ed|ing)?\s+(?:a few more\s+)?(?:\$?([A-Z]{2,5})\s+)?(?:at|@|here)\s+\$?(\d+\.?\d*)', content, re.IGNORECASE)
    if trim_match:
        ticker = trim_match.group(1) or extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        pct_match = re.search(r'(\d+)%\s*profit', cl)
        pct = pct_match.group(1) if pct_match else ''
        return 'TRIM', 'STC', ticker, 'OPTION', '', '', 'HIGH', f'Trim @${trim_match.group(2)}, {pct}% profit' if pct else f'Trim @${trim_match.group(2)}'
    
    # Sold/Out: "Sold at $X" or "Fully out"
    if re.search(r'(?:fully\s+)?(?:out|sold)\s+(?:on\s+)?(?:the\s+rest\s+of\s+)?(?:\$?([A-Z]{2,5}))?\s*(?:at|@|puts?|calls?)?\s*\$?(\d+\.?\d*)?', cl, re.IGNORECASE):
        if re.search(r'fully out|sold.+(?:at|@)|out on', cl):
            ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
            pct_match = re.search(r'(\d+)%\s*profit', cl)
            return 'EXIT', 'STC', ticker, 'OPTION', '', '', 'HIGH', f'Full exit' + (f', {pct_match.group(1)}% profit' if pct_match else '')
    
    # Stop loss hit
    if re.search(r'stop\s*loss\s*(?:hit|triggered)|hit\s+(?:my\s+)?(?:stop|sl)', cl, re.IGNORECASE):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'EXIT', 'STC', ticker, 'OPTION', '', '', 'HIGH', 'Stop loss hit'
    
    # Cutting position
    if re.search(r'cutting|closing|closed', cl) and re.search(r'\$?([A-Z]{2,5})', content):
        ticker = extract_ticker_from_content(content)
        return 'EXIT', 'STC', ticker, 'OPTION', '', '', 'HIGH', 'Closing position'
    
    # "on watch" / "watching"
    if re.search(r'on\s+watch|watching|setting up', cl, re.IGNORECASE):
        ticker = extract_ticker_from_content(content)
        return 'ALERT', '', ticker or '', '', '', '', 'LOW', 'Watchlist'
    
    # Weekly recap
    if re.search(r'recap|no losses today|react with', cl, re.IGNORECASE):
        return 'NOISE', '', '', '', '', '', '', 'Recap/social'
    
    # Personal/chat
    if re.search(r'pray for me|flu|sick|earnings|good morning|weekend|hello|hey|massive week', cl, re.IGNORECASE):
        return 'NOISE', '', '', '', '', '', '', 'Personal/chat'
    
    # Profit update (reply)
    if re.search(r'\d+%\s*profit|up\s+\$\d+|sitting\s+@|contracts?\s+sitting', cl, re.IGNORECASE):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'UPDATE', '', ticker, 'OPTION', '', '', 'LOW', 'Position update'
    
    # Share buying for long term
    if re.search(r'shares\s+here|long\s+term\s+hold|accumulate\s+shares', cl, re.IGNORECASE):
        ticker = extract_ticker_from_content(content)
        return 'ENTRY', 'BTO', ticker, 'STOCK', '', '', 'MEDIUM', 'Long-term share accumulation'
    
    # SL update
    if re.search(r'(?:my\s+)?sl\s+(?:is|for)|stop\s+loss|moving\s+sl', cl, re.IGNORECASE):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'UPDATE', '', ticker, '', '', '', 'LOW', 'Stop loss update'
    
    # Image only
    if not content.strip() and row['has_image'] == 'YES':
        return 'NOISE', '', '', '', '', '', '', 'Chart image'
    
    # "." messages
    if content.strip() in ['.', '..', '...']:
        return 'NOISE', '', '', '', '', '', '', 'Placeholder/empty'
    
    # General ticker discussion
    ticker = extract_ticker_from_content(content)
    if ticker:
        return 'DISCUSSION', '', ticker, '', '', '', 'LOW', 'Ticker mentioned'
    
    # Tenor/gif links
    if 'tenor.com' in cl or 'gif' in cl:
        return 'NOISE', '', '', '', '', '', '', 'GIF/meme'
    
    return 'NOISE', '', '', '', '', '', '', 'General commentary'

def classify_eva(row):
    content = row['content'] or ''
    embed = row['embed_text'] or ''
    cl = content.lower()
    el = embed.lower()
    is_reply = row['is_reply'] == 'YES'
    
    # Eva uses embed titles: "Open" = entry, "Close" = exit, "Update" = update
    embed_title_match = re.search(r'\[EMBED TITLE\]\s*(Open|Close|Update)', embed, re.IGNORECASE)
    
    if embed_title_match:
        title = embed_title_match.group(1).lower()
        
        if title == 'open':
            # BTO TICKER DATE STRIKE @ PRICE
            bto_match = re.search(r'BTO\s+([A-Z]{2,5})\s+(\d+/\d+/?\d*)\s+(\d+\.?\d*[CP])\s+@\s*(\d+\.?\d*)', embed, re.IGNORECASE)
            if bto_match:
                conv = 'LOW' if 'lotto' in el or 'risky' in el else 'HIGH'
                return 'ENTRY', 'BTO', bto_match.group(1), 'OPTION', bto_match.group(3).upper(), bto_match.group(2), conv, f'Entry @${bto_match.group(4)}'
            # Fallback: try to find ticker
            ticker = extract_ticker_from_content(embed)
            return 'ENTRY', 'BTO', ticker, 'OPTION', '', '', 'MEDIUM', 'Embed open signal'
        
        elif title == 'close':
            # STC TICKER DATE STRIKE @ PRICE
            stc_match = re.search(r'STC\s+([A-Z]{2,5})\s+(\d+/\d+/?\d*)\s+(\d+\.?\d*[CP])\s+@\s*(\d+\.?\d*)', embed, re.IGNORECASE)
            if stc_match:
                # Check if partial (holding/1/2/remaining)
                is_partial = bool(re.search(r'half|holding|1/2|scale out|remaining', el))
                signal_type = 'TRIM' if is_partial else 'EXIT'
                return signal_type, 'STC', stc_match.group(1), 'OPTION', stc_match.group(3).upper(), stc_match.group(2), 'HIGH', f'{"Partial" if is_partial else "Full"} exit @${stc_match.group(4)}'
            ticker = extract_ticker_from_content(embed)
            return 'EXIT', 'STC', ticker, 'OPTION', '', '', 'HIGH', 'Embed close signal'
        
        elif title == 'update':
            # Updates - look for actionable info
            ticker = extract_ticker_from_content(embed)
            if re.search(r'looking at|swing|scalp|like|setup', el):
                return 'ALERT', '', ticker, 'OPTION', '', '', 'LOW', 'Potential setup alert'
            return 'UPDATE', '', ticker, '', '', '', 'LOW', 'Position/market update'
    
    # Embed entry without title: "🟢 ENTERING" or "BTO"
    entry_match = re.search(r'(?:🟢\s*)?(?:ENTERING|BTO)\s+\$?([A-Z]{2,5})\s+(\d+\.?\d*[CP])\s+(\d+/\d+)', embed, re.IGNORECASE)
    if entry_match:
        return 'ENTRY', 'BTO', entry_match.group(1), 'OPTION', entry_match.group(2).upper(), entry_match.group(3), 'HIGH', 'Embed entry signal'
    
    # Embed exit without title: "🔴 SOLD" or "STC"
    exit_match = re.search(r'(?:🔴\s*)?(?:SOLD|STC)\s+\$?([A-Z]{2,5})\s+(\d+\.?\d*[CP])\s+(\d+/\d+)', embed, re.IGNORECASE)
    if exit_match:
        remaining_match = re.search(r'Remaining:?\s*(\d+)', embed)
        remaining = int(remaining_match.group(1)) if remaining_match else 0
        pct_match = re.search(r'\(([+-]?\d+)%\)', embed)
        pct = pct_match.group(1) if pct_match else ''
        signal_type = 'TRIM' if remaining > 0 else 'EXIT'
        return signal_type, 'STC', exit_match.group(1), 'OPTION', exit_match.group(2).upper(), exit_match.group(3), 'HIGH', f'{pct}% {"partial" if remaining > 0 else "full"} exit'
    
    # Daily reminder
    if 'daily reminder' in cl or 'risk tolerance' in cl:
        return 'NOISE', '', '', '', '', '', '', 'Daily disclaimer'
    
    if not content.strip() and not embed.strip():
        return 'NOISE', '', '', '', '', '', '', 'Empty'
    
    if row['has_image'] == 'YES' and not content.strip():
        return 'NOISE', '', '', '', '', '', '', 'Chart image'
    
    return 'NOISE', '', '', '', '', '', '', 'General commentary'

def classify_nando(row):
    content = row['content'] or ''
    cl = content.lower()
    is_reply = row['is_reply'] == 'YES'
    ctx = row['reply_context'] or ''
    embed = row['embed_text'] or ''
    
    # "Swing Trade: BTO X TICKER STRIKE DATE @ PRICE"
    swing_match = re.search(r'(?:swing\s+trade:?\s*)?BTO\s+\d*\s*\$?([A-Z]{2,5})\s+(\d+\.?\d*[CP])\s+(\d+/\d+)\s+@\s*\$?(\d+\.?\d*)', content, re.IGNORECASE)
    if swing_match:
        return 'ENTRY', 'BTO', swing_match.group(1), 'OPTION', swing_match.group(2).upper(), swing_match.group(3), 'HIGH', f'Swing entry @${swing_match.group(4)}'
    
    # "Bought a few more TICKER STRIKE DATE @ PRICE" or "$TICKER DATE STRIKE @ PRICE"
    bought_match = re.search(r'(?:bought|grabbed|added)\s+(?:a\s+few\s+(?:more\s+)?)?(?:some\s+)?\*?\*?\$?([A-Z]{2,5})\s+(?:\d+/\d+(?:/\d+)?\s+)?(\d+\.?\d*[CP])\s+(?:\d+/\d+(?:/\d+)?\s+)?@\s*\$?(\d+\.?\d*)', content, re.IGNORECASE)
    if bought_match:
        ticker = bought_match.group(1)
        strike = bought_match.group(2).upper()
        # Try to find date
        date_match = re.search(r'(\d+/\d+(?:/\d+)?)', content)
        expiry = date_match.group(1) if date_match else ''
        return 'ENTRY', 'BTO', ticker, 'OPTION', strike, expiry, 'MEDIUM', f'Entry @${bought_match.group(3)}'
    
    # Alternative entry: "Contract I'm looking @: TICKER STRIKE DATE @ PRICE"
    alt_entry = re.search(r'\$?([A-Z]{2,5})\s+(\d+\.?\d*[CP])\s+(\d+/\d+)\s+@\s*\$?(\d+\.?\d*)', content, re.IGNORECASE)
    if alt_entry and not is_reply:
        return 'ENTRY', 'BTO', alt_entry.group(1), 'OPTION', alt_entry.group(2).upper(), alt_entry.group(3), 'MEDIUM', f'Entry @${alt_entry.group(4)}'
    
    # Trimmed: "Trimmed majority/1 @ PRICE" or "Trimmed X TICKER at PRICE"
    if re.search(r'trimm?(?:ed|ing)\s+(?:majority|1|a few|some|more|half)?', cl):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        pct_match = re.search(r'\+?(\d+)%', cl)
        is_partial = not re.search(r'fully|all|rest', cl)
        signal_type = 'TRIM' if is_partial else 'EXIT'
        return signal_type, 'STC', ticker, 'OPTION', '', '', 'HIGH', f'Trim' + (f', +{pct_match.group(1)}%' if pct_match else '')
    
    # Position update with price: "Contracts Sitting @ X from Y" or "+XX% here"
    if re.search(r'sitting\s+@|contracts?\s+(?:sitting|@)|per\s+contract|from\s+\$?\d+\.\d+|\+\d+%\s*(?:return|profit|here)', cl):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'UPDATE', '', ticker, 'OPTION', '', '', 'LOW', 'Position update with price info'
    
    # "No longer a shareholder" = exit
    if re.search(r'no longer|fully out|sold.+position', cl):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'EXIT', 'STC', ticker, 'STOCK', '', '', 'HIGH', 'Full exit'
    
    # Weekly recap
    if re.search(r'recap|closed positions|flawless week|printing|wins from', cl):
        return 'NOISE', '', '', '', '', '', '', 'Weekly recap'
    
    # "Taking Profit" suggestion
    if re.search(r'taking profit|realize.+gains|de-risk|start taking', cl):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'ALERT', '', ticker, '', '', '', 'MEDIUM', 'Profit-taking advisory'
    
    # Bullish/bearish commentary on a name
    if is_reply and re.search(r'holding|ride|let\'s|how are we|our time', cl):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'UPDATE', '', ticker, '', '', '', 'LOW', 'Position commentary/hold update'
    
    # "Potential Swing Entry" or setup
    if re.search(r'potential.*entry|swing.*entry|looking\s+@|setup|believe|expecting', cl):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        if ticker:
            return 'ALERT', '', ticker, '', '', '', 'LOW', 'Potential setup/watchlist'
    
    # Image only
    if not content.strip() and row['has_image'] == 'YES':
        return 'NOISE', '', '', '', '', '', '', 'Chart image'
    
    # "Anyone recognize" / social
    if re.search(r'anyone recognize|printing|member|shoutout|banning', cl):
        return 'NOISE', '', '', '', '', '', '', 'Social/community'
    
    ticker = extract_ticker_from_content(content)
    if ticker:
        return 'DISCUSSION', '', ticker, '', '', '', 'LOW', 'Ticker discussion'
    
    return 'NOISE', '', '', '', '', '', '', 'General commentary'

def classify_zabes(row):
    content = row['content'] or ''
    cl = content.lower()
    is_reply = row['is_reply'] == 'YES'
    ctx = row['reply_context'] or ''
    
    # Options entry: "$TICKER DATE STRIKE at/@ $PRICE" or "TICKER DATE $STRIKEP/C at $PRICE"
    opt_match = re.search(r'\$?([A-Z]{2,5})\s+(\d+/\d+(?:/\d+)?)\s+\$?(\d+\.?\d*[CP])\s+(?:at|@)\s*\n?\s*\$?(\d+\.?\d*)', content, re.IGNORECASE)
    if opt_match:
        conv = 'HIGH'
        if 'risky' in cl or 'riskier' in cl or 'high risk' in cl or 'lotto' in cl: conv = 'LOW'
        return 'ENTRY', 'BTO', opt_match.group(1), 'OPTION', opt_match.group(3).upper(), opt_match.group(2), conv, f'Entry @${opt_match.group(4)}'
    
    # Share buying: "buying TICKER shares" or "long term hold"
    if re.search(r'(?:buy(?:ing)?|start(?:ing)?)\s+(?:to\s+buy\s+)?\$?([A-Z]{2,5})\s+shares|long\s+term\s+hold', cl, re.IGNORECASE):
        ticker = extract_ticker_from_content(content)
        return 'ENTRY', 'BTO', ticker, 'STOCK', '', '', 'HIGH', 'Long-term share purchase (NOT options)'
    
    # Trim: "Trimmed/trim at $X" or "Trimmed TICKER at $X"
    trim_match = re.search(r'trimm?(?:ed|ing)?\s+(?:a few more\s+)?(?:\$?([A-Z]{2,5})\s+)?(?:at|@|here)\s+\$?(\d+\.?\d*)', content, re.IGNORECASE)
    if trim_match:
        ticker = trim_match.group(1) or extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        pct_match = re.search(r'(\d+)%\s*profit', cl)
        pct = pct_match.group(1) if pct_match else ''
        return 'TRIM', 'STC', ticker, 'OPTION', '', '', 'HIGH', f'Trim @${trim_match.group(2)}' + (f', {pct}%' if pct else '')
    
    # Full exit: "Fully out" or "Sold TICKER" or "Out on TICKER"
    if re.search(r'fully\s+out|sold\s+(?:the\s+rest|last)|out on|selling', cl):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        pct_match = re.search(r'(\d+)%\s*profit', cl)
        return 'EXIT', 'STC', ticker, 'OPTION', '', '', 'HIGH', f'Full exit' + (f', {pct_match.group(1)}%' if pct_match else '')
    
    # Quick profit take
    if re.search(r'\d+%\s*profit\s*(?:selling|sold)?|profit.*selling', cl):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        pct_match = re.search(r'(\d+)%\s*profit', cl)
        return 'TRIM', 'STC', ticker, 'OPTION', '', '', 'HIGH', f'{pct_match.group(1)}% profit' if pct_match else 'Taking profit'
    
    # Stop loss
    if re.search(r'stop\s*loss|hit\s+(?:my\s+)?stop|sl\s+hit|cutting', cl, re.IGNORECASE):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'EXIT', 'STC', ticker, 'OPTION', '', '', 'HIGH', 'Stop loss hit / cutting'
    
    # SL update
    if re.search(r'(?:my\s+)?sl\s+(?:is|for)|stop\s+loss|moving\s+sl', cl, re.IGNORECASE):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'UPDATE', '', ticker, '', '', '', 'LOW', 'Stop loss update'
    
    # Watch alerts
    if re.search(r'watch(?:ing)?|on\s+watch|setting\s+up|calls\s+on\s+watch|puts\s+on\s+watch', cl):
        ticker = extract_ticker_from_content(content)
        return 'ALERT', '', ticker or '', '', '', '', 'LOW', 'Watchlist alert'
    
    # Position update (reply with price info)
    if is_reply and re.search(r'up\s+\$|sitting\s+@|ripping|back\s+green|already\s+up', cl):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'UPDATE', '', ticker, '', '', '', 'LOW', 'Position update'
    
    # Swinging
    if re.search(r'swinging\s+(?:my|some|a)', cl):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'UPDATE', '', ticker, 'OPTION', '', '', 'LOW', 'Swinging position overnight'
    
    # Small profit / correction
    if re.search(r'small\s+profit|quick\s+\+?\$\d+', cl):
        ticker = extract_ticker_from_content(content) or extract_ticker_from_context(ctx)
        return 'UPDATE', '', ticker, '', '', '', 'LOW', 'Small profit note'
    
    # "." messages
    if content.strip() in ['.', '..', '...']:
        return 'NOISE', '', '', '', '', '', '', 'Placeholder'
    
    # Personal/no trade
    if re.search(r'flu|sick|not trading|not feeling|may not trade|haven\'t taken|enjoy your|hope everyone', cl):
        return 'NOISE', '', '', '', '', '', '', 'Personal/no trade'
    
    # Earnings/general market
    if re.search(r'earnings|massive week|big week', cl):
        return 'NOISE', '', '', '', '', '', '', 'General market commentary'
    
    # General ticker discussion  
    ticker = extract_ticker_from_content(content)
    if ticker:
        if re.search(r'gonna|going to|looking|believe|think|support|resistance', cl):
            return 'DISCUSSION', '', ticker, '', '', '', 'LOW', 'Analysis/opinion'
        return 'DISCUSSION', '', ticker, '', '', '', 'LOW', 'Ticker mentioned'
    
    return 'NOISE', '', '', '', '', '', '', 'General commentary'


# Helper functions
COMMON_TICKERS = {'BTC', 'ETH', 'SOL', 'TON', 'XRP', 'ADA', 'DOGE', 'BNB', 'AVAX', 'LINK', 'DOT', 'MATIC', 'SUI', 'NEAR', 'APT',
                  'SPY', 'SPX', 'QQQ', 'AAPL', 'NVDA', 'TSLA', 'AMD', 'META', 'MSFT', 'AMZN', 'GOOGL', 'GOOG',
                  'MU', 'ARM', 'HOOD', 'PLTR', 'CLSK', 'RIOT', 'IBIT', 'MSTR', 'COIN', 'NFLX', 'BABA', 'XOM',
                  'WMT', 'RDDT', 'SNDK', 'FSLR', 'UNH', 'SOFI', 'RIVN', 'GRAB', 'DELL', 'CRM', 'SNOW'}
NOISE_WORDS = {'BTO', 'STC', 'STO', 'NEW', 'ALL', 'FOR', 'THE', 'ARE', 'HAS', 'OOT', 'PRO', 'NOT', 'OUT', 'HOD', 'LOD', 'ATH', 'ATL', 'ITM', 'OTM', 'IMO', 'FYI', 'DD', 'ER', 'AH', 'PM', 'AM', 'PER', 'SL', 'HERE', 'AVG', 'ANY', 'NOW', 'SET', 'BIG', 'RUN', 'RED', 'LOW', 'HIGH', 'DAY', 'VIX', 'USD', 'MORE', 'NICE', 'DONE', 'BACK', 'JUST', 'STILL', 'TRIM', 'HOLD', 'RISK', 'LAST', 'CLOSE'}

def extract_ticker_from_content(text):
    """Extract most likely ticker from content."""
    if not text:
        return ''
    # Look for $TICKER pattern first
    dollar_match = re.findall(r'\$([A-Z]{2,5})', text)
    for t in dollar_match:
        if t in COMMON_TICKERS:
            return t
    if dollar_match:
        return dollar_match[0]
    
    # Look for known tickers in text
    words = re.findall(r'\b([A-Z]{2,5})\b', text)
    for w in words:
        if w in COMMON_TICKERS and w not in NOISE_WORDS:
            return w
    
    # Lowercase search for crypto
    lower = text.lower()
    for crypto in ['btc', 'eth', 'sol', 'ton', 'xrp']:
        if re.search(r'\b' + crypto + r'\b', lower):
            return crypto.upper()
    
    return ''

def extract_ticker_from_context(ctx):
    """Extract ticker from reply context."""
    return extract_ticker_from_content(ctx)


def classify_row(row):
    analyst = row['analyst']
    classifiers = {
        'Grizzlies': classify_grizzlies,
        'Waxui': classify_waxui,
        'Enhanced Market': classify_enhanced_market,
        'ECS': classify_ecs,
        'Eva': classify_eva,
        'Nando': classify_nando,
        'Zabes': classify_zabes,
    }
    
    classifier = classifiers.get(analyst)
    if not classifier:
        return 'NOISE', '', '', '', '', '', '', f'Unknown analyst: {analyst}'
    
    return classifier(row)


def main():
    # Read
    with open(INPUT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    
    # Classify
    stats = {}
    for row in rows:
        signal_type, action, ticker, asset_type, strike, expiry, conviction, notes = classify_row(row)
        row['dolph_signal_type'] = signal_type
        row['dolph_action'] = action
        row['dolph_ticker'] = ticker
        row['dolph_asset_type'] = asset_type
        row['dolph_strike'] = strike
        row['dolph_expiry'] = expiry
        row['dolph_conviction'] = conviction
        row['dolph_notes'] = notes
        
        analyst = row['analyst']
        if analyst not in stats:
            stats[analyst] = {'total': 0, 'signals': 0, 'noise': 0, 'types': {}}
        stats[analyst]['total'] += 1
        if signal_type in ('ENTRY', 'EXIT', 'TRIM'):
            stats[analyst]['signals'] += 1
        elif signal_type == 'NOISE':
            stats[analyst]['noise'] += 1
        stats[analyst]['types'][signal_type] = stats[analyst]['types'].get(signal_type, 0) + 1
    
    # Write
    with open(OUTPUT, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    
    # Print stats
    print("=== CLASSIFICATION SUMMARY ===\n")
    for analyst in sorted(stats.keys()):
        s = stats[analyst]
        print(f"{analyst}: {s['total']} msgs → {s['signals']} signals, {s['noise']} noise")
        for t, c in sorted(s['types'].items()):
            print(f"  {t}: {c}")
        print()

if __name__ == '__main__':
    main()
