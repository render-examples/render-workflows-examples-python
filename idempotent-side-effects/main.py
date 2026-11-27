"""
Idempotent side effects on Render Workflows

A refund flow for support tickets. An LLM reads the ticket and drafts a
decision, then the workflow issues a refund credit. The LLM call can be retried
as often as needed. The credit must happen once.

Render retries a task when an attempt fails, and a retry runs the task again
from the top. If an attempt fails after the credit was written, a naive retry
writes it again. This example keeps that from happening in two ways:

1. The LLM call and the credit are separate tasks. A retry of one never reruns
   the other.
2. The credit is written with an idempotency key derived from the business
   input (account and order), with INSERT ... ON CONFLICT DO NOTHING. A retry
   finds the credit the failed attempt wrote and doesn't write another.

Pass fail_after_credit=true to handle_refund to fail issue_credit once, right
after the credit is written, and watch the retry skip it.
"""

import hashlib
import json
import logging
import os
import pathlib

import psycopg
from openai import APIStatusError, AsyncOpenAI
from psycopg.rows import dict_row
from pydantic import BaseModel
from render import Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Read the model from the environment so a retired model is a config change,
# not a code change.
MODEL = os.getenv("OPENAI_MODEL", "gpt-6.2-sol")

# OpenAI errors that mean the request itself is wrong: unknown model, bad key,
# invalid input. A retry can't fix them, so draft_refund returns them as a
# result instead of raising.
PERMANENT_ERROR_STATUSES = {400, 401, 403, 404, 422}

SCHEMA = (pathlib.Path(__file__).parent / "schema.sql").read_text()

app = Workflows()


# ============================================================================
# Sample orders. In production, read these from your database. The credit
# amount comes from here, not from the model.
# ============================================================================

ORDERS = {
    "ORD-2001": {"status": "delivered", "amount_cents": 4900, "times_charged": 2},
    "ORD-2002": {"status": "processing", "amount_cents": 12000, "times_charged": 1},
    "ORD-2003": {"status": "shipped", "amount_cents": 1550, "times_charged": 1},
}


class RefundDraft(BaseModel):
    """What the model drafts for a ticket."""

    refund: bool
    reason: str
    customer_message: str


# ============================================================================
# Database
# ============================================================================

_schema_applied = False


async def connect() -> psycopg.AsyncConnection:
    """Open a connection and create the table on first use in this process."""
    global _schema_applied
    # autocommit makes each statement durable as soon as it runs, so a credit
    # written before a failure stays written, like a real payment would.
    conn = await psycopg.AsyncConnection.connect(
        os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row
    )
    if not _schema_applied:
        await conn.execute(SCHEMA)
        _schema_applied = True
    return conn


def credit_key(account_id: str, order_id: str) -> str:
    """The idempotency key for a refund credit.

    Built from the business input, never from the run ID or attempt: every
    attempt and every run for the same account and order gets the same key.
    One credit per account and order is the business rule here. If an order
    can get several credits, add what tells them apart (a charge ID, say).
    """
    return hashlib.sha256(f"{account_id}:refund:{order_id}".encode()).hexdigest()


# ============================================================================
# Tasks
# ============================================================================


# draft_refund calls the OpenAI API, so it retries if an attempt fails. It
# only reads, so a retry costs another model call and nothing else.
@app.task(
    retry=Retry(max_retries=2, wait_duration_ms=1000, backoff_scaling=2),
    timeout_seconds=60,
)
async def draft_refund(ctx: TaskContext, ticket: dict, order: dict) -> dict:
    """Ask the model whether the ticket warrants a refund credit, and draft the reply."""
    ticket_id = ticket.get("id", "unknown")
    logger.info(f"[DRAFT] {ticket_id}: {ticket.get('subject')}")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # A setup problem a retry can't fix, so return it instead of raising.
        return {"ticket_id": ticket_id, "error": "OPENAI_API_KEY is not set"}

    # Render retries this task, so turn off the OpenAI client's own retries to
    # keep one retry policy in charge.
    client = AsyncOpenAI(api_key=api_key, max_retries=0)

    try:
        response = await client.responses.parse(
            model=MODEL,
            instructions=(
                "You review support tickets for a refund credit. Approve one only "
                "when the order data shows the customer was charged more than once, "
                "or the order can't be delivered. Write a short customer_message "
                "that tells the customer what happens next. Don't state an amount."
            ),
            input=(
                f"Ticket {ticket_id} from account {ticket.get('account_id')}\n"
                f"Subject: {ticket.get('subject')}\n\n{ticket.get('body', '')}\n\n"
                f"Order {ticket.get('order_id')}: {json.dumps(order)}"
            ),
            text_format=RefundDraft,
        )
    except APIStatusError as e:
        if e.status_code in PERMANENT_ERROR_STATUSES:
            logger.error(f"[DRAFT] {ticket_id}: OpenAI error {e.status_code}, not retrying")
            return {"ticket_id": ticket_id, "error": f"OpenAI error {e.status_code}: {e.message}"}
        raise

    draft = response.output_parsed
    if draft is None:
        # No parsed output, for example a refusal. Raise so Render retries.
        raise RuntimeError(f"{ticket_id}: the model returned no draft")

    logger.info(f"[DRAFT] {ticket_id}: refund={draft.refund}")
    return {"ticket_id": ticket_id, **draft.model_dump()}


# issue_credit is the side effect. It retries, which is safe only because the
# write is idempotent: every attempt uses the same key, and the insert does
# nothing if a credit with that key exists. The other safe choice is no
# retries at all (max_retries=0), which means a failed credit stays failed
# until someone looks at it.
@app.task(
    retry=Retry(max_retries=2, wait_duration_ms=1000, backoff_scaling=2),
    timeout_seconds=30,
)
async def issue_credit(
    ctx: TaskContext,
    account_id: str,
    order_id: str,
    amount_cents: int,
    customer_message: str,
    fail_after_credit: bool = False,
) -> dict:
    """Issue a refund credit once per account and order."""
    if not os.getenv("DATABASE_URL"):
        return {"order_id": order_id, "error": "DATABASE_URL is not set"}

    key = credit_key(account_id, order_id)
    run_id = ctx.metadata.task_run_id

    async with await connect() as conn:
        row = await (
            await conn.execute(
                """
                INSERT INTO refund_credits
                    (idempotency_key, account_id, order_id, amount_cents, customer_message, task_run_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING idempotency_key
                """,
                (key, account_id, order_id, amount_cents, customer_message, run_id),
            )
        ).fetchone()
        issued = row is not None
        logger.info(
            f"[CREDIT] run {run_id}: {'issued' if issued else 'already issued, skipped'} "
            f"credit for {order_id} (key {key[:12]})"
        )

        # The demo switch. Fail right after the credit is written, but only on
        # the attempt that wrote it, so the retry can succeed.
        if fail_after_credit and issued:
            logger.warning(f"[CREDIT] run {run_id}: failing on purpose after the credit")
            raise RuntimeError("Failed on purpose after issuing the credit (fail_after_credit)")

        # If the credit goes through a payment provider instead, pass the same
        # key to the provider. Stripe, for example, reads it from the
        # Idempotency-Key header and replays the first response for a repeated
        # key. With stripe-python:
        #   stripe_client.v1.refunds.create(
        #       params={"payment_intent": ..., "amount": amount_cents},
        #       options={"idempotency_key": key},
        #   )

        count = await (
            await conn.execute(
                "SELECT count(*) AS n FROM refund_credits WHERE account_id = %s AND order_id = %s",
                (account_id, order_id),
            )
        ).fetchone()

    return {
        "order_id": order_id,
        "idempotency_key": key,
        "status": "issued" if issued else "already_issued",
        "amount_cents": amount_cents,
        "credits_for_order": count["n"],
    }


# handle_refund only orchestrates, so it doesn't retry: a retry would rerun the
# model call and the credit. Each subtask retries on its own.
@app.task(
    retry=Retry(max_retries=0, wait_duration_ms=1000, backoff_scaling=1.5),
    timeout_seconds=300,
)
async def handle_refund(ctx: TaskContext, ticket: dict, fail_after_credit: bool = False) -> dict:
    """Draft a refund decision with the model, then issue the credit if it's approved."""
    ticket_id = ticket.get("id", "unknown")
    order = ORDERS.get(ticket.get("order_id") or "")
    if order is None:
        # Bad input. A retry can't fix it.
        return {"ticket_id": ticket_id, "error": f"No known order on ticket {ticket_id}"}

    # ctx.run starts each step as its own task run with its own retries.
    draft = await ctx.run(draft_refund, ticket, order)
    if "error" in draft:
        return {"ticket_id": ticket_id, "error": draft["error"]}
    if not draft["refund"]:
        return {"ticket_id": ticket_id, "decision": "no_refund", "draft": draft}

    credit = await ctx.run(
        issue_credit,
        ticket["account_id"],
        ticket["order_id"],
        order["amount_cents"],
        draft["customer_message"],
        fail_after_credit,
    )
    if "error" in credit:
        return {"ticket_id": ticket_id, "error": credit["error"]}

    return {"ticket_id": ticket_id, "decision": "refund", "draft": draft, "credit": credit}


if __name__ == "__main__":
    app.start()
