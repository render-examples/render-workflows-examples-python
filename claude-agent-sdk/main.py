"""
Claude Agent SDK on Render Workflows

A support-ticket triage agent built with the Claude Agent SDK, running inside
a workflow task. The SDK runs the agent loop (model calls and tool calls)
within one task run. Render runs that task, retries it, and fans it out
across many tickets.

The SDK drives a Claude Code process that it starts for each query(). The
Python package bundles that process as a native binary, so the service needs
no Node.js install.
"""

import asyncio
import json
import logging
import os
import tempfile
from collections import Counter
from typing import Any, Literal

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultError,
    ResultMessage,
    ToolAnnotations,
    create_sdk_mcp_server,
    query,
    tool,
)
from pydantic import BaseModel
from render import Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Read the model from the environment so a retired model is a config change,
# not a code change.
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")

# Upper bound on agent turns per ticket, so an agent that keeps calling tools
# can't run forever. Each model response counts as one turn.
MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "8"))

# Anthropic API errors that mean the request itself is wrong: unknown model,
# bad key, invalid input. A retry can't fix them, so triage_ticket returns them
# as a result instead of raising.
PERMANENT_ERROR_STATUSES = {400, 401, 403, 404, 422}

# Run results a retry can't fix: the agent ran out of turns, or couldn't
# produce output that matches the Resolution schema.
PERMANENT_RESULT_SUBTYPES = {"error_max_turns", "error_max_structured_output_retries"}

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
# They run in this Python process, served to Claude by an in-process MCP server.
# ============================================================================


@tool(
    "lookup_order",
    "Look up an order's status, amount, and how many times it was charged.",
    {"order_id": str},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def lookup_order(args: dict[str, Any]) -> dict[str, Any]:
    order = ORDERS.get(args["order_id"])
    if not order:
        return {
            "content": [{"type": "text", "text": f"No order {args['order_id']}"}],
            "is_error": True,
        }
    return {"content": [{"type": "text", "text": json.dumps(order)}]}


@tool(
    "get_account_status",
    "Look up an account's plan, standing, and balance due.",
    {"account_id": str},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def get_account_status(args: dict[str, Any]) -> dict[str, Any]:
    account = ACCOUNTS.get(args["account_id"])
    if not account:
        return {
            "content": [{"type": "text", "text": f"No account {args['account_id']}"}],
            "is_error": True,
        }
    return {"content": [{"type": "text", "text": json.dumps(account)}]}


support_server = create_sdk_mcp_server(
    name="support",
    version="1.0.0",
    tools=[lookup_order, get_account_status],
)


# ============================================================================
# Agent
# ============================================================================


class Resolution(BaseModel):
    """The structured result the agent returns for a ticket."""

    category: Literal["billing", "technical"]
    priority: Literal["low", "normal", "high", "urgent"]
    summary: str
    next_steps: list[str]
    customer_reply: str


SYSTEM_PROMPT = (
    "You triage customer support tickets. Decide whether each ticket is a "
    "billing issue (charges, refunds, invoices, payment methods, balances) or a "
    "technical issue (sign-in problems, API errors, outages, broken links). "
    "Check the order or account with the tools before you answer. Then return "
    "a resolution with the next steps for the support team and a reply to the "
    "customer."
)


def format_ticket(ticket: dict) -> str:
    """Render a ticket as the agent's prompt."""
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


def agent_options(workdir: str) -> ClaudeAgentOptions:
    """Options for one agent run, isolated in its own working directory."""
    return ClaudeAgentOptions(
        model=MODEL,
        system_prompt=SYSTEM_PROMPT,
        max_turns=MAX_TURNS,
        # Serve the custom tools in-process and pre-approve them.
        mcp_servers={"support": support_server},
        allowed_tools=["mcp__support__lookup_order", "mcp__support__get_account_status"],
        # No built-in tools (Bash, Read, Edit, ...). This agent only needs the
        # two lookups, so it gets nothing that can touch the machine.
        tools=[],
        # Return a Resolution as validated JSON on the result message.
        output_format={"type": "json_schema", "schema": Resolution.model_json_schema()},
        # Each run gets its own working directory and Claude Code config
        # directory, so concurrent runs on one instance share no files and
        # session transcripts are deleted with the directory.
        cwd=workdir,
        setting_sources=[],
        env={
            "CLAUDE_CONFIG_DIR": os.path.join(workdir, ".claude"),
            # Render retries this task, so turn off Claude Code's own API
            # retries to keep one retry policy in charge.
            "CLAUDE_CODE_MAX_RETRIES": "0",
        },
    )


# ============================================================================
# Tasks
# ============================================================================


# triage_ticket calls the Anthropic API, so it retries once if an attempt fails.
# A retry reruns the whole agent. That's safe here because the tools only read.
@app.task(
    retry=Retry(max_retries=1, wait_duration_ms=1000, backoff_scaling=2),
    timeout_seconds=180,
)
async def triage_ticket(ctx: TaskContext, ticket: dict) -> dict:
    """Triage one support ticket and return the agent's structured result."""
    ticket_id = ticket.get("id", "unknown")
    logger.info(f"[TRIAGE] {ticket_id}: {ticket.get('subject')}")

    if not os.getenv("ANTHROPIC_API_KEY"):
        # A setup problem a retry can't fix, so return it instead of raising.
        return {"ticket_id": ticket_id, "error": "ANTHROPIC_API_KEY is not set"}

    result: ResultMessage | None = None
    with tempfile.TemporaryDirectory(prefix=f"triage-{ticket_id}-") as workdir:
        try:
            async for message in query(
                prompt=format_ticket(ticket), options=agent_options(workdir)
            ):
                if isinstance(message, ResultMessage):
                    result = message
        except ResultError as e:
            # query() raises ResultError when the run ends in an error result.
            if e.subtype in PERMANENT_RESULT_SUBTYPES:
                logger.warning(f"[TRIAGE] {ticket_id}: run ended as {e.subtype}")
                return {"ticket_id": ticket_id, "error": f"Agent run ended as {e.subtype}"}
            if e.api_error_status in PERMANENT_ERROR_STATUSES:
                logger.error(
                    f"[TRIAGE] {ticket_id}: Anthropic error {e.api_error_status}, not retrying"
                )
                return {
                    "ticket_id": ticket_id,
                    "error": f"Anthropic error {e.api_error_status}: {e.result}",
                }
            # Rate limits, overloads, timeouts: raise so Render retries.
            raise

    if result is None or result.structured_output is None:
        # The run ended without a Resolution. Raise so Render retries once.
        raise RuntimeError(f"Agent returned no structured output for {ticket_id}")

    # Validate the agent's output and return it as plain JSON.
    resolution = Resolution.model_validate(result.structured_output)
    cost = round(result.total_cost_usd or 0, 6)
    logger.info(
        f"[TRIAGE] {ticket_id}: {resolution.category}, {result.num_turns} turns, ${cost}"
    )
    return {
        "ticket_id": ticket_id,
        "final_output": resolution.model_dump(),
        "num_turns": result.num_turns,
        "total_cost_usd": cost,
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
        "by_category": dict(Counter(r["final_output"]["category"] for r in triaged)),
        "total_cost_usd": round(sum(r["total_cost_usd"] for r in triaged), 6),
        "failed": failed,
        "results": triaged,
    }


if __name__ == "__main__":
    app.start()
