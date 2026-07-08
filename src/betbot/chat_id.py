from __future__ import annotations

import asyncio

from telegram import Bot

from .config import load_settings


async def main() -> None:
    settings = load_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("Configure TELEGRAM_BOT_TOKEN no .env antes de rodar.")
    bot = Bot(settings.telegram_bot_token)
    updates = await bot.get_updates()
    if not updates:
        print("Nenhuma mensagem encontrada. Envie qualquer mensagem para o bot e rode novamente.")
        return
    for update in updates[-10:]:
        message = update.effective_message
        chat = update.effective_chat
        if message and chat:
            print(f"chat_id={chat.id} | nome={chat.title or chat.full_name} | texto={message.text}")


if __name__ == "__main__":
    asyncio.run(main())
