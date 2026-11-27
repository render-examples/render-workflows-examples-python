"""
AI chat turns: the workflow service

Each chat turn runs as one task run. The API service stores the user's message
and starts run_turn with the message's ID. run_turn loads the conversation from
Postgres, runs a support agent built with the OpenAI Agents SDK, and writes the
reply back to Postgres.
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass

from agents import Agent, RunContextWrapper, Runner, set_default_openai_client
from agents.decorators import tool
from agents.exceptions import MaxTurnsExceeded
from openai import APIStatusError, AsyncOpenAI
from render import Retry, TaskContext, Workflows

import db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Read the model from the environment so a retired model is a config change,
# not a code change.
MODEL = os.getenv("OPENAI_MODEL", "gpt-6.2-sol")
MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "8"))

# OpenAI errors that mean the request itself is wrong. A retry can't fix
# them, so run_turn returns them as a result instead of raising.
PERMANENT_ERROR_STATUSES = {400, 401, 403, 404, 422}

app = Workflows()


# ============================================================================
# Sample data the read-only tools use. In production, query your database.
# ============================================================================

ORDERS = {
    "ORD-2001": {"status": "delivered", "amount": 49.00, "times_charged": 2},
    "ORD-2002": {"status": "processing", "amount": 120.00, "times_charged": 1},
    "ORD-2003": {"status": "shipped", "amount": 15.50, "times_charged": 1},
}

ACCOUNTS = {
    "ACC-100": {"plan": "pro", "standing": "good", "balance_due": 0.00},
    "ACC-200": {"plan": "starter", "standing": "good", "balance_due": 0.00},
    "ACC-300": {"plan": "pro", "standing": "past_due", "balance_due": 79.00},
}


@dataclass
class TurnContext:
    """What the tools need to know about the turn they run in."""

    conversation_id: str
    message_id: int


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order's status, amount, and how many times it was charged.

    Args:
        order_id: The order ID, such as ORD-2001.
    """
    return json.dumps(ORDERS.get(order_id) or {"error": f"No order {order_id}"})


@tool
def get_account_status(account_id: str) -> str:
    """Look up an account's plan, standing, and balance due.

    Args:
        account_id: The account ID, such as ACC-100.
    """
    return json.dumps(ACCOUNTS.get(account_id) or {"error": f"No account {account_id}"})


@tool
async def open_refund_request(
    wrapper: RunContextWrapper[TurnContext], order_id: str, reason: str
) -> str:
    """Open a refund request for an order. If one is already open for the order
    in this conversation, this reports it instead of opening another.

    Args:
        order_id: The order ID to refund, such as ORD-2001.
        reason: Why the customer should get a refund.
    """
    turn = wrapper.context
    # Derive the key from the conversation and the order, not the reason text:
    # a retried turn may phrase the reason differently, and a later message
    # ("is my refund open?") may call the tool again. Both find the request
    # that's already open.
    key = hashlib.sha256(f"{turn.conversation_id}:refund:{order_id}".encode()).hexdigest()
    async with await db.connect() as conn:
        row = await (
            await conn.execute(
                """
                INSERT INTO refund_requests
                    (idempotency_key, conversation_id, message_id, order_id, reason)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING idempotency_key
                """,
                (key, turn.conversation_id, turn.message_id, order_id, reason),
            )
        ).fetchone()
    return json.dumps({"order_id": order_id, "refund_request": "opened" if row else "already open"})


support_agent = Agent[TurnContext](
    name="Support",
    instructions=(
        "You are a customer support agent in a chat. Use the tools to check orders "
        "and accounts before you answer. Open a refund request only when the customer "
        "was charged in error or an order can't be delivered. Keep replies short."
    ),
    tools=[lookup_order, get_account_status, open_refund_request],
    model=MODEL,
)


# ============================================================================
# Task
# ============================================================================


# run_turn calls the OpenAI API, so it retries once if an attempt fails. A
# retry reruns the agent, which is safe: the reply insert and the refund tool
# are both idempotent.
@app.task(
    retry=Retry(max_retries=1, wait_duration_ms=1000, backoff_scaling=2),
    timeout_seconds=120,
)
async def run_turn(ctx: TaskContext, conversation_id: str, message_id: int) -> dict:
    """Answer one user message and store the reply."""
    logger.info(f"[TURN] conversation {conversation_id}, message {message_id}")

    async with await db.connect() as conn:
        # A previous attempt may have stored the reply before failing to report
        # it. Return that reply instead of answering twice.
        existing = await (
            await conn.execute(
                "SELECT id, content FROM messages WHERE reply_to = %s", (message_id,)
            )
        ).fetchone()
        if existing:
            return {"message_id": message_id, "reply_id": existing["id"], "reply": existing["content"]}

        history = await (
            await conn.execute(
                """
                SELECT role, content FROM messages
                WHERE conversation_id = %s AND id <= %s
                  AND (role = 'assistant' OR status = 'done' OR id = %s)
                ORDER BY id
                """,
                (conversation_id, message_id, message_id),
            )
        ).fetchall()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"message_id": message_id, "error": "OPENAI_API_KEY is not set"}

    # Render retries this task, so turn off the OpenAI client's own retries.
    set_default_openai_client(AsyncOpenAI(api_key=api_key, max_retries=0))

    try:
        result = await Runner.run(
            support_agent,
            [{"role": m["role"], "content": m["content"]} for m in history],
            context=TurnContext(conversation_id=conversation_id, message_id=message_id),
            max_turns=MAX_TURNS,
        )
    except MaxTurnsExceeded:
        return {"message_id": message_id, "error": f"Hit the limit of {MAX_TURNS} agent turns"}
    except APIStatusError as e:
        if e.status_code in PERMANENT_ERROR_STATUSES:
            return {"message_id": message_id, "error": f"OpenAI error {e.status_code}: {e.message}"}
        raise

    reply = str(result.final_output)
    async with await db.connect() as conn:
        # The unique index on reply_to makes this a no-op if a reply exists.
        row = await (
            await conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content, reply_to)
                VALUES (%s, 'assistant', %s, %s)
                ON CONFLICT (reply_to) WHERE reply_to IS NOT NULL DO NOTHING
                RETURNING id
                """,
                (conversation_id, reply, message_id),
            )
        ).fetchone()
        if row is None:
            row = await (
                await conn.execute("SELECT id, content FROM messages WHERE reply_to = %s", (message_id,))
            ).fetchone()
            reply = row["content"]

    logger.info(f"[TURN] message {message_id} answered by reply {row['id']}")
    return {"message_id": message_id, "reply_id": row["id"], "reply": reply}


if __name__ == "__main__":
    app.start()
