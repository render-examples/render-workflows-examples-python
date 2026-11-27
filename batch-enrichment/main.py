"""
Batch CSV enrichment with the OpenAI Batch API on Render Workflows

Enriches every row of a CSV of companies (industry, size bucket, one-line
summary) through the OpenAI Batch API, which costs half as much as normal API
calls but can take up to 24 hours. One task submits the batch, one waits for it
on the flex plan, and one parses, validates and joins the results back to the
rows, reporting the rows that failed.

The waiting task sleeps between status checks inside a single long attempt.
See the README for why this beats "retry until done" on Render.
"""

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import pathlib
import time
import urllib.error
import urllib.request
from typing import Literal

from openai import APIConnectionError, APIStatusError, OpenAI
from pydantic import BaseModel, Field, ValidationError
from render import Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Read the model from the environment so a retired model is a config change,
# not a code change.
MODEL = os.getenv("OPENAI_MODEL", "gpt-6.2-sol")

# How long wait_for_batch sleeps between status checks. Small batches often
# finish in a few minutes; large ones can take hours.
POLL_SECONDS = int(os.getenv("BATCH_POLL_SECONDS", "60"))

# The CSV that ships with this example. Pass a csv_url to enrich your own.
BUNDLED_CSV = pathlib.Path(__file__).parent / "companies.csv"

# OpenAI errors that mean the request itself is wrong: bad key, unknown model,
# invalid input. A retry can't fix them, so tasks return them as results.
PERMANENT_ERROR_STATUSES = {400, 401, 403, 404, 422}

# Batch statuses after which nothing changes.
TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}

# Bump this when you change the prompt or schema, so a rerun submits a new
# batch instead of reusing one built with the old prompt.
PROMPT_VERSION = "v1"

INSTRUCTIONS = (
    "You enrich rows of a company list for a sales team. From the company name, "
    "website and description, pick the closest industry, estimate the headcount "
    "bucket, and write a one-sentence summary of what the company does (at most "
    "25 words, no line breaks). Use only the information given."
)

INDUSTRIES = [
    "Software",
    "Fintech",
    "Healthcare",
    "Logistics",
    "Manufacturing",
    "Energy",
    "Agriculture",
    "Education",
    "Media & Entertainment",
    "Professional Services",
    "Other",
]
SIZE_BUCKETS = ["1-10", "11-50", "51-200", "201-1000", "1000+"]

app = Workflows()


# ============================================================================
# The enrichment schema. The model must answer in this shape (strict JSON
# schema), and collect_results validates every answer against it again.
# ============================================================================


class Enrichment(BaseModel):
    industry: Literal[tuple(INDUSTRIES)]  # type: ignore[valid-type]
    size_bucket: Literal[tuple(SIZE_BUCKETS)]  # type: ignore[valid-type]
    summary: str = Field(min_length=10, max_length=240, pattern=r"^[^\n]+$")


ENRICHMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["industry", "size_bucket", "summary"],
    "properties": {
        "industry": {"type": "string", "enum": INDUSTRIES},
        "size_bucket": {"type": "string", "enum": SIZE_BUCKETS},
        "summary": {"type": "string"},
    },
}


# ============================================================================
# Helpers
# ============================================================================


def load_rows(csv_url: str | None) -> list[dict]:
    """Read the CSV from a URL, or the bundled companies.csv if none is given.

    Tasks take the URL rather than the rows, so task arguments stay small no
    matter how big the CSV is (a run's arguments are capped at 4 MB).
    """
    if csv_url:
        with urllib.request.urlopen(csv_url, timeout=30) as resp:
            text = resp.read().decode("utf-8")
    else:
        text = BUNDLED_CSV.read_text(encoding="utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def split_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separate rows we can send from rows that fail input checks."""
    valid, invalid, seen = [], [], set()
    for row in rows:
        row_id = (row.get("id") or "").strip()
        if not row_id:
            invalid.append({"id": None, "error": "Row has no id"})
        elif row_id in seen:
            invalid.append({"id": row_id, "error": "Duplicate id"})
        elif not (row.get("company") or "").strip():
            invalid.append({"id": row_id, "error": "Row has no company name"})
        else:
            valid.append(row)
        seen.add(row_id)
    return valid, invalid


def job_key(rows: list[dict]) -> str:
    """A fingerprint of the rows, model and prompt, stored on the batch.

    submit_batch looks for an existing batch with this key before creating
    one, so a retry or a rerun of the same CSV reuses the batch instead of
    paying for a second one.
    """
    payload = json.dumps([MODEL, PROMPT_VERSION, rows], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def request_line(row: dict) -> str:
    """One line of the batch input file: a Responses API request for one row."""
    return json.dumps(
        {
            "custom_id": row["id"],
            "method": "POST",
            "url": "/v1/responses",
            "body": {
                "model": MODEL,
                "instructions": INSTRUCTIONS,
                "input": json.dumps(
                    {k: row.get(k, "") for k in ("company", "website", "description")}
                ),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "company_enrichment",
                        "schema": ENRICHMENT_SCHEMA,
                        "strict": True,
                    }
                },
            },
        }
    )


def output_text(body: dict) -> str | None:
    """The model's text answer from a Responses API body, if there is one."""
    for item in body.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    return part.get("text")
    return None


def openai_client() -> OpenAI | None:
    if not os.getenv("OPENAI_API_KEY"):
        return None
    # Render retries these tasks, so turn off the OpenAI client's own retries
    # to keep one retry policy in charge.
    return OpenAI(max_retries=0)


def batch_summary(batch) -> dict:
    counts = batch.request_counts
    return {
        "batch_id": batch.id,
        "status": batch.status,
        "request_counts": counts.model_dump() if counts else None,
        "errors": [e.model_dump() for e in batch.errors.data] if batch.errors and batch.errors.data else [],
    }


# ============================================================================
# Tasks
# ============================================================================


# submit_batch uploads the input file and creates the batch. It retries on
# network errors and 5xx. Retrying is safe because it reuses a batch that has
# the same job key instead of creating a second one.
@app.task(
    retry=Retry(max_retries=2, wait_duration_ms=2000, backoff_scaling=2),
    timeout_seconds=600,
)
def submit_batch(ctx: TaskContext, csv_url: str | None = None) -> dict:
    """Build the JSONL input from the CSV and submit it as one OpenAI batch."""
    client = openai_client()
    if client is None:
        return {"error": "OPENAI_API_KEY is not set"}

    try:
        rows, invalid = split_rows(load_rows(csv_url))
    except (urllib.error.URLError, UnicodeDecodeError, csv.Error) as e:
        return {"error": f"Could not read the CSV: {e}"}
    if not rows:
        return {"error": "No valid rows to enrich", "invalid": invalid}

    key = job_key(rows)
    try:
        # Reuse a batch for these exact rows if one is running or finished.
        # Batches are listed newest first; only look back two days.
        cutoff = time.time() - 2 * 24 * 3600
        for batch in client.batches.list(limit=100):
            if batch.created_at < cutoff:
                break
            if (batch.metadata or {}).get("job_key") == key and batch.status not in {
                "failed",
                "expired",
                "cancelled",
                "cancelling",
            }:
                logger.info(f"[SUBMIT] Reusing batch {batch.id} ({batch.status})")
                return {"batch_id": batch.id, "rows": len(rows), "reused": True}

        jsonl = "\n".join(request_line(row) for row in rows).encode()
        input_file = client.files.create(
            file=("enrichment.jsonl", io.BytesIO(jsonl)), purpose="batch"
        )
        batch = client.batches.create(
            input_file_id=input_file.id,
            endpoint="/v1/responses",
            completion_window="24h",
            metadata={"job_key": key, "source": "render-workflows-batch-enrichment"},
        )
    except APIStatusError as e:
        if e.status_code in PERMANENT_ERROR_STATUSES:
            return {"error": f"OpenAI error {e.status_code}: {e.message}"}
        raise

    logger.info(f"[SUBMIT] Created batch {batch.id} with {len(rows)} requests")
    return {"batch_id": batch.id, "rows": len(rows), "reused": False}


# wait_for_batch is the long wait. It runs on flex, which bills for the CPU and
# memory a run actually uses, and sleeps between checks inside one attempt.
# The 24-hour timeout matches the Batch API's 24-hour completion window.
# Errors while checking are logged and retried on the next check, so one
# network blip doesn't end a wait that has already run for hours.
@app.task(
    plan="flex",
    retry=Retry(max_retries=1, wait_duration_ms=5000, backoff_scaling=2),
    timeout_seconds=24 * 3600,
)
async def wait_for_batch(ctx: TaskContext, batch_id: str) -> dict:
    """Check the batch every BATCH_POLL_SECONDS until it reaches a final status."""
    client = openai_client()
    if client is None:
        return {"batch_id": batch_id, "error": "OPENAI_API_KEY is not set"}

    started = time.monotonic()
    last_status = None
    while True:
        try:
            batch = await asyncio.to_thread(client.batches.retrieve, batch_id)
        except APIStatusError as e:
            if e.status_code in PERMANENT_ERROR_STATUSES:
                return {"batch_id": batch_id, "error": f"OpenAI error {e.status_code}: {e.message}"}
            logger.warning(f"[WAIT] {batch_id}: OpenAI error {e.status_code}, checking again later")
        except APIConnectionError as e:
            logger.warning(f"[WAIT] {batch_id}: {e}, checking again later")
        else:
            if batch.status != last_status:
                logger.info(f"[WAIT] {batch_id}: {batch.status} {batch.request_counts}")
                last_status = batch.status
            if batch.status in TERMINAL_STATUSES:
                return {**batch_summary(batch), "waited_seconds": round(time.monotonic() - started)}
        await asyncio.sleep(POLL_SECONDS)


# collect_results downloads the output and error files, validates every answer,
# and joins it back to its row by custom_id (output order isn't guaranteed to
# match input order). It only reads, so a retry is safe.
@app.task(
    retry=Retry(max_retries=2, wait_duration_ms=2000, backoff_scaling=2),
    timeout_seconds=900,
)
def collect_results(ctx: TaskContext, batch_id: str, csv_url: str | None = None) -> dict:
    """Join a finished batch's results back to the CSV rows."""
    client = openai_client()
    if client is None:
        return {"batch_id": batch_id, "error": "OPENAI_API_KEY is not set"}

    try:
        rows, invalid = split_rows(load_rows(csv_url))
        batch = client.batches.retrieve(batch_id)
        output = client.files.content(batch.output_file_id).text if batch.output_file_id else ""
        errors = client.files.content(batch.error_file_id).text if batch.error_file_id else ""
    except (urllib.error.URLError, UnicodeDecodeError, csv.Error) as e:
        return {"batch_id": batch_id, "error": f"Could not read the CSV: {e}"}
    except APIStatusError as e:
        if e.status_code in PERMANENT_ERROR_STATUSES:
            return {"batch_id": batch_id, "error": f"OpenAI error {e.status_code}: {e.message}"}
        raise

    # One result line per request, from either file. Expired or cancelled
    # batches still return the requests that finished.
    by_id: dict[str, dict] = {}
    for line in (output + "\n" + errors).splitlines():
        if line.strip():
            result = json.loads(line)
            by_id[result["custom_id"]] = result

    enriched, failed = [], list(invalid)
    for row in rows:
        result = by_id.get(row["id"])
        if result is None:
            failed.append({"id": row["id"], "error": f"No result (batch {batch.status})"})
            continue
        if result.get("error"):
            failed.append({"id": row["id"], "error": result["error"].get("message", str(result["error"]))})
            continue
        response = result.get("response") or {}
        body = response.get("body") or {}
        if response.get("status_code") != 200:
            message = (body.get("error") or {}).get("message", "request failed")
            failed.append({"id": row["id"], "error": f"HTTP {response.get('status_code')}: {message}"})
            continue
        text = output_text(body)
        if text is None:
            failed.append({"id": row["id"], "error": f"No answer (response {body.get('status')})"})
            continue
        try:
            enrichment = Enrichment.model_validate_json(text)
        except ValidationError as e:
            failed.append({"id": row["id"], "error": f"Invalid answer: {e.errors()[0]['msg']}"})
            continue
        enriched.append(
            {"id": row["id"], "company": row["company"], "website": row.get("website", ""), **enrichment.model_dump()}
        )

    logger.info(f"[COLLECT] {batch_id}: {len(enriched)} enriched, {len(failed)} failed")
    return {
        "batch_id": batch_id,
        "batch_status": batch.status,
        "total": len(rows) + len(invalid),
        "enriched": len(enriched),
        "failed": failed,
        "rows": enriched,
    }


# enrich_csv chains the three steps. It doesn't retry: each step has its own
# retry policy. If it fails or times out partway, run it again with the same
# CSV: submit_batch finds the existing batch, so the rerun picks up where the
# last one left off and nothing is paid for twice.
@app.task(
    plan="flex",
    retry=Retry(max_retries=0, wait_duration_ms=1000, backoff_scaling=1.5),
    timeout_seconds=24 * 3600,
)
async def enrich_csv(ctx: TaskContext, csv_url: str | None = None) -> dict:
    """Submit the CSV as a batch, wait for it, and return the enriched rows."""
    submitted = await ctx.run(submit_batch, csv_url)
    if "error" in submitted:
        return submitted

    batch_id = submitted["batch_id"]
    finished = await ctx.run(wait_for_batch, batch_id)
    if "error" in finished:
        return finished
    if finished["status"] == "failed":
        # The input file failed validation, so no request ran.
        return {"batch_id": batch_id, "error": "Batch failed validation", "details": finished["errors"]}

    result = await ctx.run(collect_results, batch_id, csv_url)
    return {**result, "waited_seconds": finished["waited_seconds"], "reused_batch": submitted["reused"]}


if __name__ == "__main__":
    app.start()
