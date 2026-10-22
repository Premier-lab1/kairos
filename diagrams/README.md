# Diagrams

[Mermaid](https://mermaid.js.org/) diagrams that GitHub renders in the browser. They use the default theme with no hard-coded colours, so they read on both the light and the dark GitHub themes. Each one is checked against the code it describes.

| Diagram | Type | Shows |
| --- | --- | --- |
| [architecture.md](architecture.md) | Flowchart | Services and how they connect: PumpPortal, Frontrun, Helius, Postgres/TimescaleDB, Redis, the pipeline workers, bot, web, Caddy |
| [pipeline.md](pipeline.md) | Sequence | One token from creation to a Telegram message: search X for the CA, shillers, history, trades, outcome, score, delivery |
| [data-model.md](data-model.md) | ER diagram | Main tables and their relations |
| [outcomes.md](outcomes.md) | State diagram | How a token gets `success`, `rug`, `dead`, `neutral`, `pending` or `no_data` |

Related: [`docs/signals.md`](../docs/signals.md) (scoring), [`docs/outcomes.md`](../docs/outcomes.md) (label rules), [`schemas/`](../schemas) (field-level descriptions).
