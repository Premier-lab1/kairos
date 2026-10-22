# JSON Schemas

[JSON Schema](https://json-schema.org/) (draft 2020-12) descriptions of the main Kairos records, with a description for every field. They are derived from the SQL migrations in [`backend/migrations/`](../backend/migrations), the scoring and outcome code in `backend/kairos/`, and the dashboard queries in `web/src/lib/server/queries/`.

| Schema | Record | Table or source |
| --- | --- | --- |
| [`signal.schema.json`](signal.schema.json) | A scored token, including the full `features` object | `signals` |
| [`token.schema.json`](token.schema.json) | A launchpad token and its creator wallet | `tokens` (+ `wallets.address`) |
| [`shiller.schema.json`](shiller.schema.json) | An X account that posts contract addresses, with its derived track record | `kols` (+ dashboard stats, `kol_wallets`) |
| [`mention.schema.json`](mention.schema.json) | One tweet with a contract address (a "call") | `mentions` |
| [`outcome.schema.json`](outcome.schema.json) | How a token ended on one horizon under one rule version | `token_outcomes` |

Real records that validate against these schemas are in [`../examples`](../examples).

## Notes

- **Rows, not an API.** There is no documented public JSON API. A schema describes a database row (timestamps in ISO 8601, numerics as JSON numbers) or, for `shiller`, the row plus the statistics the dashboard computes. The `creator_wallet` field of `token` and the `stats` and `wallets` blocks of `shiller` are derived, not columns.
- **Older signals.** Signals created before 21.10.2026 have no `onchain` key per shiller, no `kol_*` components and no `shillers_with_onchain` / `shillers_holding` evidence. The schema marks these as optional. On 22.10.2026 `onchain` is still `null` in every production signal because no shiller-wallet trades (`kol_trades`) had been collected yet.
- **No look-ahead.** Everything in `signal.features` is what was known at `features.evaluated_at`. The rule is described in [`docs/signals.md`](../docs/signals.md).
- **Outcomes.** Labels, thresholds and the `metrics` evidence are described in [`docs/outcomes.md`](../docs/outcomes.md). `horizon` keeps PostgreSQL's interval text: 24 hours prints as `1 day`.
- **Strictness.** Row-level objects reject unknown properties so that schema drift is noticed; `outcome.metrics` allows extra keys because its content depends on the rule version.
- The schemas were also checked against a random sample of production rows (all 628 signals, 3000 tokens, 3000 mentions, 4000 outcomes, 1500 shillers) with no violations.

## Validate

```bash
pip install jsonschema
python schemas/validate.py
```

The same command runs in CI (see [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)).
