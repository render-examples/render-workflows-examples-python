"""
LLM evals on Render Workflows

An eval harness that compares models on the same dataset. Every model answers
every case in its own task run, an LLM judge scores each answer with a
structured verdict, and a parent task returns a scorecard per model: pass rate,
average score, latency, tokens, and the cases that failed.

The dataset is the support-ticket triage scenario the agent examples use
(tickets.json), plus the properties a good answer must have (cases.json).
"""

import asyncio
import json
import logging
import os
import statistics
import time
from typing import Literal

import anthropic
import openai
from pydantic import BaseModel, ValidationError
from render import Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# The models to compare, as provider:model pairs. They're read from the
# environment so you can compare different models without a code change.
EVAL_MODELS = os.getenv("EVAL_MODELS", "openai:gpt-6.2-sol,anthropic:claude-opus-5-5")

# The model that grades every answer. Pick one that isn't in EVAL_MODELS, so
# no contestant grades its own answers.
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "anthropic:claude-sonnet-5-5")

# Render Workflows has no concurrency cap of its own, so run_eval caps how many
# model calls each provider gets at once. Raise it if your rate limits allow.
MAX_CONCURRENT_PER_PROVIDER = int(os.getenv("MAX_CONCURRENT_PER_PROVIDER", "4"))

# Provider errors that mean the request itself is wrong: unknown model, bad
# key, invalid input. A retry can't fix them, so the tasks return them as
# results instead of raising. 429s and 5xx errors raise, and Render retries.
PERMANENT_ERROR_STATUSES = {400, 401, 403, 404, 413, 422}

API_KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

app = Workflows()


# ============================================================================
# Sample records. Every model sees the same facts for a ticket, so the eval
# compares answers, not lookups. In production these would come from your
# database.
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
# Output schemas. Both providers return JSON that matches these.
# ============================================================================


class TriageAnswer(BaseModel):
    """What each model under test returns for a ticket."""

    category: Literal["billing", "technical"]
    priority: Literal["low", "normal", "high", "urgent"]
    summary: str
    customer_reply: str


class CriterionResult(BaseModel):
    criterion: str
    met: bool
    explanation: str


class Verdict(BaseModel):
    """What the judge returns for one answer."""

    criteria: list[CriterionResult]
    score: Literal[1, 2, 3, 4, 5]
    reasoning: str


ANSWER_INSTRUCTIONS = (
    "You triage customer support tickets. Read the ticket and the account and "
    "order records, then classify the ticket and write the reply the customer "
    "will get. Categories: billing covers charges, refunds, invoices, payment "
    "methods and balances. technical covers everything else, including sign-in "
    "problems, API errors, outages, broken links, and shipping or tracking "
    "problems. Policy: duplicate charges are always refunded within 5 business "
    "days. Use only facts from the records and the policy. Keep the reply under "
    "120 words."
)

JUDGE_INSTRUCTIONS = (
    "You grade a support agent's reply to a customer ticket. For each criterion, "
    "decide whether the reply meets it and explain why in one sentence. Then "
    "score the reply's overall quality from 1 (harmful or wrong) to 5 (accurate, "
    "complete, and ready to send). Judge only the reply. Don't reward length."
)


def format_ticket(ticket: dict) -> str:
    """Render a ticket and its records as the model's input."""
    account = ACCOUNTS.get(ticket.get("account_id") or "")
    order = ORDERS.get(ticket.get("order_id") or "")
    return "\n".join(
        [
            f"Ticket: {ticket.get('id')}",
            f"Subject: {ticket.get('subject')}",
            "",
            str(ticket.get("body", "")),
            "",
            f"Account {ticket.get('account_id')}: {json.dumps(account)}",
            f"Order {ticket.get('order_id') or 'none'}: {json.dumps(order)}",
        ]
    )


# ============================================================================
# Provider calls
# ============================================================================


def parse_model(spec: str) -> tuple[str, str]:
    """Split "provider:model" into its parts."""
    provider, _, model = spec.partition(":")
    if provider not in API_KEY_ENV or not model:
        raise ValueError(
            f"Bad model {spec!r}. Use provider:model, with provider openai or anthropic."
        )
    return provider, model


class OutputError(Exception):
    """The model answered, but not with output that matches the schema."""


async def call_model(spec: str, instructions: str, prompt: str, schema: type[BaseModel]):
    """Ask one model for JSON that matches schema.

    Returns (parsed output, usage dict, latency in ms). Raises OutputError when
    the model refuses, runs out of tokens, or returns output that doesn't
    parse. Provider API errors propagate.
    """
    provider, model = parse_model(spec)
    api_key = os.environ[API_KEY_ENV[provider]]
    started = time.monotonic()

    try:
        if provider == "openai":
            # Render retries the task, so turn off the client's own retries to
            # keep one retry policy in charge.
            client = openai.AsyncOpenAI(api_key=api_key, max_retries=0, timeout=90)
            response = await client.responses.parse(
                model=model, instructions=instructions, input=prompt, text_format=schema
            )
            parsed = response.output_parsed
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            if parsed is None:
                raise OutputError(f"No parsed output (status {response.status})")
        else:
            client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0, timeout=90)
            response = await client.messages.parse(
                model=model,
                max_tokens=8000,
                system=instructions,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
            )
            parsed = response.parsed_output
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            # A refusal or a truncated answer is the model's result, so it's
            # scored as a failed case. No fallback model: a fallback would
            # score a different model's answer.
            if response.stop_reason in ("refusal", "max_tokens") or parsed is None:
                raise OutputError(f"No parsed output (stop reason {response.stop_reason})")
    except ValidationError as e:
        raise OutputError(f"Output didn't match the schema: {e.error_count()} errors") from e

    latency_ms = round((time.monotonic() - started) * 1000)
    return parsed, usage, latency_ms


def permanent_error(e: Exception) -> str | None:
    """Return a message if e is a provider error a retry can't fix."""
    if isinstance(e, openai.APIStatusError):
        provider = "OpenAI"
    elif isinstance(e, anthropic.APIStatusError):
        provider = "Anthropic"
    else:
        return None
    if e.status_code not in PERMANENT_ERROR_STATUSES:
        return None
    # OpenAI's error body is the error object. Anthropic's wraps it in "error".
    body = e.body if isinstance(e.body, dict) else {}
    error = body["error"] if isinstance(body.get("error"), dict) else body
    return f"{provider} error {e.status_code}: {error.get('message') or e.message}"


def missing_key(spec: str) -> str | None:
    """Return a message if spec is malformed or its provider's key isn't set."""
    try:
        provider, _ = parse_model(spec)
    except ValueError as e:
        return str(e)
    env = API_KEY_ENV[provider]
    return None if os.getenv(env) else f"{env} is not set"


# ============================================================================
# Tasks
# ============================================================================


# answer_case calls a model API, so it retries: a 429 or a 5xx gets three more
# attempts, 2, 4 and 8 seconds apart. Calling the model again is safe because
# nothing is written.
@app.task(
    retry=Retry(max_retries=3, wait_duration_ms=2000, backoff_scaling=2),
    timeout_seconds=120,
)
async def answer_case(ctx: TaskContext, model: str, ticket: dict) -> dict:
    """Have one model answer one ticket."""
    ticket_id = ticket.get("id", "unknown")
    result = {"model": model, "ticket_id": ticket_id}

    if problem := missing_key(model):
        # A setup problem a retry can't fix, so return it instead of raising.
        return {**result, "error": problem}

    try:
        answer, usage, latency_ms = await call_model(
            model, ANSWER_INSTRUCTIONS, format_ticket(ticket), TriageAnswer
        )
    except OutputError as e:
        # The model's own failure. run_eval counts it as a failed case.
        logger.warning(f"[ANSWER] {model} {ticket_id}: {e}")
        return {**result, "output_error": str(e)}
    except Exception as e:
        if message := permanent_error(e):
            logger.error(f"[ANSWER] {model} {ticket_id}: {message}, not retrying")
            return {**result, "error": message}
        raise

    logger.info(f"[ANSWER] {model} {ticket_id}: {answer.category}/{answer.priority} in {latency_ms} ms")
    return {**result, "answer": answer.model_dump(), "latency_ms": latency_ms, **usage}


# judge_answer calls a model API too, so it retries the same way.
@app.task(
    retry=Retry(max_retries=3, wait_duration_ms=2000, backoff_scaling=2),
    timeout_seconds=120,
)
async def judge_answer(ctx: TaskContext, ticket: dict, case: dict, answer: dict) -> dict:
    """Have the judge model grade one answer against the case's rubric."""
    ticket_id = ticket.get("id", "unknown")
    result = {"judge": JUDGE_MODEL, "ticket_id": ticket_id}

    if problem := missing_key(JUDGE_MODEL):
        return {**result, "error": problem}

    rubric = "\n".join(f"- {c}" for c in case.get("rubric", []))
    prompt = "\n".join(
        [
            "Ticket and records:",
            format_ticket(ticket),
            "",
            "Reply to grade:",
            str(answer.get("customer_reply", "")),
            "",
            "Criteria:",
            rubric,
        ]
    )

    try:
        verdict, _, latency_ms = await call_model(JUDGE_MODEL, JUDGE_INSTRUCTIONS, prompt, Verdict)
    except OutputError as e:
        return {**result, "error": f"Judge output unusable: {e}"}
    except Exception as e:
        if message := permanent_error(e):
            logger.error(f"[JUDGE] {ticket_id}: {message}, not retrying")
            return {**result, "error": message}
        raise

    logger.info(f"[JUDGE] {ticket_id}: score {verdict.score} in {latency_ms} ms")
    return {**result, **verdict.model_dump(), "latency_ms": latency_ms}


def check_answer(case: dict, answer: dict, verdict: dict) -> list[str]:
    """Return the reasons an answer fails its case. Empty means it passes."""
    reasons = []
    if answer["category"] != case["category"]:
        reasons.append(f"category {answer['category']}, expected {case['category']}")
    if answer["priority"] not in case["priorities"]:
        reasons.append(f"priority {answer['priority']}, expected one of {case['priorities']}")
    for c in verdict["criteria"]:
        if not c["met"]:
            reasons.append(f"not met: {c['criterion']}")
    return reasons


# run_eval only fans out and adds up, so it doesn't retry: a retry would rerun
# every model call, including the ones that already finished. Each
# answer_case and judge_answer run retries on its own.
@app.task(
    retry=Retry(max_retries=0, wait_duration_ms=1000, backoff_scaling=1.5),
    timeout_seconds=1800,
)
async def run_eval(ctx: TaskContext, tickets: list[dict], cases: list[dict]) -> dict:
    """Run every model on every case, judge each answer, and return a scorecard."""
    models = [m.strip() for m in EVAL_MODELS.split(",") if m.strip()]
    by_id = {t["id"]: t for t in tickets}
    cases = [c for c in cases if c["ticket_id"] in by_id]
    logger.info(f"[EVAL] {len(cases)} cases x {len(models)} models, judge {JUDGE_MODEL}")

    # Without a working judge nothing can be scored, so stop before paying for
    # any answers.
    if problem := missing_key(JUDGE_MODEL):
        return {"judge": JUDGE_MODEL, "error": f"Judge unavailable: {problem}"}

    # One semaphore per provider caps the model calls in flight, contestants
    # and judge together.
    limits: dict[str, asyncio.Semaphore] = {}

    def limit(spec: str) -> asyncio.Semaphore:
        provider = spec.partition(":")[0]
        return limits.setdefault(provider, asyncio.Semaphore(MAX_CONCURRENT_PER_PROVIDER))

    async def evaluate(model: str, case: dict) -> dict:
        ticket = by_id[case["ticket_id"]]
        # ctx.run starts each call as its own task run on its own compute.
        async with limit(model):
            answered = await ctx.run(answer_case, model, ticket)
        if "answer" not in answered:
            return answered
        async with limit(JUDGE_MODEL):
            verdict = await ctx.run(judge_answer, ticket, case, answered["answer"])
        if "error" in verdict:
            return {**answered, "error": verdict["error"]}
        return {**answered, "verdict": verdict}

    pairs = [(m, c) for m in models for c in cases]
    # return_exceptions keeps one failed run from failing the eval. A run that
    # raised used up its retries; it's listed as an error, not scored.
    outcomes = await asyncio.gather(
        *(evaluate(m, c) for m, c in pairs), return_exceptions=True
    )

    results = []
    for (model, case), outcome in zip(pairs, outcomes):
        if isinstance(outcome, BaseException):
            outcome = {"model": model, "ticket_id": case["ticket_id"], "error": str(outcome)}
        if "verdict" in outcome:
            reasons = check_answer(case, outcome["answer"], outcome["verdict"])
            outcome = {**outcome, "passed": not reasons, "reasons": reasons}
        elif "output_error" in outcome:
            outcome = {**outcome, "passed": False, "reasons": [outcome["output_error"]]}
        results.append(outcome)

    scorecard = [summarize(model, [r for r in results if r["model"] == model]) for model in models]
    # Best model first: highest pass rate, then highest average score.
    scorecard.sort(key=lambda s: (s["pass_rate"] or 0, s["avg_score"] or 0), reverse=True)

    for s in scorecard:
        logger.info(f"[EVAL] {s['model']}: {s['passed']}/{s['scored']} passed, avg score {s['avg_score']}")
    return {"judge": JUDGE_MODEL, "cases": len(cases), "scorecard": scorecard, "results": results}


def summarize(model: str, results: list[dict]) -> dict:
    """Add up one model's results."""
    scored = [r for r in results if "passed" in r]
    judged = [r for r in scored if "verdict" in r]
    latencies = [r["latency_ms"] for r in results if "latency_ms" in r]
    passed = sum(r["passed"] for r in scored)
    return {
        "model": model,
        "scored": len(scored),
        "passed": passed,
        "pass_rate": round(passed / len(scored), 2) if scored else None,
        "avg_score": (
            round(statistics.mean(r["verdict"]["score"] for r in judged), 2) if judged else None
        ),
        "latency_ms": {
            "p50": round(statistics.median(latencies)) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "tokens": {
            "input": sum(r.get("input_tokens", 0) for r in results),
            "output": sum(r.get("output_tokens", 0) for r in results),
        },
        "failures": [
            {"ticket_id": r["ticket_id"], "reasons": r["reasons"]}
            for r in scored
            if not r["passed"]
        ],
        "errors": [{"ticket_id": r["ticket_id"], "error": r["error"]} for r in results if "error" in r],
    }


if __name__ == "__main__":
    app.start()
