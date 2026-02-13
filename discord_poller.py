"""Discord poller — fetches new messages from analyst channels via REST API."""

import logging
import time
from dataclasses import dataclass, field

import requests

from config import Config

logger = logging.getLogger(__name__)


@dataclass
class DiscordMessage:
    channel_id: str
    message_id: str
    content: str
    timestamp: str
    embeds: list[dict] = field(default_factory=list)


class DiscordPoller:
    def __init__(self, config: Config):
        self.config = config
        self.base_url = config.DISCORD_API_BASE
        self.headers = {
            "Authorization": config.discord_user_token,
            "Content-Type": "application/json",
        }
        # Track last seen message ID per channel for pagination
        self.last_message_id: dict[str, str | None] = {
            ch: None for ch in config.watched_channels
        }
        self._backoff: dict[str, float] = {}

    def poll(self) -> list[DiscordMessage]:
        """Poll all watched channels and return new messages."""
        all_messages: list[DiscordMessage] = []
        for channel_id in self.config.watched_channels:
            try:
                msgs = self._fetch_channel(channel_id)
                all_messages.extend(msgs)
            except Exception:
                logger.exception("Error polling channel %s", channel_id)
        return all_messages

    def _fetch_channel(self, channel_id: str) -> list[DiscordMessage]:
        url = f"{self.base_url}/channels/{channel_id}/messages"
        params: dict[str, str | int] = {"limit": 50}

        last_id = self.last_message_id.get(channel_id)
        if last_id:
            params["after"] = last_id

        resp = self._request_with_backoff(url, params, channel_id)
        if resp is None:
            return []

        if resp.status_code != 200:
            logger.error("Discord API %s for channel %s: %s",
                         resp.status_code, channel_id, resp.text[:200])
            return []

        data = resp.json()
        if not data:
            return []

        # Discord returns newest first — reverse to chronological order
        data.sort(key=lambda m: m["id"])

        messages: list[DiscordMessage] = []
        for msg in data:
            messages.append(DiscordMessage(
                channel_id=channel_id,
                message_id=msg["id"],
                content=msg.get("content", ""),
                timestamp=msg.get("timestamp", ""),
                embeds=msg.get("embeds", []),
            ))

        # Update cursor to the newest message
        self.last_message_id[channel_id] = data[-1]["id"]
        # Clear backoff on success
        self._backoff.pop(channel_id, None)
        return messages

    def _request_with_backoff(self, url: str, params: dict,
                              channel_id: str,
                              max_retries: int = 5) -> requests.Response | None:
        wait = self._backoff.get(channel_id, 0)
        if wait > 0:
            logger.info("Backing off channel %s for %.1fs", channel_id, wait)
            time.sleep(wait)

        for attempt in range(max_retries):
            try:
                resp = requests.get(url, headers=self.headers, params=params,
                                    timeout=10)
            except requests.RequestException:
                logger.exception("Request failed for channel %s (attempt %d)",
                                 channel_id, attempt + 1)
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 429:
                retry_after = float(resp.json().get("retry_after", 2 ** attempt))
                logger.warning("Rate limited on channel %s, retry after %.1fs",
                               channel_id, retry_after)
                self._backoff[channel_id] = retry_after
                time.sleep(retry_after)
                continue

            return resp

        logger.error("Exhausted retries for channel %s", channel_id)
        return None
