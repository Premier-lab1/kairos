"""Subscription commands; accepts a pool and plain command arguments."""

import logging

from kairos.config import get_settings
from . import texts
from .format import features_of

logger = logging.getLogger(__name__)
ALLOWLIST_NOBODY = "none"   # BOT_ALLOWLIST=none — закрытый список, в котором пока никого нет
_skipped_logged: set = set()   # чаты, об отказе которым уже писали в лог (раз на процесс)


def is_allowed(chat_id, allowlist):
    """Пусто — пускаем всех; «none» — никого; иначе — только перечисленные chat_id."""
    parts = [part.strip() for part in (allowlist or "").split(",") if part.strip()]
    if not parts:
        return True
    if len(parts) == 1 and parts[0].lower() == ALLOWLIST_NOBODY:
        return False
    return str(chat_id) in parts


def allowed_or_skip(chat_id, settings, where):
    """Проверка списка для уже подписанных (рассылка, команды): список закрыли или сузили — чат пропускаем,
    строку подписки не трогаем (вернут в список — всё продолжится). В лог — один раз на чат за процесс."""
    if is_allowed(chat_id, settings.bot_allowlist):
        return True
    if chat_id not in _skipped_logged:
        _skipped_logged.add(chat_id)
        logger.info("closed beta: skipping chat_id=%s (%s): not in BOT_ALLOWLIST", chat_id, where)
    return False


async def get_subscriber(pool, chat_id):
    return await pool.fetchrow("SELECT * FROM bot_subscribers WHERE chat_id=$1 AND active", chat_id)


async def _active(pool, chat_id, settings):
    """(подписка, ответ): команды подписчика отвечают текстом закрытой беты, если чат вне списка,
    и подсказкой /start, если подписки нет."""
    if not allowed_or_skip(chat_id, settings or get_settings(), "command"):
        return None, texts.closed_beta(chat_id)
    row = await get_subscriber(pool, chat_id)
    return row, (None if row is not None else texts.START_HINT)


async def subscribe(pool, chat_id, username, settings):
    if not is_allowed(chat_id, settings.bot_allowlist):
        # Оператор находит отказ в docker logs и добавляет chat_id в BOT_ALLOWLIST.
        logger.info("closed beta: rejected chat_id=%s username=%s", chat_id, username or "-")
        return texts.closed_beta(chat_id)
    row = await pool.fetchrow(
        """INSERT INTO bot_subscribers(chat_id, username, threshold) VALUES ($1,$2,$3)
           ON CONFLICT(chat_id) DO UPDATE SET active=true, username=excluded.username,
           updated_at=now() RETURNING *""", chat_id, username, settings.signal_score_threshold)
    return texts.welcome(row["threshold"])


async def set_threshold(pool, chat_id, argument="", settings=None):
    row, reply = await _active(pool, chat_id, settings)
    if reply:
        return reply
    if argument:
        try:
            value = int(argument)
            if not 0 <= value <= 100:
                raise ValueError
        except ValueError:
            return texts.INVALID_THRESHOLD
        await pool.execute("UPDATE bot_subscribers SET threshold=$2, updated_at=now() WHERE chat_id=$1", chat_id, value)
        return texts.threshold(value)
    return texts.threshold(row["threshold"])


async def set_mute(pool, chat_id, handle="", *, mute=True, kol_id=None, settings=None):
    _, reply = await _active(pool, chat_id, settings)
    if reply:
        return reply
    if not handle and kol_id is None:
        await pool.execute("UPDATE bot_subscribers SET paused=$2, updated_at=now() WHERE chat_id=$1", chat_id, mute)
        return texts.PAUSED if mute else texts.RESUMED
    if kol_id is not None:
        kol = await pool.fetchrow("SELECT id,username FROM kols WHERE id=$1", kol_id)
    else:
        kol = await pool.fetchrow("SELECT id,username FROM kols WHERE lower(username)=lower($1)", handle.lstrip("@"))
    if kol is None:
        return texts.UNKNOWN_SHILLER
    if mute:
        await pool.execute("INSERT INTO bot_muted_kols(chat_id,kol_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", chat_id, kol["id"])
    else:
        await pool.execute("DELETE FROM bot_muted_kols WHERE chat_id=$1 AND kol_id=$2", chat_id, kol["id"])
    return texts.muted(kol["username"], mute)


async def subscription_status(pool, chat_id, settings=None):
    row, reply = await _active(pool, chat_id, settings)
    if reply:
        return reply
    handles = await pool.fetch("""SELECT k.username FROM bot_muted_kols m JOIN kols k ON k.id=m.kol_id
                                  WHERE m.chat_id=$1 ORDER BY lower(k.username)""", chat_id)
    stats = await pool.fetchrow("SELECT count(*) AS n, max(sent_at) AS last FROM signal_deliveries WHERE chat_id=$1", chat_id)
    return texts.status(row, [r["username"] for r in handles], stats["n"], stats["last"])


def default_explain():
    try:
        from kairos.explain import explain_signal
    except ImportError:
        return None
    return explain_signal


async def why_signal(pool, chat_id, signal_id, explain=None, settings=None):
    _, reply = await _active(pool, chat_id, settings)
    if reply:
        return reply
    row = await pool.fetchrow("SELECT * FROM signals WHERE id=$1", signal_id)
    if row is None:
        return texts.SIGNAL_MISSING
    body = await explain(pool, signal_id) if explain is not None else None
    return texts.why(texts.safe(body) if body is not None else texts.why_fallback(features_of(row)))
