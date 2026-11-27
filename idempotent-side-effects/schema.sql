-- One row per refund credit. The primary key is the idempotency key, which is
-- derived from the business input (account and order), so a retried attempt
-- or a second run for the same order can't add a second credit.
CREATE TABLE IF NOT EXISTS refund_credits (
    idempotency_key   TEXT PRIMARY KEY,
    account_id        TEXT NOT NULL,
    order_id          TEXT NOT NULL,
    amount_cents      INTEGER NOT NULL CHECK (amount_cents > 0),
    customer_message  TEXT NOT NULL,
    -- The task run that issued the credit. Retries of a run share its ID.
    task_run_id       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
