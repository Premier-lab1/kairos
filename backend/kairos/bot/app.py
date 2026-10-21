"""Thin aiogram routing and application lifecycle."""

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject

from kairos.config import get_settings
from kairos.db import create_pool
from . import subscribers
from .delivery import BotSender, deliver_pending

logger = logging.getLogger(__name__)


def create_router(pool, settings, explain=None):
    router = Router()
    explain = subscribers.default_explain() if explain is None else explain

    @router.message(Command("start"))
    async def start(message):
        await message.answer(await subscribers.subscribe(pool, message.chat.id, message.chat.username, settings))

    @router.message(Command("threshold"))
    async def threshold(message, command: CommandObject):
        await message.answer(await subscribers.set_threshold(pool, message.chat.id, (command.args or "").strip(), settings=settings))

    @router.message(Command("mute", "unmute"))
    async def mute(message, command: CommandObject):
        await message.answer(await subscribers.set_mute(pool, message.chat.id, (command.args or "").strip(), mute=command.command == "mute",
                                                      settings=settings))

    @router.message(Command("status"))
    async def status(message):
        await message.answer(await subscribers.subscription_status(pool, message.chat.id, settings=settings))

    @router.callback_query(F.data.regexp(r"^why:\d+$"))
    async def why(callback):
        chat_id = callback.message.chat.id if callback.message else callback.from_user.id
        # Подтверждаем нажатие сразу: объяснение LLM может занять дольше срока жизни callback.
        await callback.answer()
        result = await subscribers.why_signal(pool, chat_id, int(callback.data.split(":")[1]), explain, settings=settings)
        if callback.message:
            await callback.message.answer(result)

    @router.callback_query(F.data.regexp(r"^mute:\d+$"))
    async def mute_callback(callback):
        chat_id = callback.message.chat.id if callback.message else callback.from_user.id
        result = await subscribers.set_mute(pool, chat_id, kol_id=int(callback.data.split(":")[1]), settings=settings)
        # Callback answers are plain text, whereas command replies use HTML.
        from html import unescape
        await callback.answer(unescape(result))

    return router


async def main(settings=None):
    settings = settings or get_settings()
    if settings.telegram_bot_token is None:
        logger.error("TELEGRAM_BOT_TOKEN is not set")
        return 2
    pool = await create_pool(settings.database_url)
    task = None
    try:
        async with Bot(settings.telegram_bot_token.get_secret_value(),
                       default=DefaultBotProperties(parse_mode=ParseMode.HTML)) as bot:
            dispatcher = Dispatcher()
            dispatcher.include_router(create_router(pool, settings))

            async def delivery_loop():
                while True:
                    try:
                        await deliver_pending(pool, BotSender(bot), settings=settings)
                    except Exception:
                        logger.exception("Delivery pass failed")
                    await asyncio.sleep(settings.bot_poll_interval_s)

            task = asyncio.create_task(delivery_loop())
            try:
                await dispatcher.start_polling(bot, close_bot_session=False)
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
    finally:
        await pool.close()
    return 0
