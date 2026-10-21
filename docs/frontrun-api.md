# Frontrun API — конспект документации

Источник: Notion «Frontrun API» (https://turquoise-earl-bf5.notion.site/Frontrun-API-2428bbcdc225806186dfe420734fad66), прочитан 18.10.2026. Ключ — `FRONTRUN_API_KEY` в `.env`.

## Главное для Kairos

- **Только REST, WebSocket в документации нет.** Прежнее «реалтайм через WebSocket, задержка миллисекунды» не подтвердилось. Реалтайм собираем сами: поток Solana (Helius) → новый CA → запросы в Frontrun.
- Лимиты по умолчанию: **60 запросов/мин, 100 000/сутки**.
- Оплата кредитами. Наш ключ 18.10.2026: **100 000 кредитов** (`includedCredits` 100 000, докупленных 0) — объём тарифа Extension Gold.
- Метки кошельков отдаются частично: `primaryLabel`/`compactLabel` в профиле, Smart Followers есть отдельными эндпоинтами. AI Wallet Tags как отдельного эндпоинта нет.
- Исходов токенов (рост, раг, ликвидность) в API нет — считаем сами по ончейн-данным.
- Права на коммерческое использование и хранение в документации не описаны — вопрос к Frontrun остаётся.

## FOMO Tracker (проверено 21.10.2026)

- Ленты сделок FOMO Tracker (как на frontrun.pro во вкладке FOMO Tracker) в API нет — ни REST, ни WebSocket.
- Есть только `GET /pro/wallet/fomo?fomoUsername=|fomoUserId=` — «доверенные» кошельки одного FOMO-профиля, 1000 кредитов (не найден — 100). Это треть дневного бюджета за один профиль.
- Новая подстраница «Frontrun Trading API Beta» (`https://solana.frontrun.pro/api/v1/trading-api`, ключи `fr_sk_…`, LLM-справка https://www.frontrun.pro/api/llm.txt) — это API для торговли со своих кастодиальных кошельков. Его WebSocket пушит изменения баланса только своих кошельков, для слежения за чужими не годится.
- Аналог FOMO Tracker у нас свой: кошельки шиллеров (`associated-wallets`, 400 кредитов) → их сделки через Helius → таблица `kol_trades`, лента `/app/kols`.

## Тарифы

| План | $/мес | Кредитов |
| --- | --- | --- |
| API Basic | 200 | 300 000 |
| API Standard | 500 | 1 500 000 |
| API Enterprise | 1000 | 4 000 000 |
| Extension Gold | 180 | 100 000 |

Покупка через https://t.me/frontrunintern.

## Эндпоинты

База `https://api.frontrun.pro/api/v1` (часть примеров — через `https://loadbalance.frontrun.pro/api/v1`). Заголовки: `Authorization: Bearer <KEY>`, `accept: application/json`, опционально `X-Copilot-Client-Language: en`.

| Что | Метод и путь | Кредиты | Время ответа | На 100k кредитов |
| --- | --- | --- | --- | --- |
| Остаток кредитов | `GET /user/paid-api/points/<KEY>` | 0 | — | — |
| Поиск твитов (CA, слова, операторы) | `GET /pro/twitter/tweets/search?q=&page_size=1..100&cursor=` | 2 | 1–3 с, таймаут 20 с | 50 000 |
| Трендовые аккаунты | `GET /pro/trending-accounts?window=1h\|3h\|6h\|24h\|7d` | 30 | — | 3 333 |
| Является ли smart follower | `GET /pro/twitter/<user>/is-smart-follower[?twitterId=]` | 5 | — | 20 000 |
| Поиск по старому нику | `GET /pro/twitter/lookup-by-old-username/<old>` | 10 | — | 10 000 |
| Профиль (id, био, подписчики, метки) | `GET /pro/twitter/<user>/info[?twitterId=]` | 10 | — | 10 000 |
| Пакетная проверка кошельков | `POST /pro/twitter/wallets-batch-query` `{wallets:[{chain:"SOLANA"\|"EVM",address}]}` | 100 за совпавший, 5 за несовпавший | — | — |
| Привязанные кошельки X-аккаунта | `GET /pro/twitter/<user>/associated-wallets` | 400 | — | 250 |
| Упомянутые кошельки X-аккаунта | `GET /pro/twitter/<user>/mentioned-wallets` | 500 | — | 200 |
| Число smart followers KOL | `GET /pro/twitter/<user>/smart-followers/count` | 3 | 30–300 мс | 33 333 |
| Топ-20 smart followers KOL | `GET /pro/twitter/<user>/smart-followers` | 10 | 30–300 мс | 10 000 |
| Кошельки FOMO-профиля | `GET /pro/wallet/fomo?fomoUsername=\|fomoUserId=` | 1000 (не найден — 100) | — | 100 |
| История CA, асинхронно | `GET /pro/twitter/<user>/tweets-with-ca-cache` | 50 | 30–300 мс; `data:null` → повтор через ~90 с | 2 000 |
| История CA, синхронно | `GET /pro/twitter/<user>/tweets-with-ca` | 100 | мгновенно из кеша, иначе 10–90 с | 1 000 |
| Лидерборд PnL кошельков | `GET /pro/high-pnl?chain=SOL\|BSC&period=1d\|7d\|30d&limit=1..500&offset=` | 5000 | 90–200 мс | 20 |
| Здоровье | `GET /health` | 0 | ~0,5 с от нас | — |

Особенности ответов:

- Обёртка `{data, code, status, message}`. Ошибки — `{statusCode, errorCode, message}`; часть ошибок возвращает кредиты.
- Smart followers: окончательный результат только при `meta.resolved: true`.
- История CA: `topUndeletedTweets`, `topDeletedTweets` (`tweetId`, `twitterHandle`, `chain`, `ca`, `content`, `isDeleted`, счётчики, `tweetTime`) и итоги `deletedCACount`, `deletedTweetCount`, `undeletedCACount`, `undeletedTweetCount`. Для «шиллер → токен» это ядро.
- Профиль: `twitterId` — стабильный идентификатор, ник может смениться.
- Поиск твитов: `nextCursor: null` — конец выдачи.

## Следствия для архитектуры

1. История CA шиллера стоит 50 кредитов, поэтому её кешируем у себя и обновляем редко: 100k кредитов хватит примерно на 2 000 профилей, полное обновление базы KOL выйдет дорого.
2. Поиск твитов по CA (2 кредита) — самый дешёвый способ узнать «кто уже шиллит этот токен». Это кандидат на основной реалтайм-запрос по новому токену.
3. При 60 запросах в минуту на каждый новый токен можно тратить 1–3 запроса, остальное ставим в очередь (Redis).
4. Привязанные и упомянутые кошельки (400–500 кредитов) запрашиваем только для KOL из нашей базы и один раз.
