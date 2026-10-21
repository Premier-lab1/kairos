# Kairos — лендинг

Next.js 16 (App Router) + Tailwind CSS v4 + three.js. Сайт на английском (русского нет). Все данные на странице демонстрационные (`src/lib/demo.ts`, выдуманные хендлы и тикеры) и помечены бейджем «Demo data · example».

```bash
npm install
npm run dev      # http://localhost:3000
npm run build && npm start
```

## Устройство

| Путь | Что там |
| --- | --- |
| `src/app/globals.css` | Цвета и шрифты (`@theme`): тёмная основа `ink`, светлые секции `paper`, единственный акцент `accent` #ff4f17, цвета исходов `win` / `rug` / `flat`; классы `.headline`, `.mono-label`, анимация появления |
| `src/app/page.tsx` | Порядок секций |
| `src/components/ui/` | Общие: `Section` / `SectionHeading`, `Reveal` (появление при прокрутке), `DemoTag`, `Button` |
| `src/components/sections/` | Секции: Header, Hero, HowItWorks, SignalCard, Graph, Telegram, Roadmap, Cta, Footer |
| `src/components/hero/` | Сцена «кластер-сигнал» на three.js (`scene.ts`), статичный SVG-вариант (`HeroStill.tsx`), общая композиция (`layout.ts`) |
| `src/components/graph/` | Граф «шиллер → токен» на Canvas 2D |
| `src/components/how/`, `signal/`, `telegram/` | Части секций «Как это работает», «Карточка сигнала», макет бота |
| `src/lib/demo.ts` | Демо-данные (шиллеры, токены, исходы, сигнал) |

## Дашборд `/app`

Закрытый дашборд для тестеров: обзор сбора, сигналы, токены (график капитализации с коллами шиллеров), рейтинг шиллеров, страница «Logic» с правилами исходов и скоринга. Только чтение базы.

| Путь | Что там |
| --- | --- |
| `src/proxy.ts`, `src/lib/server/auth.ts`, `src/app/login/` | С 21.10.2026 дашборд открыт без входа. `requireUser()` только делает страницу динамической (`connection()`); `/login` и `/logout` перенаправляют на `/app` |
| `src/lib/server/db.ts`, `src/lib/server/queries/` | Запросы к Postgres (`DATABASE_URL_RO`, роль `kairos_ro`) |
| `src/app/app/` | Страницы дашборда |
| `src/components/chart/` | `CallChart` (lightweight-charts 5): капитализация, объём, аватарки коллов; общий для лендинга (демо, `src/lib/demoChart.ts`) и дашборда |

Локально: `.env.local` с `DATABASE_URL_RO` на копию базы (`kairos_bt`, порт 5434) или на боевую через SSH-туннель под `kairos_ro`.

Подводные камни Next 16 здесь: `cacheComponents` требует, чтобы чтение cookies/базы было внутри `<Suspense>` (это делает layout дашборда), а `requireUser()` вызывает `connection()`; CSS-модули не работают (правило Tailwind в `next.config.ts` обрабатывает все `*.css` как глобальные), поэтому стили графика — в `globals.css` с префиксом `kc-`.

## Правила

- Без анимаций переходов между страницами. Анимация внутри страницы есть; при системной настройке «уменьшить движение» всё показывается статично.
- three.js грузится лениво, только в браузере; пока он не загрузился, нет WebGL или включено «уменьшить движение», показывается статичный SVG.
- Адаптив от 360 px.
- Кнопка «Ранний доступ» пока ведёт на `#cta`: ссылку на бота поставить, когда откроется бета (TODO в `Cta.tsx`).
