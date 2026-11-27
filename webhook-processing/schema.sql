-- Every delivery the receiver accepted, keyed by GitHub's delivery ID.
-- A redelivery from GitHub has the same delivery ID as the original.
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    delivery_id  text PRIMARY KEY,
    event        text NOT NULL,
    payload      jsonb NOT NULL,
    status       text NOT NULL DEFAULT 'pending',  -- pending or processed
    received_at  timestamptz NOT NULL DEFAULT now(),
    receipts     integer NOT NULL DEFAULT 1,       -- times GitHub sent it
    attempts     integer NOT NULL DEFAULT 0,       -- processing attempts
    last_run_id  text,
    last_error   text,
    processed_at timestamptz,
    result       jsonb
);

-- What processing produces: activity per repository. Adding to a counter
-- isn't idempotent, so a delivery must never be applied twice.
CREATE TABLE IF NOT EXISTS repo_activity (
    repo          text PRIMARY KEY,
    pushes        integer NOT NULL DEFAULT 0,
    commits       integer NOT NULL DEFAULT 0,
    issues_opened integer NOT NULL DEFAULT 0,
    issues_closed integer NOT NULL DEFAULT 0,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
