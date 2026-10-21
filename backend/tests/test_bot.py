import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage
from aiogram.types import Update

from kairos.bot import texts
from kairos.bot.app import create_router, main
from kairos.bot.delivery import deliver_pending
from kairos.bot.format import format_signal, signal_keyboard
from kairos.bot.subscribers import (is_allowed, subscribe, get_subscriber, set_threshold, set_mute,
                                    subscription_status, why_signal)
from kairos.db import migrate

DSN = 'postgresql://kairos@localhost:5434/kairos_test_bot'
NOW = datetime.now(timezone.utc)

@pytest.fixture
async def bot_pool():
    conn = await asyncpg.connect(DSN)
    try:
        assert await conn.fetchval('SELECT current_database()') == 'kairos_test_bot'
        await conn.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public;')
        await migrate(conn)
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=3)
    yield pool
    await pool.close()

@pytest.fixture
def settings(monkeypatch):
    settings = SimpleNamespace(bot_allowlist='', signal_score_threshold=60,
                               signal_max_age_hours=6, telegram_bot_token=None)
    # Команды без явных settings берут их из get_settings(): в тестах — эти, а не реальный .env.
    monkeypatch.setattr('kairos.bot.subscribers.get_settings', lambda: settings)
    monkeypatch.setattr('kairos.bot.subscribers._skipped_logged', set())
    return settings


async def test_closed_list_skips_active_subscribers(bot_pool, settings, features, caplog):
    """Список закрыли после подписки: рассылка и команды чат пропускают, строка подписки остаётся активной;
    отказ пишется в лог один раз на чат за процесс. Вернули в список — всё работает снова."""
    await seed(bot_pool, features)
    await subscribe(bot_pool, 1, None, settings)
    await subscribe(bot_pool, 2, None, settings)
    settings.bot_allowlist = '2'
    sender = Sender()
    with caplog.at_level('INFO', logger='kairos.bot.subscribers'):
        assert await deliver(bot_pool, sender, settings) == 1
        assert [m[0] for m in sender.messages] == [2]
        assert await set_threshold(bot_pool, 1, '70', settings=settings) == texts.closed_beta(1)
        assert await set_mute(bot_pool, 1, 'alpha') == texts.closed_beta(1)              # settings из get_settings()
        assert await subscription_status(bot_pool, 1, settings=settings) == texts.closed_beta(1)
        assert await why_signal(bot_pool, 1, 1, AsyncMock(return_value=None), settings=settings) == texts.closed_beta(1)
        assert 'Status: subscribed' in await subscription_status(bot_pool, 2, settings=settings)
    assert caplog.text.count('skipping chat_id=1') == 1 and 'skipping chat_id=2' not in caplog.text
    assert (await get_subscriber(bot_pool, 1))['active'] and (await get_subscriber(bot_pool, 1))['threshold'] == 60
    assert await bot_pool.fetchval('SELECT count(*) FROM signal_deliveries WHERE chat_id=1') == 0
    settings.bot_allowlist = ''
    assert await deliver(bot_pool, Sender(), settings) == 1                                 # догнал чат 1
    assert '70/100' in await set_threshold(bot_pool, 1, '70', settings=settings)


async def test_allowlist_none_stops_delivery(bot_pool, settings, features):
    await seed(bot_pool, features)
    await subscribe(bot_pool, 1, None, settings)
    settings.bot_allowlist = 'none'
    assert await deliver(bot_pool, Sender(), settings) == 0
    assert await set_threshold(bot_pool, 1) == texts.closed_beta(1)
    assert (await get_subscriber(bot_pool, 1))['active']

@pytest.fixture
def features():
    return {'token': {'mint': 'TestMintpump', 'symbol': 'WIF', 'age_minutes': 14.1},
            'inside': [{'kol_id': 1, 'username': 'alpha', 'minute': 3,
                        'success': 5, 'n_final': 12},
                       {'kol_id': 2, 'username': 'beta', 'minute': 9, 'n_final': 0}],
            'similar': [{'symbol': 'XYZ', 'similarity': .72, 'label': 'success'}],
            'creator': {'past_final': 4, 'past_rug': 3}, 'n_shillers': 2,
            'risks': ['creator_rugged_before', 'single_shiller']}

def row(features, score=63):
    return dict(id=1, features=features, score=score, confidence='medium')

async def seed(pool, features, score=80, age=0):
    await pool.execute("INSERT INTO kols(id,username) VALUES (1,'alpha'),(2,'beta') ON CONFLICT DO NOTHING")
    token = await pool.fetchval("INSERT INTO tokens(mint,source) VALUES ($1,'test') RETURNING id", f'mint{score}-{age}-{await pool.fetchval("SELECT count(*) FROM tokens")}')
    return await pool.fetchval('''INSERT INTO signals(token_id,score,scorer_version,features,confidence,created_at,updated_at)
        VALUES ($1,$2,'s1',$3,'medium',$4,$4) RETURNING id''', token, score,
        json.dumps(features), NOW - timedelta(hours=age))

class Sender:
    def __init__(self, error=None):
        self.messages = []
        self.error = error
    async def send_signal(self, chat_id, text, keyboard):
        if self.error:
            raise self.error
        self.messages.append((chat_id, text, keyboard))
        return len(self.messages)

async def deliver(pool, sender, settings):
    return await deliver_pending(pool, sender, now=NOW, settings=settings, delay=0)

def test_format_full(features):
    text = format_signal(row(features))
    for part in ('$WIF', 'minute 14', '42% win rate (5/12)', 'no history yet', '$XYZ — success · 72%', 'evidence: medium'):
        assert part in text

def test_format_minimal():
    assert texts.DISCLAIMER in format_signal({'id': 1})

def test_html_escape(features):
    features['token']['symbol'] = '<&>'
    features['inside'][0]['username'] = '<&>'
    text = format_signal(row(features))
    assert '$&lt;&amp;&gt;' in text and '@&lt;&amp;&gt;' in text
    assert '<&>' not in text

@pytest.mark.parametrize('score,filled', [(0,0),(63,6),(100,10)])
def test_bar(features, score, filled):
    text = format_signal(row(features, score))
    assert '▰' * filled + '▱' * (10-filled) in text

def test_more(features):
    features['inside'] *= 4
    assert '+3 more' in format_signal(row(features))

def test_no_similar(features):
    features.pop('similar')
    assert 'Similar past cases' not in format_signal(row(features))

def test_risks(features):
    features['risks'] += ['unknown<&>']
    text = format_signal(row(features))
    assert 'creator rugged before · single shiller' in text
    assert 'unknown&lt;&amp;&gt;' in text
    assert len(texts.RISK_PHRASES) == 9
    features['risks'] = ['kol_fast_dumper', 'kol_sold_after_call', 'kol_bought_before_call']
    text = format_signal(row(features))
    assert 'shiller usually dumps within minutes · shiller sold within 30 min of the call · shiller bought before the call' in text


def test_onchain_notes(features):
    """Пометка о поведении кошелька шиллера; без данных (onchain отсутствует или null) строка прежняя."""
    features['inside'][1]['onchain'] = None
    assert texts.onchain(features['inside'][0]) == '' and 'bought' not in format_signal(row(features))
    cases = [(dict(holding=True), 'holding on-chain'),
             (dict(holding=False, sold_after_call=True), 'sold after call'),
             (dict(fast_dumper=True, median_hold_s=41.4), 'dumps in ~41 s'),
             (dict(fast_dumper=True), 'fast dumper'),
             (dict(first_buy_at='x', first_sell_at='y'), 'sold'),
             (dict(first_buy_at='x'), 'bought'),
             (dict(closed_positions=0), '')]
    for data, note in cases:
        features['inside'][0]['onchain'] = data
        assert texts.onchain(features['inside'][0]) == note
    features['inside'][0]['onchain'] = dict(holding=True)
    assert '@alpha — min 3 · 42% win rate (5/12) · holding on-chain' in format_signal(row(features))

def test_keyboard(features):
    buttons = signal_keyboard(row(features)).inline_keyboard[0]
    assert [b.text for b in buttons] == ['Why?', 'pump.fun', 'Mute @alpha']
    assert buttons[0].callback_data == 'why:1'
    assert buttons[1].url == 'https://pump.fun/coin/TestMintpump'
    assert buttons[2].callback_data == 'mute:1'
    assert len(signal_keyboard({'id':1}).inline_keyboard[0]) == 2

async def test_subscribe(bot_pool, settings):
    text = await subscribe(bot_pool, 1, 'user', settings)
    assert '60/100' in text and texts.DISCLAIMER in text
    assert (await get_subscriber(bot_pool, 1))['active']

async def test_allowlist(bot_pool, settings, caplog):
    settings.bot_allowlist = '2, 3'
    with caplog.at_level('INFO', logger='kairos.bot.subscribers'):
        text = await subscribe(bot_pool, 1, 'stranger', settings)
    assert text == 'Kairos is in closed beta. Your chat id: 1 — send it to the team to get access.'
    assert text.startswith(texts.CLOSED_BETA)
    assert 'rejected chat_id=1 username=stranger' in caplog.text   # оператор ищет chat_id в docker logs
    assert await get_subscriber(bot_pool, 1) is None
    assert '60/100' in await subscribe(bot_pool, 2, None, settings)


@pytest.mark.parametrize('allowlist', ['none', 'NONE', ' none '])
async def test_allowlist_none_means_nobody(bot_pool, settings, allowlist, caplog):
    """BOT_ALLOWLIST=none — закрытая бета, в которой пока никого нет; пусто — по-прежнему пускаем всех."""
    settings.bot_allowlist = allowlist
    with caplog.at_level('INFO', logger='kairos.bot.subscribers'):
        assert (await subscribe(bot_pool, 1, None, settings)).startswith(texts.CLOSED_BETA)
    assert 'username=-' in caplog.text
    assert await get_subscriber(bot_pool, 1) is None
    settings.bot_allowlist = ''
    assert '60/100' in await subscribe(bot_pool, 1, None, settings)


def test_is_allowed():
    assert is_allowed(5, '') and is_allowed(5, None) and is_allowed(5, ' , ')
    assert not is_allowed(5, 'none') and not is_allowed(5, 'None')
    assert is_allowed(5, '5') and is_allowed(5, '4, 5') and not is_allowed(6, '4, 5')
    assert not is_allowed(5, 'none, 6') and is_allowed(6, 'none, 6')   # «none» рядом с id — просто не id

async def test_reactivate(bot_pool, settings, features):
    await seed(bot_pool, features)
    await subscribe(bot_pool, 1, None, settings)
    await set_threshold(bot_pool, 1, '73')
    await set_mute(bot_pool, 1, 'alpha')
    await bot_pool.execute('UPDATE bot_subscribers SET active=false WHERE chat_id=1')
    await subscribe(bot_pool, 1, 'new', settings)
    assert (await get_subscriber(bot_pool, 1))['threshold'] == 73
    assert await bot_pool.fetchval('SELECT count(*) FROM bot_muted_kols') == 1

@pytest.mark.parametrize('argument', ['-1','101','abc','1.5','70 80'])
async def test_threshold_invalid(bot_pool, settings, argument):
    await subscribe(bot_pool, 1, None, settings)
    assert await set_threshold(bot_pool, 1, argument) == texts.INVALID_THRESHOLD
    assert (await get_subscriber(bot_pool, 1))['threshold'] == 60

async def test_threshold_show_set(bot_pool, settings):
    await subscribe(bot_pool, 1, None, settings)
    assert '60/100' in await set_threshold(bot_pool, 1)
    for value in ('0', '70', '100'):
        assert f'{value}/100' in await set_threshold(bot_pool, 1, value)

async def test_unsubscribed(bot_pool):
    assert await set_threshold(bot_pool, 1) == texts.START_HINT
    assert await set_mute(bot_pool, 1) == texts.START_HINT
    assert await subscription_status(bot_pool, 1) == texts.START_HINT
    assert await why_signal(bot_pool, 1, 1) == texts.START_HINT

async def test_mute_unmute_pause(bot_pool, settings, features):
    await seed(bot_pool, features)
    await subscribe(bot_pool, 1, None, settings)
    assert await set_mute(bot_pool, 1, '@ALPHA') == 'Muted @alpha'
    assert await set_mute(bot_pool, 1, 'missing') == texts.UNKNOWN_SHILLER
    assert await set_mute(bot_pool, 1, 'alpha', mute=False) == 'Unmuted @alpha'
    assert not await bot_pool.fetchval('SELECT count(*) FROM bot_muted_kols')
    await set_mute(bot_pool, 1)
    assert (await get_subscriber(bot_pool, 1))['paused']
    await set_mute(bot_pool, 1, mute=False)
    assert not (await get_subscriber(bot_pool, 1))['paused']

async def test_status(bot_pool, settings, features):
    await seed(bot_pool, features)
    await subscribe(bot_pool, 1, None, settings)
    await set_mute(bot_pool, 1, 'alpha')
    assert 'Muted shillers: @alpha' in await subscription_status(bot_pool, 1)
    await deliver(bot_pool, Sender(), settings)
    text = await subscription_status(bot_pool, 1)
    assert 'Status: subscribed' in text and 'Signals delivered: 1' in text
    assert NOW.isoformat() in text

async def test_delivery_filters(bot_pool, settings, features):
    await seed(bot_pool, features, score=59)
    await seed(bot_pool, features, score=90, age=7)
    await seed(bot_pool, features, score=60)
    await subscribe(bot_pool, 1, None, settings)
    sender = Sender()
    assert await deliver(bot_pool, sender, settings) == 1
    assert len(sender.messages) == 1

async def test_delivery_skips_low_confidence(bot_pool, settings, features):
    low = await seed(bot_pool, features, score=95)
    await bot_pool.execute("UPDATE signals SET confidence='low' WHERE id=$1", low)
    await seed(bot_pool, features, score=70)
    await subscribe(bot_pool, 1, None, settings)
    sender = Sender()
    assert await deliver(bot_pool, sender, settings) == 1
    assert len(sender.messages) == 1

async def test_delivery_once_and_timestamp(bot_pool, settings, features):
    signal_id = await seed(bot_pool, features)
    await subscribe(bot_pool, 1, None, settings)
    assert await deliver(bot_pool, Sender(), settings) == 1
    await bot_pool.execute('UPDATE signals SET score=99,updated_at=$1', NOW)
    assert await deliver(bot_pool, Sender(), settings) == 0
    await subscribe(bot_pool, 2, None, settings)
    assert await deliver_pending(bot_pool, Sender(), now=NOW+timedelta(minutes=1), settings=settings, delay=0) == 1
    assert await bot_pool.fetchval('SELECT delivered_at FROM signals WHERE id=$1', signal_id) == NOW
    assert await bot_pool.fetchval('SELECT count(*) FROM signal_deliveries') == 2

async def test_all_muted_then_unmute(bot_pool, settings, features):
    await seed(bot_pool, features)
    await subscribe(bot_pool, 1, None, settings)
    for handle in ('alpha','beta'):
        await set_mute(bot_pool, 1, handle)
    assert await deliver(bot_pool, Sender(), settings) == 0
    assert await bot_pool.fetchval('SELECT count(*) FROM signal_deliveries') == 0
    await set_mute(bot_pool, 1, 'alpha', mute=False)
    assert await deliver(bot_pool, Sender(), settings) == 1

async def test_paused(bot_pool, settings, features):
    await seed(bot_pool, features)
    await subscribe(bot_pool, 1, None, settings)
    await set_mute(bot_pool, 1)
    assert await deliver(bot_pool, Sender(), settings) == 0

async def test_forbidden(bot_pool, settings, features):
    await seed(bot_pool, features)
    await subscribe(bot_pool, 1, None, settings)
    error = TelegramForbiddenError(method=SendMessage(chat_id=1,text='x'), message='Forbidden')
    assert await deliver(bot_pool, Sender(error), settings) == 0
    assert not await bot_pool.fetchval('SELECT active FROM bot_subscribers WHERE chat_id=1')

async def test_retry_after(bot_pool, settings, features, monkeypatch):
    await seed(bot_pool, features)
    await subscribe(bot_pool, 1, None, settings)
    sleep = AsyncMock()
    monkeypatch.setattr('kairos.bot.delivery.asyncio.sleep', sleep)
    error = TelegramRetryAfter(method=SendMessage(chat_id=1,text='x'), message='retry', retry_after=50)
    assert await deliver(bot_pool, Sender(error), settings) == 0
    sleep.assert_awaited_once_with(30)

async def test_other_error_continues(bot_pool, settings, features):
    await seed(bot_pool, features)
    await subscribe(bot_pool, 1, None, settings)
    await subscribe(bot_pool, 2, None, settings)
    class Flaky(Sender):
        async def send_signal(self, chat_id, text, keyboard):
            if chat_id == 1:
                raise RuntimeError('fake failure')
            return await super().send_signal(chat_id, text, keyboard)
    assert await deliver(bot_pool, Flaky(), settings) == 1

async def test_limit_and_order(bot_pool, settings, features):
    for age in range(22):
        await seed(bot_pool, features, score=80, age=age/100)
    await subscribe(bot_pool, 1, None, settings)
    sender = Sender()
    assert await deliver(bot_pool, sender, settings) == 20
    ids = [int(m[2].inline_keyboard[0][0].callback_data.split(':')[1]) for m in sender.messages]
    assert ids == list(range(22,2,-1))
    assert await deliver(bot_pool, sender, settings) == 2

@pytest.mark.parametrize('explanation', ['Historical evidence.', None])
async def test_why(bot_pool, settings, features, explanation):
    signal_id = await seed(bot_pool, features)
    await subscribe(bot_pool, 1, None, settings)
    explain = AsyncMock(return_value=explanation)
    text = await why_signal(bot_pool, 1, signal_id, explain)
    explain.assert_awaited_once_with(bot_pool, signal_id)
    assert text.startswith('<b>Why this signal</b>')
    assert (explanation or 'Creator history: 4 final cases, 3 rugs.') in text
    assert text.endswith(texts.DISCLAIMER)

async def test_no_token(settings, monkeypatch, caplog):
    create_pool = AsyncMock(side_effect=AssertionError('must not connect'))
    monkeypatch.setattr('kairos.bot.app.create_pool', create_pool)
    assert await main(settings) == 2
    create_pool.assert_not_called()
    assert 'TELEGRAM_BOT_TOKEN is not set' in caplog.text

class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
    async def close(self):
        pass
    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        return True
    async def stream_content(self, *args, **kwargs):
        if False:
            yield b''

async def test_dispatcher_start(bot_pool, settings):
    session = FakeSession()
    bot = Bot('123456:FAKE_TEST_TOKEN', session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(bot_pool, settings, explain=AsyncMock(return_value=None)))
    update = Update.model_validate({'update_id':1, 'message': {
        'message_id':1, 'date':int(NOW.timestamp()), 'chat':{'id':123,'type':'private','username':'tester'},
        'from':{'id':123,'is_bot':False,'first_name':'Test'}, 'text':'/start',
        'entities':[{'type':'bot_command','offset':0,'length':6}]}})
    await dispatcher.feed_update(bot, update)
    assert (await get_subscriber(bot_pool, 123))['username'] == 'tester'
    assert len(session.calls) == 1
    assert isinstance(session.calls[0], SendMessage)
    assert texts.DISCLAIMER in session.calls[0].text
    await bot.session.close()

@pytest.mark.parametrize('explanation', ['Evidence <&>.', None])
async def test_dispatcher_callbacks(bot_pool, settings, features, explanation):
    signal_id = await seed(bot_pool, features)
    await subscribe(bot_pool, 123, None, settings)
    session = FakeSession()
    bot = Bot('123456:FAKE_TEST_TOKEN', session=session)
    dispatcher = Dispatcher()
    explain = AsyncMock(return_value=explanation)
    dispatcher.include_router(create_router(bot_pool, settings, explain=explain))
    def callback(data, update_id):
        return Update.model_validate({'update_id':update_id, 'callback_query':{
            'id':str(update_id), 'chat_instance':'fake', 'data':data,
            'from':{'id':123,'is_bot':False,'first_name':'Test'},
            'message':{'message_id':1,'date':int(NOW.timestamp()),
                       'chat':{'id':123,'type':'private'}, 'text':'signal'}}})
    await dispatcher.feed_update(bot, callback(f'why:{signal_id}', 1))
    explain.assert_awaited_once_with(bot_pool, signal_id)
    # Сначала подтверждение нажатия (answerCallbackQuery), потом сообщение с объяснением.
    assert type(session.calls[0]).__name__ == 'AnswerCallbackQuery'
    assert '<b>Why this signal</b>' in session.calls[1].text
    assert ('Evidence &lt;&amp;&gt;.' if explanation else 'Top shiller @alpha') in session.calls[1].text
    session.calls.clear()
    await dispatcher.feed_update(bot, callback('mute:1', 2))
    assert await bot_pool.fetchval('SELECT count(*) FROM bot_muted_kols WHERE chat_id=123 AND kol_id=1') == 1
    assert len(session.calls) == 1
    assert session.calls[0].text == 'Muted @alpha'
    await bot.session.close()


async def test_interrupted_pass_keeps_already_sent_deliveries(bot_pool, settings, features):
    """Проход прервался на втором сообщении: первое уже ушло в Telegram и должно остаться записанным,
    иначе следующий проход отправит его повторно."""
    import asyncio
    first = await seed(bot_pool, features, score=80)
    await seed(bot_pool, features, score=81)
    await subscribe(bot_pool, 1, None, settings)

    class Interrupting(Sender):
        async def send_signal(self, chat_id, text, keyboard):
            if self.messages:
                raise asyncio.CancelledError()
            return await super().send_signal(chat_id, text, keyboard)

    with pytest.raises(asyncio.CancelledError):
        await deliver(bot_pool, Interrupting(), settings)
    assert await bot_pool.fetchval('SELECT array_agg(signal_id) FROM signal_deliveries') == [first]
    assert await bot_pool.fetchval('SELECT pg_try_advisory_lock(1)')  # блокировка чата снята
    await bot_pool.execute('SELECT pg_advisory_unlock(1)')
