"""
OpenAI Agents SDK on Render Workflows

A support-ticket triage agent built with the OpenAI Agents SDK, running inside
a workflow task. The SDK runs the agent loop (model calls, tool calls and
handoffs) within one task run. Render runs that task, retries it, and fans it
out across many tickets.

For an agent loop where every model call and tool call is its own task, see
the openai-agent example.
"""

import asyncio
import json
import logging
import os
from collections import Counter
from typing import Literal

from agents import Agent, Runner, set_default_openai_client
from agents.decorators import tool
from agents.exceptions import MaxTurnsExceeded
from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel
from render import Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Read the model from the environment so a retired model is a config change,
# not a code change.
MODEL = os.getenv("OPENAI_MODEL", "gpt-6.2-sol")

# Upper bound on model turns per ticket, so an agent that keeps calling tools
# or handing off can't run forever.
MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "6"))

# OpenAI errors that mean the request itself is wrong: unknown model, bad key,
# invalid input. A retry can't fix them, so triage_ticket returns them as a
# result instead of raising.
PERMANENT_ERROR_STATUSES = {400, 401, 403, 404, 422}

app = Workflows()


# ============================================================================
# Sample data the tools read. In production these would query your database.
# ============================================================================

ORDERS = {
    "ORD-2001": {"status": "delivered", "amount": 49.00, "times_charged": 2},
    "ORD-2002": {"status": "processing", "amount": 120.00, "times_charged": 1},
    "ORD-2003": {"status": "shipped", "amount": 15.50, "times_charged": 1},
}

ACCOUNTS = {
    "ACC-100": {"plan": "pro", "standing": "good", "balance_due": 0.00},
    "ACC-200": {"plan": "starter", "standing": "good", "balance_due": 0.00},
    "ACC-300": {
        "plan": "pro",
        "standing": "past_due",
        "balance_due": 79.00,
        "card_on_file": "expired",
    },
}


# ============================================================================
# Tools. Both only read data, so rerunning the agent on a retry is safe.
# ============================================================================


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order's status, amount, and how many times it was charged.

    Args:
        order_id: The order ID, such as ORD-2001.
    """
    order = ORDERS.get(order_id)
    return json.dumps(order if order else {"error": f"No order {order_id}"})


@tool
def get_account_status(account_id: str) -> str:
    """Look up an account's plan, standing, and balance due.

    Args:
        account_id: The account ID, such as ACC-100.
    """
    account = ACCOUNTS.get(account_id)
    return json.dumps(account if account else {"error": f"No account {account_id}"})


# ============================================================================
# Agents
# ============================================================================


class Resolution(BaseModel):
    """The structured result a specialist returns for a ticket."""

    category: Literal["billing", "technical"]
    priority: Literal["low", "normal", "high", "urgent"]
    summary: str
    next_steps: list[str]
    customer_reply: str


billing_agent = Agent(
    name="Billing",
    handoff_description="Charges, refunds, invoices, payment methods, and account balances.",
    instructions=(
        "You resolve billing support tickets. Check the order or account with the "
        "tools before you answer. Set category to billing."
    ),
    tools=[lookup_order, get_account_status],
    output_type=Resolution,
    model=MODEL,
)

technical_agent = Agent(
    name="Technical Support",
    handoff_description="Sign-in problems, API errors, outages, and broken links.",
    instructions=(
        "You resolve technical support tickets. Use the tools if the account or "
        "order matters to the problem. Set category to technical."
    ),
    tools=[lookup_order, get_account_status],
    output_type=Resolution,
    model=MODEL,
)

triage_agent = Agent(
    name="Triage",
    instructions=(
        "Read the support ticket and hand it off to the right specialist. If the "
        "right team isn't clear, check the order or account with the tools first. "
        "Always hand off. Don't answer the customer yourself."
    ),
    tools=[lookup_order, get_account_status],
    handoffs=[billing_agent, technical_agent],
    model=MODEL,
)


def format_ticket(ticket: dict) -> str:
    """Render a ticket as the agent's input message."""
    return "\n".join(
        [
            f"Ticket: {ticket.get('id')}",
            f"Account: {ticket.get('account_id')}",
            f"Order: {ticket.get('order_id') or 'none'}",
            f"Subject: {ticket.get('subject')}",
            "",
            str(ticket.get("body", "")),
        ]
    )


# ============================================================================
# Tasks
# ============================================================================


# triage_ticket calls the OpenAI API, so it retries once if an attempt fails.
# A retry reruns the whole agent. That's safe here because the tools only read.
@app.task(
    retry=Retry(max_retries=1, wait_duration_ms=1000, backoff_scaling=2),
    timeout_seconds=120,
)
async def triage_ticket(ctx: TaskContext, ticket: dict) -> dict:
    """Triage one support ticket and return the specialist's structured result."""
    ticket_id = ticket.get("id", "unknown")
    logger.info(f"[TRIAGE] {ticket_id}: {ticket.get('subject')}")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # A setup problem a retry can't fix, so return it instead of raising.
        return {"ticket_id": ticket_id, "error": "OPENAI_API_KEY is not set"}

    # Render retries this task, so turn off the OpenAI client's own retries to
    # keep one retry policy in charge.
    set_default_openai_client(AsyncOpenAI(api_key=api_key, max_retries=0))

    try:
        result = await Runner.run(
            triage_agent, format_ticket(ticket), max_turns=MAX_TURNS
        )
    except MaxTurnsExceeded:
        logger.warning(f"[TRIAGE] {ticket_id}: stopped after {MAX_TURNS} turns")
        return {"ticket_id": ticket_id, "error": f"Hit the limit of {MAX_TURNS} agent turns"}
    except APIStatusError as e:
        if e.status_code in PERMANENT_ERROR_STATUSES:
            logger.error(f"[TRIAGE] {ticket_id}: OpenAI error {e.status_code}, not retrying")
            return {
                "ticket_id": ticket_id,
                "error": f"OpenAI error {e.status_code}: {e.message}",
            }
        raise

    # The SDK's run result isn't JSON-serializable, and task results must be.
    # Return the agent that finished the run and its output as plain data.
    output = result.final_output
    handled_by = result.last_agent.name
    logger.info(f"[TRIAGE] {ticket_id}: handled by {handled_by}")
    return {
        "ticket_id": ticket_id,
        "handled_by": handled_by,
        "final_output": (
            output.model_dump() if isinstance(output, BaseModel) else {"text": str(output)}
        ),
    }


# triage_batch only fans out, so it doesn't retry: a retry would rerun every
# ticket, including the ones that already finished. Each triage_ticket run
# retries on its own.
@app.task(
    retry=Retry(max_retries=0, wait_duration_ms=1000, backoff_scaling=1.5),
    timeout_seconds=600,
)
async def triage_batch(ctx: TaskContext, tickets: list[dict]) -> dict:
    """Triage many tickets in parallel, one task run per ticket."""
    logger.info(f"[BATCH] Triaging {len(tickets)} tickets")

    # ctx.run starts each ticket as its own task run on its own compute.
    # return_exceptions keeps one failed ticket from failing the batch.
    results = await asyncio.gather(
        *(ctx.run(triage_ticket, ticket) for ticket in tickets),
        return_exceptions=True,
    )

    triaged, failed = [], []
    for ticket, result in zip(tickets, results):
        if isinstance(result, BaseException):
            failed.append({"ticket_id": ticket.get("id"), "error": str(result)})
        elif "error" in result:
            failed.append(result)
        else:
            triaged.append(result)

    logger.info(f"[BATCH] {len(triaged)} triaged, {len(failed)} failed")
    return {
        "total": len(tickets),
        "triaged": len(triaged),
        "by_team": dict(Counter(r["handled_by"] for r in triaged)),
        "failed": failed,
        "results": triaged,
    }


if __name__ == "__main__":
    app.start()
