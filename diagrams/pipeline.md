# Pipeline: from a new token to a Telegram message

The sequence for one token. Steps 1-4 take minutes: the token is scored and, if the score and the evidence are high enough, delivered. Steps 5 and 6 come later: trades are downloaded after each outcome checkpoint (the reference moment plus 1 h, 24 h or 7 d, plus 5 minutes) and only then is the outcome label final. A token's own outcome therefore never reaches its own score; outcomes of earlier tokens are what shiller track records are made of.

```mermaid
sequenceDiagram
    participant PP as PumpPortal
    participant COL as Collector
    participant DB as Postgres
    participant SCH as Scheduler
    participant FW as Frontrun worker
    participant FR as Frontrun API
    participant HW as Helius worker
    participant HE as Helius RPC
    participant SC as Scoring loop
    participant BOT as Bot
    participant TG as Telegram

    rect rgba(128, 128, 128, 0.15)
    Note over PP,DB: 1. New token
    PP->>COL: new token / migration event
    COL->>DB: upsert tokens, creator wallet, first snapshot
    end

    rect rgba(128, 128, 128, 0.15)
    Note over SCH,FR: 2. Who is shilling it
    loop every 30 s
        SCH->>DB: enqueue frontrun_search_ca (migrated: priority 10, 5% sample of new: priority 1, +5 min)
    end
    FW->>DB: claim job (daily credit budget permitting)
    FW->>FR: search X for the contract address
    FR-->>FW: tweets with the CA
    FW->>DB: upsert kols and mentions
    Note over FW,DB: author with 1000+ followers becomes tracked
    FW->>DB: enqueue frontrun_ca_history for the new tracked shiller
    FW->>FR: that shiller's past CA posts
    FR-->>FW: history of CAs
    FW->>DB: mentions and tokens (source frontrun_ca_history)
    end

    rect rgba(128, 128, 128, 0.15)
    Note over SC,DB: 3. Score
    loop every 60 s
        SC->>DB: tokens with new mentions (first shill under 6 h old)
        SC->>DB: load history: mentions, final outcomes of earlier tokens, kol trades
        Note over SC: features at time t only, no look-ahead
        SC->>DB: upsert signals (score, confidence, features)
    end
    end

    rect rgba(128, 128, 128, 0.15)
    Note over BOT,TG: 4. Delivery
    loop every 20 s
        BOT->>DB: signals with score >= threshold and confidence medium or high, not yet delivered
        BOT->>TG: send card with Why? / pump.fun / Mute
        BOT->>DB: signal_deliveries
    end
    TG->>BOT: user presses Why?
    BOT->>DB: cached explanation for these facts?
    alt not cached and ANTHROPIC_API_KEY set
        BOT->>BOT: ask Claude to explain the facts (LLM never sets the score)
        BOT->>DB: store in signal_explanations
    end
    BOT->>TG: explanation, or a plain facts summary if there is none
    end

    rect rgba(128, 128, 128, 0.15)
    Note over SCH,HE: 5. Trades, after each checkpoint
    SCH->>DB: enqueue helius_backfill at reference + 1 h / 24 h / 7 d + 5 min
    HW->>DB: claim job
    HW->>HE: getTransactionsForAddress (pages of 100)
    HE-->>HW: Pump.fun and pool trades
    HW->>DB: trades, snapshots, tokens.trades_synced_until
    end

    rect rgba(128, 128, 128, 0.15)
    Note over HW,DB: 6. Outcome label
    HW->>DB: recompute token_outcomes (v1 and v2)
    Note over DB: also every 5 min by the outcomes loop. success / rug / dead / neutral, or pending until the horizon is final
    end
```

## Details that matter

- **Search is sampled.** About 13 new tokens appear per minute and a search costs 2 Frontrun credits, so searching everything would cost around 37 000 credits a day against a budget of 3000. All migrated tokens are searched; of new tokens, 5% by a hash of the mint.
- **Time of the signal.** A token is scored while its first shill is younger than `SIGNAL_MAX_AGE_HOURS` (6). `signals` has one row per token and scorer version, updated as more shillers appear (or shiller wallets trade it).
- **What the score reads.** Final outcomes of other tokens (24 h, from first mention) are the shillers' track records; the creator's earlier tokens use the `creation` reference. A past token counts only if its horizon had ended by the evaluation time. See [`docs/signals.md`](../docs/signals.md).
- **Shiller wallets.** In parallel, wallets linked to top shillers are found through Frontrun and polled through Helius; their trades feed the `onchain` fields (holding, quick dumps, selling after a call). Not drawn above to keep the main path readable.
- **Delivery limits.** Each signal goes to a chat once, at most 20 per chat per pass; shillers a subscriber muted are skipped, and a signal is not sent if all of its shillers are muted.
