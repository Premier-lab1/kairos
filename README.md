# Kairos

Early signals for Solana trenches. Kairos watches new pump.fun tokens, finds which X (Twitter) shillers are already posting the contract address, and checks how those shillers' past calls ended. It does this before the crowd arrives.

- Site and public dashboard: https://xkairos.io (dashboard at [xkairos.io/app](https://xkairos.io/app))
- X: [@max_reporter](https://x.com/max_reporter)
- Telegram bot: [@kairostrench_bot](https://t.me/kairostrench_bot) (closed beta)

> Not financial advice. Kairos shows data and statistics about past behaviour; it does not tell anyone what to buy.

## How it works

1. **New tokens.** New pump.fun tokens and migrations stream in for free from PumpPortal.
2. **Who is shilling.** For each token, Kairos searches X for its contract address through the [Frontrun](https://frontrun.pro) API. Every author becomes a tracked shiller with followers, smart followers (SMARTS) and the number of distinct CAs they have posted.
3. **On-chain history.** Trades come from Helius. Outcome labels (success, rug, dead, neutral) are computed from the price path after the first shill. The rules are in [docs/outcomes.md](docs/outcomes.md).
4. **KOL wallets.** Wallets linked to shillers are polled for their own trades. This gives median hold time, fast dumps, and selling after one's own call.
5. **Score.** Rules and statistics, not a black box: each shiller's track record (Wilson lower bound of the win rate), how early the entry was, how many shillers are in, creator history and KOL on-chain behaviour. Every risk is listed next to the score. See [docs/signals.md](docs/signals.md).
6. **Delivery.** A Telegram bot sends signals above a user's threshold, with a "Why?" explanation written by Claude from the signal's facts. The LLM never sets the score.

## Repository

| Path | What |
| --- | --- |
| `backend/` | Python 3.11 asyncio: collectors, Frontrun/Helius clients, Postgres job queue with daily credit budgets, outcomes, scoring, backtest, Telegram bot (aiogram), LLM explanations. Migrations in `backend/migrations`, tests in `backend/tests` |
| `web/` | Next.js 16 + Tailwind v4: the landing page and the public dashboard (tokens, signals, shillers leaderboard, KOL trades, narratives, logic). Reads Postgres under a read-only role |
| `deploy/` | Docker Compose (TimescaleDB, Redis, backend, bot, web, Caddy for HTTPS), backup script, step-by-step server guide |
| `docs/` | Outcome and scoring rules, Frontrun API notes, design brief, screenshots |

Code comments and the internal docs are mostly in Russian; the product UI is in English.

## Running locally

Requirements: Python 3.11+, Node 22+, PostgreSQL 16+ (TimescaleDB optional), API keys for Frontrun and Helius.

```bash
cp .env.example .env        # fill in DATABASE_URL, FRONTRUN_API_KEY, HELIUS_API_KEY
cd backend && python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m kairos.db              # applies migrations
.venv/bin/python -m kairos.pipeline.run    # collection, scheduler, workers, outcomes and scoring
```

```bash
cd web && npm install
DATABASE_URL_RO=postgresql://... npm run dev   # http://localhost:3000
```

Details are in [backend/README.md](backend/README.md) and [web/README.md](web/README.md). The production setup (one ARM server, Docker Compose, Caddy) is in [deploy/README.md](deploy/README.md).

Tests: `cd backend && KAIROS_TEST_DATABASE_URL=postgresql://.../kairos_test python -m pytest tests`.

## License

[MIT](LICENSE)
