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
    referenced_message: str | None = None  # Quoted/replied-to message content


class DiscordPoller:
    # Circuit breaker: disable channel after this many consecutive 403s
    CIRCUIT_BREAKER_THRESHOLD = 5
    # Re-test disabled channels every 5 minutes
    CIRCUIT_BREAKER_RETRY_INTERVAL = 300

    def __init__(self, config: Config):
        self.config = config
        self.base_url = config.DISCORD_API_BASE
        self.headers = {
            "Authorization": config.discord_user_token,
            "Content-Type": "application/json",
        }
        # Track last seen message ID per channel for pagination
        self.last_message_id: dict[str, str | None] = {
            ch: None for ch in config.discord_only_channels
        }
        self._backoff: dict[str, float] = {}
        self._last_success: dict[str, float] = {
            ch: time.time() for ch in config.discord_only_channels
        }
        # Circuit breaker state per channel
        self._consecutive_403s: dict[str, int] = {}
        self._disabled_channels: dict[str, float] = {}  # channel_id → time disabled

    def poll(self) -> list[DiscordMessage]:
        """Poll all watched channels and return new messages."""
        all_messages: list[DiscordMessage] = []
        now = time.time()
        for channel_id in self.config.discord_only_channels:
            # Circuit breaker: skip disabled channels unless retry interval has passed
            disabled_at = self._disabled_channels.get(channel_id)
            if disabled_at is not None:
                if now - disabled_at < self.CIRCUIT_BREAKER_RETRY_INTERVAL:
                    continue  # Still disabled, skip
                else:
                    # Retry interval passed — re-test this channel
                    logger.info("Circuit breaker retry: re-testing channel %s", channel_id)
                    del self._disabled_channels[channel_id]
                    self._consecutive_403s[channel_id] = 0

            try:
                msgs = self._fetch_channel(channel_id)
                all_messages.extend(msgs)
                self._last_success[channel_id] = time.time()
            except Exception:
                logger.exception("Error polling channel %s", channel_id)
            # Small stagger between channels to reduce rate limit pressure
            time.sleep(0.5)
        return all_messages

    def _fetch_channel(self, channel_id: str) -> list[DiscordMessage]:
        url = f"{self.base_url}/channels/{channel_id}/messages"
        params: dict[str, str | int] = {"limit": 50}

        last_id = self.last_message_id.get(channel_id)
        if last_id:
            # Check if we've been offline for a while — expand fetch window
            time_since_success = time.time() - self._last_success.get(channel_id, time.time())
            if time_since_success > 120:  # >2 min gap = potential missed signals
                params["limit"] = 100  # Fetch more to catch up
                logger.info("Catch-up fetch for channel %s (%.0fs since last success)", 
                           channel_id, time_since_success)
            params["after"] = last_id
        else:
            # First poll for this channel — seed cursor to latest message
            # to avoid processing entire backlog as live signals
            logger.info("First poll for channel %s — seeding cursor to latest", channel_id)
            seed_params: dict[str, str | int] = {"limit": 1}
            seed_resp = self._request_with_backoff(url, seed_params, channel_id)
            if seed_resp and seed_resp.status_code == 200:
                seed_data = seed_resp.json()
                if seed_data:
                    self.last_message_id[channel_id] = seed_data[0]["id"]
                    logger.info("Seeded channel %s cursor to message %s", channel_id, seed_data[0]["id"])
            return []

        resp = self._request_with_backoff(url, params, channel_id)
        if resp is None:
            return []

        if resp.status_code == 403:
            count = self._consecutive_403s.get(channel_id, 0) + 1
            self._consecutive_403s[channel_id] = count
            if count >= self.CIRCUIT_BREAKER_THRESHOLD:
                self._disabled_channels[channel_id] = time.time()
                analyst = self.config.channel_to_analyst.get(channel_id, "unknown")
                logger.error("🔌 CIRCUIT BREAKER: channel %s (%s) disabled after %d consecutive 403s. "
                            "Will retry in %ds.", channel_id, analyst, count,
                            self.CIRCUIT_BREAKER_RETRY_INTERVAL)
            else:
                logger.warning("Discord 403 for channel %s (%d/%d before disable)",
                              channel_id, count, self.CIRCUIT_BREAKER_THRESHOLD)
            return []

        # Reset 403 counter on any non-403 response
        self._consecutive_403s.pop(channel_id, None)

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
            # Extract referenced (quoted/replied-to) message content
            ref_content = None
            ref_msg = msg.get("referenced_message")
            if ref_msg:
                ref_content = ref_msg.get("content", "")
            
            messages.append(DiscordMessage(
                channel_id=channel_id,
                message_id=msg["id"],
                content=msg.get("content", ""),
                timestamp=msg.get("timestamp", ""),
                embeds=msg.get("embeds", []),
                referenced_message=ref_content,
            ))

        # Update cursor to the newest message
        self.last_message_id[channel_id] = data[-1]["id"]
        # Clear backoff on success
        self._backoff.pop(channel_id, None)
        return messages

    def get_disabled_channels(self) -> dict[str, float]:
        """Return dict of disabled channel_id → seconds remaining until retry."""
        now = time.time()
        return {
            ch: max(0, self.CIRCUIT_BREAKER_RETRY_INTERVAL - (now - disabled_at))
            for ch, disabled_at in self._disabled_channels.items()
        }

    def _request_with_backoff(self, url: str, params: dict,
                              channel_id: str,
                              max_retries: int = 3) -> requests.Response | None:
        wait = self._backoff.get(channel_id, 0)
        if wait > 0:
            logger.info("Backing off channel %s for %.1fs", channel_id, wait)
            time.sleep(wait)

        for attempt in range(max_retries):
            try:
                resp = requests.get(url, headers=self.headers, params=params,
                                    timeout=20)
            except requests.RequestException:
                logger.warning("Request failed for channel %s (attempt %d/%d)",
                                 channel_id, attempt + 1, max_retries)
                # Short sleep — don't block other channels for too long
                time.sleep(min(2 ** attempt, 5))
                continue

            if resp.status_code == 429:
                retry_after = float(resp.json().get("retry_after", 2 ** attempt))
                logger.warning("Rate limited on channel %s, retry after %.1fs",
                               channel_id, retry_after)
                self._backoff[channel_id] = retry_after
                time.sleep(retry_after)
                continue
            
            if resp.status_code >= 500:
                # Discord server error — skip this channel this cycle, try next time
                logger.warning("Discord %d for channel %s — skipping this cycle",
                              resp.status_code, channel_id)
                return None

            return resp

        logger.error("Exhausted retries for channel %s", channel_id)
        return None
