"""Pending signal delivery and the production Telegram sender."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from kairos.config import get_settings
from .format import features_of, format_signal, signal_keyboard
from .subscribers import allowed_or_skip

logger = logging.getLogger(__name__)


class Sender(Protocol):
    async def send_signal(self, chat_id: int, text: str, keyboard: InlineKeyboardMarkup) -> int: ...


class BotSender:
    def __init__(self, bot):
        self.bot = bot

    async def send_signal(self, chat_id, text, keyboard):
        message = await self.bot.send_message(chat_id, text, reply_markup=keyboard)
        return message.message_id


async def deliver_pending(pool, sender: Sender, now=None, settings=None, *, delay=0.05) -> int:
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=settings.signal_max_age_hours)
    delivered = 0
    subscribers = await pool.fetch("SELECT * FROM bot_subscribers WHERE active AND NOT paused ORDER BY chat_id")
    for subscriber in subscribers:
        chat_id = subscriber["chat_id"]
        # Список беты закрыли или сузили после /start: таким чатам ничего не шлём, подписку не трогаем.
        if not allowed_or_skip(chat_id, settings, "delivery"):
            continue
        # Сессионная advisory-блокировка на чат не даёт двум проходам слать одно и то же; каждая доставка
        # записывается сразу (без общей транзакции), чтобы сбой посреди пачки не стёр отметки об уже
        # отправленных сообщениях и они не ушли повторно.
        async with pool.acquire() as conn:
            if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", chat_id):
                continue
            try:
                muted = {r["kol_id"] for r in await conn.fetch("SELECT kol_id FROM bot_muted_kols WHERE chat_id=$1", chat_id)}
                # Сигналы с confidence = low не рассылаем: за ними нет истории (docs/signals.md).
                signals = await conn.fetch("""SELECT s.* FROM signals s WHERE score >= $2 AND updated_at >= $3
                    AND confidence IN ('medium', 'high')
                    AND NOT EXISTS (SELECT 1 FROM signal_deliveries d WHERE d.signal_id=s.id AND d.chat_id=$1)
                    ORDER BY s.created_at, s.id""", chat_id, subscriber["threshold"], cutoff)
                sent = 0
                for signal in signals:
                    inside = features_of(signal).get("inside") or []
                    if inside and all(item.get("kol_id") in muted for item in inside):
                        continue
                    if sent >= 20:
                        break
                    try:
                        if sent and delay:
                            await asyncio.sleep(delay)
                        message_id = await sender.send_signal(chat_id, format_signal(signal), signal_keyboard(signal))
                    except TelegramForbiddenError:
                        await conn.execute("UPDATE bot_subscribers SET active=false, updated_at=now() WHERE chat_id=$1", chat_id)
                        break
                    except TelegramRetryAfter as exc:
                        await asyncio.sleep(min(max(exc.retry_after, 0), 30))
                        return delivered
                    except Exception:
                        logger.exception("Signal %s delivery failed for chat %s", signal["id"], chat_id)
                        continue
                    async with conn.transaction():
                        await conn.execute("""INSERT INTO signal_deliveries(signal_id,chat_id,sent_at,message_id)
                            VALUES ($1,$2,$3,$4)""", signal["id"], chat_id, now, message_id)
                        await conn.execute("UPDATE signals SET delivered_at=$2 WHERE id=$1 AND delivered_at IS NULL", signal["id"], now)
                    sent += 1
                    delivered += 1
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", chat_id)
    return delivered
