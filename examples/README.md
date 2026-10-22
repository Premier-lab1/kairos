# Examples

Real records from the production database, read-only, taken on 22.10.2026. Nothing is invented or edited except where noted below. They all belong to one story: a token, the signal Kairos computed for it, the shiller who posted it, and how it ended.

| File | What it is | Schema |
| --- | --- | --- |
| [`token.json`](token.json) | `$WIFSI` (Super Wif Inu), a Pump.fun token created 20.10.2026 17:20 UTC | [`token.schema.json`](../schemas/token.schema.json) |
| [`mention.json`](mention.json) | The tweet in which `@solhousesignals` posted its contract address, 1 minute 50 seconds after creation | [`mention.schema.json`](../schemas/mention.schema.json) |
| [`shiller.json`](shiller.json) | The account behind that tweet, with the statistics the dashboard shows | [`shiller.schema.json`](../schemas/shiller.schema.json) |
| [`signal.json`](signal.json) | Signal 242: the s1 score and every feature behind it | [`signal.schema.json`](../schemas/signal.schema.json) |
| [`outcome.json`](outcome.json) | What happened in the first hour after the shill (rule set v2, `first_mention`, 1 hour) | [`outcome.schema.json`](../schemas/outcome.schema.json) |
| [`telegram-message.md`](telegram-message.md) | The Telegram card and the "Why?" reply for that signal, rendered by the real formatter | none |

All JSON files validate against the schemas: `python schemas/validate.py`.

## The story in one paragraph

`$WIFSI` was created at 17:20:54 UTC. At 17:22:44 `@solhousesignals`, an account that has posted 221 distinct contract addresses, tweeted this one. Kairos found the tweet through a Frontrun search, scored the token at 19:52 UTC and got **0 out of 100 with `low` confidence**: a single shiller, no past outcomes to learn from (`n_final = 0`), so no points from `shillers`, `breadth` or `creator_clean`. Within about four minutes of the tweet the token lost 97% of its market cap and liquidity fell from 32 to 2.5 SOL, and the first-hour outcome is `rug`, with reasons `liquidity_crash`, `collapse` and `fast_dump`. The score pointing the right way (nothing to see) is the intended behaviour, not a prediction claim: one example proves nothing about quality.

## Read these first

- **Why is the score 0 and the confidence `low`?** Production data is young. On 22.10.2026 all 628 stored signals have `confidence = low`, and the highest score is 20. Shillers need at least 5 (for points) and 10 (for `medium`) tokens with a final 24-hour outcome, and almost no outcomes are final yet: most horizons are still `pending`, either because they have not ended or because the trades needed to judge them are not fully loaded. This signal would never be delivered: the bot sends only `medium` or `high` confidence at or above the user's threshold (default 60). See [`telegram-message.md`](telegram-message.md).
- **No LLM explanation here.** Explanations are generated on demand when a subscriber presses `Why?`, then cached in `signal_explanations`. That table is empty in production, so the example shows the plain fallback the bot sends without one. Nothing was written by hand.
- **Format age.** This signal was created before 21.10.2026, so its `features` lack the later `onchain` (shiller wallet behaviour) fields. See [`signal.schema.json`](../schemas/signal.schema.json), where those fields are optional.

## Fields worth knowing

**`signal.json`**

- `score` is the sum of `features.components`, clamped to 0-100. The LLM never touches it.
- `features.inside` lists the distinct shillers before the evaluation time, with their history: `n_final` past tokens with a final label, `success` and `rug` counts, `lift` (shrunk success rate relative to the base rate; about 1.0 for a newcomer), and Wilson lower bounds (display only).
- `features.base.default_used = true` means fewer than 100 final outcomes exist, so the base rates 5% success and 30% rug are fixed defaults.
- `features.token.age_minutes` is minutes since the first mention at evaluation time; this is the `minute N` in the Telegram header. `entry_minute` is minutes from creation to the first mention.
- `features.risks` are the codes shown next to the score. Full formula: [`docs/signals.md`](../docs/signals.md).

**`outcome.json`**

- `label = rug` with `metrics.rug_reasons = [liquidity_crash, collapse, fast_dump]`: liquidity fell 92% in under four minutes (`metrics.liquidity_crash`), the market cap ended at 3% of the reference (`end_multiple`), and a more-than-90% market-cap drop happened within 30 minutes (`metrics.fast_dump`, a v2 rule).
- `reference = first_mention` means the window starts at the shill (17:22:44), not at creation. `horizon` is PostgreSQL interval text (`01:00:00` = one hour).
- Market caps in `metrics` are in SOL. Rules: [`docs/outcomes.md`](../docs/outcomes.md).

**`shiller.json`**

- `stats.finals = 0`: all 221 of this account's calls are still waiting for a final 24-hour outcome, so `win_rate` is `null`. The dashboard ranks shillers by `wilson_lower_bound`, which is 0 until there are outcomes.
- `stats.hold` is zeros because no wallet of this account is tracked yet.

**`token.json`**

- `creator_wallet` is a derived field (the address behind `creator_wallet_id`). `created_at` and `migrated_at` fall within the same second. `created_at` is the time of the first event the collector saw for the mint, so treat the pair with care.

## What was changed from the database rows

- Long numeric values were rounded for readability (6 decimals in `outcome.json`, `initial_*` in `token.json`).
- `token.json` gained `creator_wallet`; `shiller.json` is the `kols` row plus the dashboard statistics, without the profile picture URL and without bookkeeping columns.
- The shiller's bio was dropped and the tweet text is kept as stored. Only public data is included: the tweet, the public handle and public on-chain addresses.

Not financial advice. These files show how the data looks, not what to buy.
