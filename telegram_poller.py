"""Telegram poller — fetches new messages from signal channels via Telethon."""

import logging
import time
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import Message

from config import Config
from discord_poller import DiscordMessage  # Reuse the same dataclass

logger = logging.getLogger(__name__)


class TelegramPoller:
    """Polls Telegram channels for new messages using a user account."""

    def __init__(self, config: Config):
        self.config = config
        self._client: Optional[TelegramClient] = None
        # Track last seen message ID per channel
        self.last_message_id: dict[str, int | None] = {}
        self._entities: dict[str, object] = {}  # cached resolved entities
        self._initialized = False

    @property
    def channels(self) -> list[str]:
        return self.config.telegram_signal_channels

    async def connect(self):
        """Initialize and connect the Telethon client."""
        if not self.config.telegram_api_id or not self.config.telegram_api_hash:
            logger.info("Telegram API credentials not configured — skipping")
            return False

        try:
            self._client = TelegramClient(
                self.config.telegram_session_file,
                int(self.config.telegram_api_id),
                self.config.telegram_api_hash,
            )
            await self._client.connect()

            if not await self._client.is_user_authorized():
                logger.error(
                    "Telegram session not authorized. Run telegram_auth.py first "
                    "to complete interactive login."
                )
                return False

            me = await self._client.get_me()
            logger.info("Telegram connected as %s (id=%s)", me.first_name, me.id)

            # Resolve channel entities and seed cursors
            for ch in self.channels:
                try:
                    entity = await self._client.get_entity(
                        int(ch) if ch.lstrip("-").isdigit() else ch
                    )
                    self._entities[ch] = entity
                    self.last_message_id[ch] = None  # will seed on first poll
                    logger.info("Resolved Telegram channel: %s → %s", ch, getattr(entity, "title", entity))
                except Exception:
                    logger.exception("Could not resolve Telegram channel: %s", ch)

            self._initialized = True
            return True

        except Exception:
            logger.exception("Telegram connection failed")
            return False

    async def disconnect(self):
        if self._client:
            await self._client.disconnect()

    async def poll(self) -> list[DiscordMessage]:
        """Poll all configured Telegram channels. Returns DiscordMessage objects."""
        if not self._initialized or not self._client:
            return []

        if not self._client.is_connected():
            logger.warning("Telegram disconnected — attempting reconnect")
            try:
                await self._client.connect()
            except Exception:
                logger.exception("Telegram reconnect failed")
                return []

        all_messages: list[DiscordMessage] = []

        for ch_id, entity in self._entities.items():
            try:
                msgs = await self._fetch_channel(ch_id, entity)
                all_messages.extend(msgs)
            except FloodWaitError as e:
                logger.warning("Telegram flood wait: %ds for channel %s", e.seconds, ch_id)
                # Don't sleep here — non-blocking. Skip this channel this cycle.
            except RPCError:
                logger.exception("Telegram RPC error polling channel %s", ch_id)
            except Exception:
                logger.exception("Error polling Telegram channel %s", ch_id)

        return all_messages

    async def _fetch_channel(self, ch_id: str, entity) -> list[DiscordMessage]:
        last_id = self.last_message_id.get(ch_id)

        if last_id is None:
            # First poll — seed cursor to latest, don't process backlog
            logger.info("Seeding Telegram channel %s cursor to latest", ch_id)
            async for msg in self._client.iter_messages(entity, limit=1):
                self.last_message_id[ch_id] = msg.id
                logger.info("Seeded Telegram channel %s to message %d", ch_id, msg.id)
            return []

        # Fetch messages newer than our cursor
        raw_msgs: list[Message] = []
        async for msg in self._client.iter_messages(entity, min_id=last_id, limit=100):
            if msg.id <= last_id:
                continue
            raw_msgs.append(msg)

        if not raw_msgs:
            return []

        # Sort chronologically (oldest first)
        raw_msgs.sort(key=lambda m: m.id)

        messages: list[DiscordMessage] = []
        for msg in raw_msgs:
            text = msg.text or msg.message or msg.raw_text or ""
            if not text.strip():
                # Log media-only messages so we know signals aren't silently dropped
                media_type = type(msg.media).__name__ if msg.media else "none"
                logger.info("Telegram channel %s msg %d: media-only (%s), skipping text parse",
                           ch_id, msg.id, media_type)
                continue

            # Extract forum topic ID if available
            topic_id = None
            if msg.reply_to:
                topic_id = getattr(msg.reply_to, 'reply_to_top_id', None) or (
                    getattr(msg.reply_to, 'reply_to_msg_id', None)
                    if getattr(msg.reply_to, 'forum_topic', False) else None
                )

            # Prepend topic metadata so parsers can filter by topic
            content = text
            if topic_id is not None:
                content = f"[topic:{topic_id}]\n{text}"

            # Build a DiscordMessage-compatible object
            # Use "tg_<channel_id>" as channel_id to namespace from Discord
            # and "tg_<msg_id>" as message_id to avoid collisions
            messages.append(DiscordMessage(
                channel_id=f"tg_{ch_id}",
                message_id=f"tg_{msg.id}",
                content=content,
                timestamp=msg.date.isoformat() if msg.date else "",
                embeds=[],
                referenced_message=(
                    msg.reply_to_msg_id and None  # Could fetch reply text if needed
                ),
            ))

        # Update cursor
        self.last_message_id[ch_id] = raw_msgs[-1].id
        logger.info("Telegram channel %s: fetched %d new messages", ch_id, len(messages))
        return messages
