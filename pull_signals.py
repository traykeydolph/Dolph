#!/usr/bin/env python3
"""Pull last 100 messages per analyst from Discord and output to CSV for manual validation."""

import requests
import csv
import json
import time
import sys
import os

TOKEN = os.getenv("DISCORD_USER_TOKEN")
if not TOKEN:
    print("ERROR: DISCORD_USER_TOKEN environment variable not set", file=sys.stderr)
    sys.exit(1)

CHANNELS = {
    "Grizzlies": "1025803258691862678",
    "Waxui": "1347238168109387857",
    "Enhanced Market": "1126325195301462117",
    "ECS": "398075210949066766",
    "Eva": "1035245170582626334",
    "Nando": "1139560883127857304",
    "Zabes": "782068070977110027",
}

HEADERS = {
    "Authorization": TOKEN,
    "Content-Type": "application/json",
}

def fetch_messages(channel_id, limit=100):
    """Fetch last N messages from a Discord channel."""
    messages = []
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages?limit={min(limit, 100)}"
    
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code == 200:
        messages = resp.json()
    elif resp.status_code == 403:
        print(f"  403 Forbidden for channel {channel_id}", file=sys.stderr)
        return []
    elif resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 5)
        print(f"  Rate limited, waiting {retry_after}s", file=sys.stderr)
        time.sleep(retry_after)
        return fetch_messages(channel_id, limit)
    else:
        print(f"  Error {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return []
    
    # If we need more than 100, paginate
    while len(messages) < limit and len(resp.json()) == 100:
        time.sleep(1)  # Rate limit safety
        last_id = messages[-1]["id"]
        url = f"https://discord.com/api/v9/channels/{channel_id}/messages?limit={min(limit - len(messages), 100)}&before={last_id}"
        resp = requests.get(url, headers=HEADERS)
        if resp.status_code == 200 and resp.json():
            messages.extend(resp.json())
        else:
            break
    
    return messages[:limit]

def extract_message_data(msg, analyst):
    """Extract relevant fields from a Discord message."""
    # Check if this is a reply
    is_reply = msg.get("message_reference") is not None
    reply_to_id = msg.get("message_reference", {}).get("message_id", "") if is_reply else ""
    
    # Get content - handle embeds
    content = msg.get("content", "")
    
    # Check for embeds (some analysts post via embeds)
    embeds = msg.get("embeds", [])
    embed_text = ""
    for embed in embeds:
        parts = []
        if embed.get("title"):
            parts.append(f"[EMBED TITLE] {embed['title']}")
        if embed.get("description"):
            parts.append(f"[EMBED] {embed['description']}")
        for field in embed.get("fields", []):
            parts.append(f"[FIELD: {field.get('name', '')}] {field.get('value', '')}")
        embed_text = " | ".join(parts)
    
    # Check for attachments (images)
    attachments = msg.get("attachments", [])
    has_image = any(a.get("content_type", "").startswith("image") for a in attachments)
    
    # Author info
    author = msg.get("author", {}).get("username", "unknown")
    
    return {
        "analyst": analyst,
        "message_id": msg.get("id", ""),
        "timestamp": msg.get("timestamp", ""),
        "author": author,
        "content": content,
        "embed_text": embed_text,
        "is_reply": "YES" if is_reply else "",
        "reply_to_message_id": reply_to_id,
        "has_image": "YES" if has_image else "",
        "attachment_urls": " | ".join(a.get("url", "") for a in attachments),
    }

def main():
    output_file = os.path.expanduser("~/Desktop/Analyst Signal Audit.csv")
    all_rows = []
    
    for analyst, channel_id in CHANNELS.items():
        print(f"Fetching {analyst} (channel {channel_id})...", file=sys.stderr)
        messages = fetch_messages(channel_id, 100)
        print(f"  Got {len(messages)} messages", file=sys.stderr)
        
        # Build a lookup of message IDs for reply context
        msg_lookup = {m["id"]: m.get("content", "")[:100] for m in messages}
        
        for msg in reversed(messages):  # Chronological order
            row = extract_message_data(msg, analyst)
            # Add reply context if available
            if row["reply_to_message_id"] and row["reply_to_message_id"] in msg_lookup:
                row["reply_context"] = msg_lookup[row["reply_to_message_id"]]
            else:
                row["reply_context"] = ""
            all_rows.append(row)
        
        time.sleep(2)  # Rate limit between channels
    
    # Write CSV
    fieldnames = [
        "analyst", "message_id", "timestamp", "author", "content", "embed_text",
        "is_reply", "reply_to_message_id", "reply_context", "has_image", "attachment_urls",
        "dolph_signal_type", "dolph_action", "dolph_ticker", "dolph_asset_type",
        "dolph_strike", "dolph_expiry", "dolph_conviction", "dolph_notes",
        "tray_validated", "tray_corrections"
    ]
    
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in all_rows:
            # Initialize Dolph analysis columns as empty (will fill after)
            for col in ["dolph_signal_type", "dolph_action", "dolph_ticker", "dolph_asset_type",
                        "dolph_strike", "dolph_expiry", "dolph_conviction", "dolph_notes",
                        "tray_validated", "tray_corrections"]:
                row.setdefault(col, "")
            writer.writerow(row)
    
    print(f"\nWrote {len(all_rows)} messages to {output_file}", file=sys.stderr)
    print(json.dumps({"total": len(all_rows), "file": output_file}))

if __name__ == "__main__":
    main()
