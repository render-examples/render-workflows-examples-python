"""
AI chat turns: the API service

A FastAPI app that stores chat messages in Postgres and runs each turn as a
workflow task:

1. POST a message. The API stores it, claims the conversation so only one turn
   runs at a time, starts run_turn, and returns 202.
2. run_turn (in the workflow service) writes the reply to Postgres.
3. The API waits for the run to finish, then marks the message done or failed
   and releases the claim.
4. GET the conversation to read the reply.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from render import RenderAsync
from render.client.errors import ClientError

import db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

SLUG = os.getenv("WORKFLOW_SLUG", "ai-chat-turns")
TERMINAL = {"completed", "failed", "canceled"}

render: RenderAsync | None = None
watchers: set[asyncio.Task] = set()


class NewMessage(BaseModel):
    client_message_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=8000)


# ============================================================================
# Turn lifecycle
# ============================================================================


async def start_turn(conversation_id: str, message_id: int, client_message_id: str) -> str:
    """Start run_turn for a stored message and watch it until it finishes."""
    # The idempotency key comes from the message, so starting the same turn
    # twice within 24 hours returns the first run instead of a second one.
    run = await render.workflows.start_task(
        f"{SLUG}/run_turn",
        [conversation_id, message_id],
        idempotency_key=f"turn:{conversation_id}:{client_message_id}",
    )
    async with await db.connect() as conn:
        await conn.execute(
            "UPDATE messages SET status = 'running', task_run_id = %s WHERE id = %s",
            (run.id, message_id),
        )
    watch(conversation_id, message_id, run.id)
    return run.id


def watch(conversation_id: str, message_id: int, task_run_id: str) -> None:
    task = asyncio.create_task(finish_when_done(conversation_id, message_id, task_run_id))
    watchers.add(task)
    task.add_done_callback(watchers.discard)


async def finish_when_done(conversation_id: str, message_id: int, task_run_id: str) -> None:
    # The events stream sends one event per run, when it reaches a terminal
    # status. It doesn't stream partial output.
    try:
        async for details in render.workflows.task_run_events([task_run_id]):
            if details.id == task_run_id:
                await finish(conversation_id, message_id, details)
                return
    except Exception as e:
        logger.warning(f"[API] Lost the event stream for {task_run_id} ({e}); polling instead")
    while True:
        try:
            details = await render.workflows.get_task_run(task_run_id)
        except ClientError as e:
            if e.status_code == 404:
                await settle_lost_run(conversation_id, message_id, task_run_id)
                return
            logger.warning(f"[API] Couldn't check {task_run_id} ({e}); retrying")
            await asyncio.sleep(5)
            continue
        except Exception as e:
            logger.warning(f"[API] Couldn't check {task_run_id} ({e}); retrying")
            await asyncio.sleep(5)
            continue
        if details.status in TERMINAL:
            await finish(conversation_id, message_id, details)
            return
        await asyncio.sleep(2)


async def finish(conversation_id: str, message_id: int, details) -> None:
    """Record how a run ended."""
    result = details.results[0] if details.results else None
    if details.status == "completed" and isinstance(result, dict) and "error" not in result:
        await record(conversation_id, message_id, "done")
    elif isinstance(result, dict) and "error" in result:
        await record(conversation_id, message_id, "failed", result["error"])
    else:
        await record(
            conversation_id, message_id, "failed", f"Run {details.status}: {details.error or 'no details'}"
        )


async def reply_exists(message_id: int) -> bool:
    async with await db.connect() as conn:
        row = await (
            await conn.execute("SELECT 1 FROM messages WHERE reply_to = %s", (message_id,))
        ).fetchone()
    return row is not None


async def settle_lost_run(conversation_id: str, message_id: int, task_run_id: str) -> None:
    """Render has no record of the run, so go by what's in the database."""
    if await reply_exists(message_id):
        await record(conversation_id, message_id, "done")
    else:
        await record(conversation_id, message_id, "failed", f"Run {task_run_id} not found")


async def record(conversation_id: str, message_id: int, status: str, error: str | None = None) -> None:
    """Mark the message done or failed and release the conversation."""
    logger.info(f"[API] Message {message_id}: {status}")
    async with await db.connect() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE messages SET status = %s, error = %s WHERE id = %s",
                (status, error, message_id),
            )
            await conn.execute(
                "UPDATE conversations SET active_message_id = NULL "
                "WHERE id = %s AND active_message_id = %s",
                (conversation_id, message_id),
            )


async def abandon(conversation_id: str, message_id: int) -> None:
    """Undo a message whose turn never started, so the client can resend it."""
    async with await db.connect() as conn:
        async with conn.transaction():
            # Only this request's own row, and never one that has a reply.
            await conn.execute(
                "DELETE FROM messages WHERE id = %s "
                "AND NOT EXISTS (SELECT 1 FROM messages WHERE reply_to = %s)",
                (message_id, message_id),
            )
            await conn.execute(
                "UPDATE conversations SET active_message_id = NULL "
                "WHERE id = %s AND active_message_id = %s",
                (conversation_id, message_id),
            )


async def reconcile() -> None:
    """Pick up turns that were running when this process last stopped.

    A deploy or restart stops this process mid-turn. The turn itself keeps
    running on Render; this finds it and finishes the bookkeeping.
    """
    async with await db.connect() as conn:
        rows = await (
            await conn.execute(
                """
                SELECT c.id AS conversation_id, m.id AS message_id,
                       m.task_run_id, m.client_message_id
                FROM conversations c JOIN messages m ON m.id = c.active_message_id
                """
            )
        ).fetchall()
    for row in rows:
        conversation_id, message_id = row["conversation_id"], row["message_id"]
        if await reply_exists(message_id):
            # The turn finished, but this process stopped before recording it.
            await record(conversation_id, message_id, "done")
        elif row["task_run_id"] is None:
            # Stopped before the start went through, or before it was saved.
            # Starting again is safe: the idempotency key returns the first run.
            await start_turn(conversation_id, message_id, row["client_message_id"])
        else:
            watch(conversation_id, message_id, row["task_run_id"])
    if rows:
        logger.info(f"[API] Reconciled {len(rows)} running turn(s)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global render
    await db.apply_schema()
    render = RenderAsync()  # reads RENDER_API_KEY (or RENDER_USE_LOCAL_DEV)
    await reconcile()
    yield
    for task in watchers:
        task.cancel()


app = FastAPI(title="AI chat turns", lifespan=lifespan)


# ============================================================================
# Routes
# ============================================================================


@app.post("/conversations/{conversation_id}/messages", status_code=202)
async def post_message(conversation_id: str, body: NewMessage, response: Response):
    async with await db.connect() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO conversations (id) VALUES (%s) ON CONFLICT DO NOTHING",
                (conversation_id,),
            )
            row = await (
                await conn.execute(
                    """
                    INSERT INTO messages (conversation_id, role, content, client_message_id, status)
                    VALUES (%s, 'user', %s, %s, 'pending')
                    ON CONFLICT (conversation_id, client_message_id) DO NOTHING
                    RETURNING id
                    """,
                    (conversation_id, body.content, body.client_message_id),
                )
            ).fetchone()

            if row is None:
                # The client resent a message we already have (a retry after a
                # timeout, say). Report it instead of starting a second turn.
                existing = await (
                    await conn.execute(
                        "SELECT id, status, task_run_id FROM messages "
                        "WHERE conversation_id = %s AND client_message_id = %s",
                        (conversation_id, body.client_message_id),
                    )
                ).fetchone()
                response.status_code = 200
                return {
                    "message_id": existing["id"],
                    "status": existing["status"],
                    "task_run_id": existing["task_run_id"],
                    "duplicate": True,
                }

            message_id = row["id"]
            claimed = await (
                await conn.execute(
                    "UPDATE conversations SET active_message_id = %s "
                    "WHERE id = %s AND active_message_id IS NULL RETURNING id",
                    (message_id, conversation_id),
                )
            ).fetchone()
            if claimed is None:
                # Another turn is running. Raising rolls back this transaction,
                # which removes only the message this request inserted.
                raise HTTPException(
                    status_code=409,
                    detail="A turn is already running in this conversation. Send again when it finishes.",
                )

    try:
        task_run_id = await start_turn(conversation_id, message_id, body.client_message_id)
    except Exception:
        logger.exception(f"[API] Couldn't start the turn for message {message_id}")
        await abandon(conversation_id, message_id)
        raise HTTPException(status_code=503, detail="Couldn't start the turn. Send the message again.")

    return {"message_id": message_id, "status": "running", "task_run_id": task_run_id}


@app.get("/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str):
    async with await db.connect() as conn:
        conversation = await (
            await conn.execute(
                "SELECT active_message_id FROM conversations WHERE id = %s", (conversation_id,)
            )
        ).fetchone()
        if conversation is None:
            raise HTTPException(status_code=404, detail="No such conversation")
        messages = await (
            await conn.execute(
                "SELECT id, role, content, status, error, reply_to, created_at "
                "FROM messages WHERE conversation_id = %s ORDER BY id",
                (conversation_id,),
            )
        ).fetchall()
    return {
        "conversation_id": conversation_id,
        "turn_running": conversation["active_message_id"] is not None,
        "messages": messages,
    }


@app.get("/healthz")
async def healthz():
    return {"ok": True}
