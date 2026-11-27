"""
Webhook processing: the workflow service

Two tasks:

- process_delivery applies one stored GitHub delivery to the repo_activity
  table. It retries on failure, and it applies each delivery at most once: the
  counters and the "processed" mark commit in one transaction.
- replay_pending finds deliveries that were stored but never processed (the
  task server was down, or every attempt failed) and processes them again.

The receiver (receiver.py) stores each delivery and starts process_delivery.
"""

import asyncio
import logging
import os

from psycopg.types.json import Jsonb
from render import Retry, TaskContext, Workflows

import db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = Workflows()

_schema_applied = False


async def connect():
    """Open a connection, and create the tables on first use in this process."""
    global _schema_applied
    if not _schema_applied:
        await db.apply_schema()
        _schema_applied = True
    return await db.connect()


async def apply_event(conn, event: str, payload: dict) -> dict:
    """Add one event to its repository's counters. Returns what changed."""
    repo = (payload.get("repository") or {}).get("full_name")
    if not repo:
        return {"event": event, "counted": False, "note": "No repository in the payload"}

    if event == "push":
        commits = len(payload.get("commits", []))
        await conn.execute(
            """
            INSERT INTO repo_activity (repo, pushes, commits) VALUES (%s, 1, %s)
            ON CONFLICT (repo) DO UPDATE SET
              pushes = repo_activity.pushes + 1,
              commits = repo_activity.commits + EXCLUDED.commits,
              updated_at = now()
            """,
            (repo, commits),
        )
        return {"repo": repo, "event": "push", "ref": payload.get("ref"), "commits": commits}

    if event == "issues" and payload.get("action") in ("opened", "closed"):
        column = "issues_opened" if payload["action"] == "opened" else "issues_closed"
        await conn.execute(
            f"""
            INSERT INTO repo_activity (repo, {column}) VALUES (%s, 1)
            ON CONFLICT (repo) DO UPDATE SET
              {column} = repo_activity.{column} + 1,
              updated_at = now()
            """,
            (repo,),
        )
        return {"repo": repo, "event": "issues", "action": payload["action"], "counted": True}

    # Other issue actions (edited, labeled, ...) change no counter.
    return {"repo": repo, "event": event, "action": payload.get("action"), "counted": False}


def fail_on_purpose(event: str, payload: dict, attempts: int) -> bool:
    """Demo switch: fail the first attempt for an issue titled "[fail-once] ..."."""
    title = (payload.get("issue") or {}).get("title", "")
    return event == "issues" and "[fail-once]" in title and attempts == 1


# process_delivery may retry: its writes commit together with the "processed"
# mark, so an attempt that fails leaves nothing behind and the next attempt
# starts clean. Each attempt gets its own 60 seconds.
@app.task(
    retry=Retry(max_retries=3, wait_duration_ms=2000, backoff_scaling=2),
    timeout_seconds=60,
)
async def process_delivery(ctx: TaskContext, delivery_id: str) -> dict:
    """Apply one stored webhook delivery, at most once."""
    # A retry can't fix a missing setting, so return it instead of raising.
    if not os.getenv("DATABASE_URL"):
        return {"delivery_id": delivery_id, "error": "DATABASE_URL is not set"}

    # Connection errors raise, so Render retries the attempt.
    async with await connect() as conn:
        # Count the attempt on its own, so the count survives a failed attempt.
        cur = await conn.execute(
            "UPDATE webhook_deliveries SET attempts = attempts + 1"
            " WHERE delivery_id = %s RETURNING attempts",
            (delivery_id,),
        )
        counted = await cur.fetchone()
        if counted is None:
            return {"delivery_id": delivery_id, "error": "No such delivery"}
        attempts = counted["attempts"]

        try:
            async with conn.transaction():
                # FOR UPDATE: if two runs process the same delivery at once,
                # the second waits here and then sees it's already processed.
                cur = await conn.execute(
                    "SELECT event, payload, status FROM webhook_deliveries"
                    " WHERE delivery_id = %s FOR UPDATE",
                    (delivery_id,),
                )
                delivery = await cur.fetchone()
                if delivery["status"] == "processed":
                    logger.info(f"[PROCESS] {delivery_id} already processed")
                    return {"delivery_id": delivery_id, "status": "duplicate"}

                summary = await apply_event(conn, delivery["event"], delivery["payload"])

                # Stands in for a downstream call that fails for a moment.
                # Raising here rolls back the counters above, so the retry
                # doesn't count the event twice.
                if fail_on_purpose(delivery["event"], delivery["payload"], attempts):
                    raise RuntimeError("Failed on purpose on the first attempt ([fail-once])")

                await conn.execute(
                    """
                    UPDATE webhook_deliveries
                    SET status = 'processed', processed_at = now(), result = %s, last_error = NULL
                    WHERE delivery_id = %s
                    """,
                    (Jsonb(summary), delivery_id),
                )
        except Exception as e:
            # Keep the error next to the delivery, then raise so Render retries.
            await conn.execute(
                "UPDATE webhook_deliveries SET last_error = %s WHERE delivery_id = %s",
                (str(e)[:500], delivery_id),
            )
            raise

    logger.info(f"[PROCESS] {delivery_id} processed on attempt {attempts}: {summary}")
    return {"delivery_id": delivery_id, "status": "processed", "attempts": attempts, **summary}


# replay_pending only starts subtasks, so it doesn't retry: a retry would start
# every subtask again. Each process_delivery retries on its own.
@app.task(
    retry=Retry(max_retries=0, wait_duration_ms=1000, backoff_scaling=1.5),
    timeout_seconds=900,
)
async def replay_pending(ctx: TaskContext, older_than_minutes: int = 10, limit: int = 100) -> dict:
    """Process again every delivery that was stored but never processed."""
    if not os.getenv("DATABASE_URL"):
        return {"error": "DATABASE_URL is not set"}

    # Skip recent deliveries: their first run may still be going.
    async with await connect() as conn:
        cur = await conn.execute(
            """
            SELECT delivery_id FROM webhook_deliveries
            WHERE status = 'pending' AND received_at < now() - make_interval(mins => %s)
            ORDER BY received_at
            LIMIT %s
            """,
            (older_than_minutes, limit),
        )
        delivery_ids = [row["delivery_id"] for row in await cur.fetchall()]

    logger.info(f"[REPLAY] {len(delivery_ids)} pending deliveries")
    # ctx.run starts each delivery as its own task run, with its own retries.
    outcomes = await asyncio.gather(
        *(ctx.run(process_delivery, delivery_id) for delivery_id in delivery_ids),
        return_exceptions=True,
    )

    processed, failed = [], []
    for delivery_id, outcome in zip(delivery_ids, outcomes):
        if isinstance(outcome, BaseException) or "error" in outcome:
            error = str(outcome) if isinstance(outcome, BaseException) else outcome["error"]
            failed.append({"delivery_id": delivery_id, "error": error})
        else:
            processed.append({"delivery_id": delivery_id, "status": outcome["status"]})
    return {"pending": len(delivery_ids), "processed": processed, "failed": failed}


if __name__ == "__main__":
    app.start()
