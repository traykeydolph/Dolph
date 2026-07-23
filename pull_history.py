#!/usr/bin/env python3
"""Pull deep message history from every analyst channel into raw JSON files.

Output: data/history_<date>/<analyst>.json — one array of raw Discord message
objects (newest last). Used by the parse-ability analysis to rank analysts.

Run: ./venv/bin/python pull_history.py [--limit N]
"""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Cloudflare blocks python-requests' default UA (error 1010) — must look like a browser
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# .env is the source of truth; hardcoded fallbacks from the original pull_signals.py
CHANNELS = {
    "zabes": os.getenv("DISCORD_CHANNEL_ZABES") or "782068070977110027",
    "grizzlies": os.getenv("DISCORD_CHANNEL_GRIZZLIES") or "1025803258691862678",
    "waxui": os.getenv("DISCORD_CHANNEL_WAXUI") or "1347238168109387857",
    "enhanced_market": os.getenv("DISCORD_CHANNEL_EM") or "1126325195301462117",
    "ecs": os.getenv("DISCORD_CHANNEL_ECS") or "398075210949066766",
    "eva": os.getenv("DISCORD_CHANNEL_EVA") or "1035245170582626334",
    "nando": os.getenv("DISCORD_CHANNEL_NANDO") or "1139560883127857304",
    "ace": os.getenv("DISCORD_CHANNEL_ACE") or "1478050123786485831",
    "luigi": os.getenv("DISCORD_CHANNEL_LUIGI") or "1381991882237939832",
}


def fetch_history(session: requests.Session, channel_id: str, limit: int) -> list[dict]:
    """Fetch up to `limit` messages, paginating backwards from newest."""
    messages: list[dict] = []
    before: str | None = None
    while len(messages) < limit:
        params = {"limit": min(100, limit - len(messages))}
        if before:
            params["before"] = before
        for attempt in range(5):
            resp = session.get(
                f"https://discord.com/api/v9/channels/{channel_id}/messages",
                params=params,
                timeout=20,
            )
            if resp.status_code == 429:
                wait = float(resp.json().get("retry_after", 2**attempt))
                print(f"    rate limited, sleeping {wait:.1f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            break
        if resp.status_code != 200:
            print(f"    HTTP {resp.status_code}: {resp.text[:150]}", file=sys.stderr)
            break
        batch = resp.json()
        if not batch:
            break
        messages.extend(batch)
        before = batch[-1]["id"]
        if len(batch) < 100:
            break  # reached start of channel
        time.sleep(1.2)  # rate-limit safety between pages
    messages.reverse()  # chronological: oldest first
    return messages


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2000, help="max messages per channel")
    ap.add_argument("--only", type=str, default="",
                    help="comma-separated analyst names to pull (default: all)")
    args = ap.parse_args()

    only = {a.strip() for a in args.only.split(",") if a.strip()}
    channels = {k: v for k, v in CHANNELS.items() if not only or k in only}
    if only:
        missing = only - set(CHANNELS)
        if missing:
            sys.exit(f"Unknown analyst(s): {', '.join(sorted(missing))}")

    token = os.getenv("DISCORD_USER_TOKEN")
    if not token:
        sys.exit("DISCORD_USER_TOKEN not set")

    out_dir = Path(__file__).parent / "data" / f"history_{date.today():%Y%m%d}"
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"Authorization": token, "User-Agent": USER_AGENT})

    summary = {}
    for analyst, channel_id in channels.items():
        print(f"Pulling {analyst} (channel {channel_id})...", file=sys.stderr)
        msgs = fetch_history(session, channel_id, args.limit)
        out_file = out_dir / f"{analyst}.json"
        out_file.write_text(json.dumps(msgs, indent=1))
        oldest = msgs[0]["timestamp"][:10] if msgs else "-"
        newest = msgs[-1]["timestamp"][:10] if msgs else "-"
        summary[analyst] = {"count": len(msgs), "oldest": oldest, "newest": newest}
        print(f"  {len(msgs)} messages ({oldest} .. {newest})", file=sys.stderr)
        time.sleep(2)

    print(json.dumps({"out_dir": str(out_dir), "channels": summary}, indent=2))


if __name__ == "__main__":
    main()
