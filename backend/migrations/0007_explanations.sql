CREATE TABLE signal_explanations (
    signal_id bigint NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    facts_hash text NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    text text NOT NULL,
    input_tokens integer,
    output_tokens integer,
    cost_usd numeric,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (signal_id, facts_hash)
);
