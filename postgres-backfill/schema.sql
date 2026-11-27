-- The table being backfilled. amount_usd_cents is the new column: it was
-- added as nullable (instant in Postgres), and the backfill fills it in.
CREATE TABLE IF NOT EXISTS orders (
    id               bigserial PRIMARY KEY,
    amount_minor     bigint      NOT NULL,  -- amount in the currency's minor unit (cents, pence, yen)
    currency         char(3)     NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    amount_usd_cents bigint                 -- new column, NULL until backfilled
);

-- One row per backfill flow run: where it is and how far it got. Each chunk
-- step updates this row in the same transaction as its writes to orders, so
-- last_id is always the last chunk that committed.
CREATE TABLE IF NOT EXISTS backfill_runs (
    flow_run_id    text PRIMARY KEY,
    resumed_from   text,                  -- the earlier run this one picked up from
    chunk_size     integer     NOT NULL,
    rows_total     bigint      NOT NULL,  -- rows in orders when the run planned
    rows_missing   bigint      NOT NULL,  -- rows with no amount_usd_cents when the run planned
    rows_scanned   bigint      NOT NULL DEFAULT 0,
    rows_updated   bigint      NOT NULL DEFAULT 0,
    chunks_done    integer     NOT NULL DEFAULT 0,  -- chunks this run finished
    last_id        bigint      NOT NULL DEFAULT 0,
    failed_once_at timestamptz,           -- set if the run simulated a chunk failure (fail_chunk)
    started_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz
);
