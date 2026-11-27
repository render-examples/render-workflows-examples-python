"""
Resumable Postgres backfill on Render Workflows

Fills a new column (orders.amount_usd_cents) across a large table, one keyset
chunk at a time. The backfill is a flow, and every chunk is a checkpointed step:

- A chunk that fails (a dropped connection, a lock timeout) retries on its own.
  Chunks that already finished don't run again.
- If the instance running the flow fails, Render replays the flow on a new one.
  Finished chunks return their recorded results, and the run carries on from
  the first chunk that didn't finish.
- If a run ends without finishing (canceled, out of retries, timed out), a new
  run started with resume_from picks up after the last chunk it committed.

Each chunk's write is idempotent (it only fills rows that are still NULL), so a
chunk that ran but never got recorded can safely run again.
"""

import logging
import math
import os
import pathlib

import psycopg
from psycopg.rows import dict_row
from render import FlowContext, Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

SCHEMA = (pathlib.Path(__file__).parent / "schema.sql").read_text()

# Conversion to US cents, in millionths of a cent per minor unit. Integer math
# keeps the result identical across reruns and across the Python and
# TypeScript versions of this example. Static rates for the demo; a real
# backfill would read them from a rates table as of each order's date.
USD_CENTS_PPM = {
    "USD": 1_000_000,  # 1 cent -> 1 cent
    "EUR": 1_080_000,
    "GBP": 1_270_000,
    "CAD": 730_000,
    "AUD": 660_000,
    "JPY": 670_000,  # 1 yen -> 0.67 cents
}

MIN_CHUNK_SIZE = 100
MAX_CHUNK_SIZE = 50_000
MAX_SEED_ROWS = 2_000_000

# A flow run can record up to 10,000 steps (render.com/docs/workflows-limits).
# The backfill uses one per chunk plus "plan" and "finish", so it checks the
# chunk count up front and leaves some headroom.
MAX_CHUNKS = 9_900

# Chunks talk to the database, so a failed chunk retries on its own. Earlier
# chunks don't rerun.
CHUNK_RETRY = Retry(max_retries=3, wait_duration_ms=2000, backoff_scaling=2.0)
DB_RETRY = Retry(max_retries=2, wait_duration_ms=2000, backoff_scaling=2.0)

app = Workflows()


def connect() -> psycopg.Connection:
    # One connection per step. After a replay, a step can run in a new process,
    # so it doesn't rely on state from earlier steps.
    return psycopg.connect(os.environ["DATABASE_URL"])


def to_usd_cents(amount_minor: int, currency: str) -> int | None:
    rate = USD_CENTS_PPM.get(currency)
    if rate is None:
        return None
    return (amount_minor * rate + 500_000) // 1_000_000  # round half up


# ============================================================================
# Steps: plain functions the flow runs with ctx.step. They get no context, and
# their arguments and return values must be JSON-serializable.
# ============================================================================


def plan_backfill(flow_run_id: str, chunk_size: int, resume_from: str | None) -> dict:
    """Count the work, find where to start, and create this run's progress row."""
    with connect() as conn:
        start_after_id, rows_scanned, rows_updated = 0, 0, 0
        if resume_from:
            previous = conn.execute(
                "SELECT last_id, rows_scanned, rows_updated FROM backfill_runs WHERE flow_run_id = %s",
                (resume_from,),
            ).fetchone()
            if previous is None:
                return {"error": f"no backfill run {resume_from} to resume from"}
            start_after_id, rows_scanned, rows_updated = previous

        rows_total, rows_missing = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE amount_usd_cents IS NULL) FROM orders"
        ).fetchone()
        # ON CONFLICT: if this step runs again after a replay, keep the first row.
        conn.execute(
            """
            INSERT INTO backfill_runs (flow_run_id, resumed_from, chunk_size, rows_total, rows_missing,
                                       last_id, rows_scanned, rows_updated)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (flow_run_id) DO NOTHING
            """,
            (flow_run_id, resume_from, chunk_size, rows_total, rows_missing,
             start_after_id, rows_scanned, rows_updated),
        )
    logger.info(
        "Planned %s: %d rows, %d missing amount_usd_cents, starting after id %d, chunks of %d",
        flow_run_id,
        rows_total,
        rows_missing,
        start_after_id,
        chunk_size,
    )
    return {
        "rows_total": rows_total,
        "rows_missing": rows_missing,
        "start_after_id": start_after_id,
        "rows_scanned": rows_scanned,
        "rows_updated": rows_updated,
    }


def backfill_chunk(
    flow_run_id: str,
    chunk: int,
    after_id: int,
    chunk_size: int,
    rows_total: int,
    rows_scanned: int,
    rows_updated: int,
    fail_chunk: int | None,
) -> dict:
    """Fill one keyset chunk: the next chunk_size rows with id > after_id.

    The chunk boundaries come from the primary key alone, so a chunk covers the
    same rows every time it runs. The UPDATE only touches rows that are still
    NULL, so running a chunk twice writes nothing the second time.
    """
    fail = False
    with connect() as conn:
        # Don't queue behind app traffic for long. A lock timeout raises, and
        # the step retries.
        conn.execute("SET LOCAL lock_timeout = '5s'")
        rows = conn.execute(
            "SELECT id, amount_minor, currency FROM orders WHERE id > %s ORDER BY id LIMIT %s",
            (after_id, chunk_size),
        ).fetchall()
        if not rows:
            return {"chunk": chunk, "scanned": 0, "updated": 0, "skipped": 0, "last_id": after_id}

        last_id = rows[-1][0]
        ids, values, skipped = [], [], 0
        for row_id, amount_minor, currency in rows:
            usd = to_usd_cents(amount_minor, currency)
            if usd is None:
                skipped += 1  # unknown currency: leave NULL, report it
                continue
            ids.append(row_id)
            values.append(usd)

        updated = conn.execute(
            """
            UPDATE orders AS o SET amount_usd_cents = v.usd
            FROM unnest(%s::bigint[], %s::bigint[]) AS v(id, usd)
            WHERE o.id = v.id AND o.id > %s AND o.id <= %s AND o.amount_usd_cents IS NULL
            """,
            (ids, values, after_id, last_id),
        ).rowcount

        scanned_so_far = rows_scanned + len(rows)
        updated_so_far = rows_updated + updated
        # Progress is set from totals the flow passes in, not incremented, so a
        # chunk that runs twice doesn't count its rows twice.
        conn.execute(
            """
            UPDATE backfill_runs
            SET last_id = %s, chunks_done = %s, rows_scanned = %s, rows_updated = %s, updated_at = now()
            WHERE flow_run_id = %s
            """,
            (last_id, chunk + 1, scanned_so_far, updated_so_far, flow_run_id),
        )

        if fail_chunk == chunk:
            # Fail at most once per run: the first attempt to get here claims it.
            fail = (
                conn.execute(
                    "UPDATE backfill_runs SET failed_once_at = now() WHERE flow_run_id = %s AND failed_once_at IS NULL",
                    (flow_run_id,),
                ).rowcount
                == 1
            )
    # The with block committed the chunk's writes and its progress together.

    if fail:
        # Fail after the chunk's writes committed, like a connection that drops
        # before the result gets back. The step retries, and the retry finds
        # nothing left to write in this chunk.
        raise RuntimeError(f"Simulated failure after chunk {chunk} committed")

    pct = 100 * scanned_so_far / rows_total if rows_total else 100.0
    logger.info(
        "Chunk %d: ids %d-%d, scanned %d, updated %d, skipped %d. Progress %d/%d rows (%.1f%%)",
        chunk,
        rows[0][0],
        last_id,
        len(rows),
        updated,
        skipped,
        scanned_so_far,
        rows_total,
        pct,
    )
    return {
        "chunk": chunk,
        "first_id": rows[0][0],
        "last_id": last_id,
        "scanned": len(rows),
        "updated": updated,
        "skipped": skipped,
    }


def finish_backfill(flow_run_id: str) -> dict:
    """Check what's left and mark the run finished."""
    with connect() as conn:
        (remaining,) = conn.execute(
            "SELECT count(*) FROM orders WHERE amount_usd_cents IS NULL"
        ).fetchone()
        row = conn.execute(
            """
            UPDATE backfill_runs SET finished_at = coalesce(finished_at, now()), updated_at = now()
            WHERE flow_run_id = %s
            RETURNING resumed_from, rows_total, rows_scanned, rows_updated, chunks_done
            """,
            (flow_run_id,),
        ).fetchone()
    resumed_from, rows_total, rows_scanned, rows_updated, chunks_done = row
    return {
        "flow_run_id": flow_run_id,
        "resumed_from": resumed_from,
        "rows_total": rows_total,
        "rows_scanned": rows_scanned,
        "rows_updated": rows_updated,
        "chunks": chunks_done,
        "remaining_nulls": remaining,
    }


# ============================================================================
# The flow
# ============================================================================


@app.flow(timeout_seconds=2 * 60 * 60)
def backfill_orders(
    ctx: FlowContext,
    chunk_size: int = 5000,
    resume_from: str | None = None,
    fail_chunk: int | None = None,
) -> dict:
    """Backfill orders.amount_usd_cents in keyset chunks, one step per chunk.

    resume_from: the flow run ID of an earlier run that didn't finish. This run
    starts after the last chunk that run committed.
    fail_chunk: make that chunk fail once, after its writes commit, to watch
    the step retry. Leave it unset in real runs.
    """
    # Bad input can't be fixed by running again, so it comes back as a result.
    if not MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE:
        return {"error": f"chunk_size must be between {MIN_CHUNK_SIZE} and {MAX_CHUNK_SIZE}"}
    if not os.environ.get("DATABASE_URL"):
        return {"error": "DATABASE_URL is not set"}

    # Flow code must be deterministic: it makes the same ctx.step calls in the
    # same order on every replay. All I/O happens inside steps.
    run_id = ctx.metadata.flow_run_id
    plan = ctx.step("plan", plan_backfill, run_id, chunk_size, resume_from, retry=DB_RETRY)
    if "error" in plan:
        return plan

    rows_total = plan["rows_total"]
    if math.ceil(rows_total / chunk_size) > MAX_CHUNKS:
        return {"error": f"{rows_total} rows need more than {MAX_CHUNKS} chunks; raise chunk_size"}

    chunk = 0
    after_id = plan["start_after_id"]
    rows_scanned = plan["rows_scanned"]
    rows_updated = plan["rows_updated"]
    while True:
        # On a replay, finished chunks return their recorded result here
        # without touching the database.
        result = ctx.step(
            f"chunk-{chunk}",
            backfill_chunk,
            run_id,
            chunk,
            after_id,
            chunk_size,
            rows_total,
            rows_scanned,
            rows_updated,
            fail_chunk,
            retry=CHUNK_RETRY,
        )
        if result["scanned"] == 0:
            break
        chunk += 1
        after_id = result["last_id"]
        rows_scanned += result["scanned"]
        rows_updated += result["updated"]
        if result["scanned"] < chunk_size:
            break  # a short chunk is the last one

    return ctx.step("finish", finish_backfill, run_id, retry=DB_RETRY)


# ============================================================================
# Tasks: set up the demo table and read progress while a backfill runs
# ============================================================================


@app.task(retry=DB_RETRY, timeout_seconds=900)
def seed_orders(ctx: TaskContext, row_count: int = 200_000) -> dict:
    """Recreate the tables and fill orders with row_count rows, amount_usd_cents NULL.

    Drops any existing orders and backfill_runs tables, so a retry starts over
    cleanly. Point DATABASE_URL at a database used only for this example.
    """
    if not 1 <= row_count <= MAX_SEED_ROWS:
        return {"error": f"row_count must be between 1 and {MAX_SEED_ROWS}"}
    if not os.environ.get("DATABASE_URL"):
        return {"error": "DATABASE_URL is not set"}

    with connect() as conn:
        conn.execute("DROP TABLE IF EXISTS orders, backfill_runs")
        conn.execute(SCHEMA)
        conn.execute(
            """
            INSERT INTO orders (amount_minor, currency, created_at)
            SELECT 100 + (g::bigint * 7919) %% 500000,
                   (ARRAY['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY'])[1 + g %% 6],
                   now() - make_interval(mins => g)
            FROM generate_series(1, %s) AS g
            """,
            (row_count,),
        )
        # Refresh planner statistics after the bulk load, so the chunk queries
        # get index plans from the first chunk on.
        conn.execute("ANALYZE orders")
    logger.info("Seeded %d orders", row_count)
    return {"rows": row_count}


@app.task(retry=DB_RETRY, timeout_seconds=60)
def backfill_status(ctx: TaskContext, flow_run_id: str | None = None) -> dict:
    """Progress of a backfill run (the latest one if flow_run_id is omitted)."""
    if not os.environ.get("DATABASE_URL"):
        return {"error": "DATABASE_URL is not set"}

    with connect() as conn:
        conn.row_factory = dict_row
        run = conn.execute(
            """
            SELECT flow_run_id, resumed_from, chunk_size, rows_total, rows_scanned, rows_updated,
                   chunks_done, last_id, failed_once_at, started_at, updated_at, finished_at
            FROM backfill_runs
            WHERE flow_run_id = coalesce(%s, flow_run_id)
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (flow_run_id,),
        ).fetchone()
        remaining = conn.execute(
            "SELECT count(*) AS n FROM orders WHERE amount_usd_cents IS NULL"
        ).fetchone()["n"]

    if run is None:
        return {"error": "no backfill run found", "remaining_nulls": remaining}
    total = run["rows_total"]
    return {
        **{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in run.items()},
        "percent": round(100 * run["rows_scanned"] / total, 1) if total else 100.0,
        "remaining_nulls": remaining,
    }


if __name__ == "__main__":
    app.start()
