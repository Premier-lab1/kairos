CREATE TABLE bot_subscribers (
    chat_id bigint PRIMARY KEY,
    username text,
    threshold integer NOT NULL CHECK (threshold BETWEEN 0 AND 100),
    paused boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE bot_muted_kols (
    chat_id bigint REFERENCES bot_subscribers ON DELETE CASCADE,
    kol_id bigint REFERENCES kols(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, kol_id)
);
CREATE TABLE signal_deliveries (
    signal_id bigint REFERENCES signals(id) ON DELETE CASCADE,
    chat_id bigint REFERENCES bot_subscribers ON DELETE CASCADE,
    sent_at timestamptz NOT NULL DEFAULT now(),
    message_id bigint,
    PRIMARY KEY (signal_id, chat_id)
);
CREATE INDEX signal_deliveries_chat_sent_idx ON signal_deliveries (chat_id, sent_at DESC);
