# Kairos backend

Python 3.11+ (на сервере 3.12), PostgreSQL (на сервере с TimescaleDB). Секреты — в `../.env` (образец `../.env.example`).

## Локально на Windows (временно, до переезда на сервер)

Отдельный кластер PostgreSQL 18 в `X:\Kairos\.localdb\pg`, порт 5434, вход без пароля только с localhost. Базы: `kairos` (рабочая) и `kairos_test` (её пересоздают тесты).

```bash
"/x/Pilgrim Copilot/Postgres/bin/pg_ctl.exe" -D /x/Kairos/.localdb/pg -o "-p 5434 -c listen_addresses=localhost" -l /x/Kairos/.localdb/pg.log start
```

```bash
"/x/Pilgrim Copilot/Postgres/bin/pg_ctl.exe" -D /x/Kairos/.localdb/pg stop
```

Окружение и миграции:

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m kairos.db
```

## Компоненты

| Модуль | Что делает |
| --- | --- |
| `migrations/0001_init.sql` | Схема: шиллеры, кошельки, токены, упоминания, сделки, снимки, исходы, сигналы, учёт API |
| `kairos/db.py` | Пул asyncpg и применение миграций (`python -m kairos.db`) |
| `kairos/collectors/pumpportal.py` | Новые токены и миграции Pump.fun из бесплатного потока PumpPortal (`python -m kairos.collectors.pumpportal --duration 600`) |
| `kairos/sources/frontrun.py` | Клиент Frontrun API: лимит 60 запросов/мин, повторы, учёт кредитов |
| `kairos/pipeline/jobs.py`, `budget.py` | Очередь задач в PostgreSQL и дневной бюджет кредитов по таблице `api_calls` |
| `kairos/pipeline/frontrun_tasks.py` | Поиск CA токена → авторы в `kols`, твиты в `mentions`; авторы от 1 000 подписчиков отслеживаются, их история CA тоже идёт в `mentions` |
| `kairos/pipeline/scheduler.py` | Что искать: все мигрировавшие токены + выборка новых (по умолчанию 5%, через 5 минут после создания) |
| `kairos/sources/helius.py`, `kairos/collectors/helius_trades.py` | Сделки и снимки цены/ликвидности Pump.fun из Helius (`getTransactionsForAddress`, ≈10 кредитов на 100 транзакций); `backfill_token(...)` дозагружает историю токена |
| `kairos/pipeline/helius_tasks.py` | Задачи дозагрузки: для зашилленных и мигрировавших токенов в точках «отсчёт + 1 ч / 24 ч / 7 д + 5 мин»; токены из истории шиллеров — с пробой в 2 страницы (не Pump.fun — помечаются и больше не качаются); после загрузки — пересчёт исходов |
| `kairos/outcomes.py` | Разметка исхода токена по правилам v1 и v2 (`docs/outcomes.md`): `compute_token_outcomes`, `compute_due_outcomes`. Горизонт размечается, только когда сделки загружены до его конца (`tokens.trades_synced_until`), иначе `pending`. Пересчёт всех токенов и сводка: `python -m kairos.outcomes --rule-version all` |
| `kairos/scoring.py` | Скоринг s1 (`docs/signals.md`): скор 0–100, confidence, признаки в `signals.features`; без утечки будущего. Конвейер пересчитывает сигналы раз в минуту; разовый проход: `python -m kairos.scoring --once` |
| `kairos/backtest.py` | Walk-forward бэктест скоринга: `python -m kairos.backtest --rule-version v2 --horizon 24h --delay-min 10 --out ../backtests/<date>.md` |
| `kairos/bot/` | Telegram-бот (aiogram 3), см. раздел «Бот» |
| `kairos/explain.py` | Короткие объяснения сигнала через Claude (раздел «Объяснения LLM») |
| `kairos/report.py` | Отчёт: сводка, рейтинг шиллеров по исходам (нижняя граница Уилсона), расход кредитов: `python -m kairos.report --horizon 24h --min-tokens 3` |
| `kairos/pipeline/run.py` | Всё вместе: сборщик, планировщик, воркеры Frontrun и Helius, расчёт исходов каждые 5 минут: `python -m kairos.pipeline.run --duration 600 --daily-credits 3000 --helius-daily-credits 20000` |

Настройки конвейера (переменные в `.env`, значения по умолчанию в `kairos/config.py`): `FRONTRUN_DAILY_CREDITS=3000`, `SEARCH_NEW_TOKEN_SAMPLE_PCT=5`, `SEARCH_NEW_TOKEN_DELAY_S=300`, `KOL_MIN_FOLLOWERS=1000`, `KOL_HISTORY_REFRESH_DAYS=7`, `HELIUS_DAILY_CREDITS=20000`, `HELIUS_BACKFILL_MAX_PAGES=50`, `BUDGET_PACING=true` (дневные бюджеты тратятся равномерно по суткам: к моменту t доступна доля «прошло с полуночи UTC + 1 час» от лимита; задача, упёршаяся в график, повторяется через 15 минут). Скоринг и бот: `SIGNAL_SCORE_THRESHOLD=60`, `SIGNAL_OUTCOME_RULE_VERSION=v2`, `SIGNAL_MAX_AGE_HOURS=6`, `SIGNAL_INTERVAL_S=60`.

Расход кредитов за сегодня:

```bash
"/x/Pilgrim Copilot/Postgres/bin/psql.exe" -h localhost -p 5434 -U kairos -d kairos -c "select endpoint, count(*), sum(credits) from api_calls where ts >= date_trunc('day', now() at time zone 'utc') at time zone 'utc' group by 1"
```

## Бот

Отдельный процесс: `python -m kairos.bot` (после миграций `python -m kairos.db`). Без `TELEGRAM_BOT_TOKEN` не стартует (код выхода 2).

- Команды: `/start` — подписаться; `/threshold 70` — порог скора (0–100), без числа — показать; `/mute` / `/unmute` — пауза и возобновление всей рассылки; `/mute @handle` / `/unmute @handle` — заглушить шиллера; `/status` — состояние подписки.
- Под сигналом кнопки `Why?` (объяснение LLM или сводка фактов без LLM), `pump.fun`, `Mute @шиллер`.
- Рассылаются сигналы со скором не ниже порога подписчика, `confidence` medium или high и не старше `SIGNAL_MAX_AGE_HOURS`; каждый — один раз на чат. Если все шиллеры сигнала заглушены, сигнал не приходит.
- Настройки: `TELEGRAM_BOT_TOKEN`, `BOT_ALLOWLIST` (chat_id через запятую для закрытой беты; `none` — никого, отказ отвечает пользователю его chat_id и пишет его в лог; пусто — пускаем всех), `SIGNAL_SCORE_THRESHOLD` (порог по умолчанию), `BOT_POLL_INTERVAL_S`.
- Таблицы: `bot_subscribers`, `bot_muted_kols`, `signal_deliveries` (миграция `0006_bot.sql`).

## Объяснения LLM

`kairos.explain.explain_signal` пишет 2–4 предложения на английском только по фактам карточки сигнала; скор считает не LLM. Без `ANTHROPIC_API_KEY` выключено (бот показывает сводку фактов).

- `EXPLAIN_MODEL` (по умолчанию `claude-sonnet-5-5`), `EXPLAIN_DAILY_LIMIT` (500 вызовов в сутки UTC).
- Доли дневного бюджета Frontrun по видам задач: `FRONTRUN_SHARE_SMART_FOLLOWERS_PCT` (10), `FRONTRUN_SHARE_KOL_WALLETS_PCT` (15), `FRONTRUN_SHARE_CA_HISTORY_PCT` (20); поиск CA получает остаток. Доля вида без работы уходит остальным.
- Кэш на сигнал по хешу фактов, скора и confidence (`signal_explanations`, миграция `0007`); вызовы и оценка стоимости — в `api_calls` (`provider = 'llm'`).
- Тексты с советами «купить/продать» и прогнозами отбрасываются. Оценка стоимости: Sonnet 5.5 ≈ $0,003 за объяснение, Haiku 5.5 ≈ $0,0002.

## Тесты

```bash
.venv/Scripts/python -m pytest -q
```

Тесты с пометкой `live` ходят во внешние API и тратят кредиты, по умолчанию они выключены: `pytest -m live`.

Документация: `../docs/frontrun-api.md`, `../docs/outcomes.md` (правила исхода токена).
