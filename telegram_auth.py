"""One-time Telegram authentication — run interactively to create session file."""

import asyncio
from config import Config
from telethon import TelegramClient


async def main():
    config = Config()
    if not config.telegram_api_id or not config.telegram_api_hash:
        print("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first!")
        return

    client = TelegramClient(
        config.telegram_session_file,
        int(config.telegram_api_id),
        config.telegram_api_hash,
    )
    await client.start()
    me = await client.get_me()
    print(f"Authenticated as {me.first_name} (id={me.id})")
    print(f"Session saved to {config.telegram_session_file}.session")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
