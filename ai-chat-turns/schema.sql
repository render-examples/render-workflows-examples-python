-- One row per conversation. active_message_id is the claim: the user message
-- whose turn is running. NULL means no turn is running.
CREATE TABLE IF NOT EXISTS conversations (
    id                 TEXT PRIMARY KEY,
    active_message_id  BIGINT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id                 BIGSERIAL PRIMARY KEY,
    conversation_id    TEXT NOT NULL REFERENCES conversations (id),
    role               TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content            TEXT NOT NULL,
    -- Set by the client on user messages, so a resent message isn't stored twice.
    client_message_id  TEXT,
    -- On assistant messages: the user message this replies to.
    reply_to           BIGINT REFERENCES messages (id),
    -- On user messages: pending -> running -> done | failed.
    status             TEXT NOT NULL DEFAULT 'done',
    task_run_id        TEXT,
    error              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, client_message_id)
);

-- A user message gets at most one reply, even if its turn runs twice.
CREATE UNIQUE INDEX IF NOT EXISTS messages_one_reply ON messages (reply_to)
    WHERE reply_to IS NOT NULL;

-- Written by the open_refund_request tool. The key is derived from the
-- conversation and the order, so a retried turn or a follow-up question
-- doesn't open a second request for the same order.
CREATE TABLE IF NOT EXISTS refund_requests (
    idempotency_key    TEXT PRIMARY KEY,
    conversation_id    TEXT NOT NULL REFERENCES conversations (id),
    message_id         BIGINT NOT NULL REFERENCES messages (id),
    order_id           TEXT NOT NULL,
    reason             TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
