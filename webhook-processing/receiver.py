"""
Webhook processing: the receiver (web service)

A FastAPI app that receives GitHub webhooks and hands each one to a workflow
task. For every delivery it:

1. Verifies the X-Hub-Signature-256 header against the raw request body.
2. Stores the payload in Postgres, keyed by the X-GitHub-Delivery ID.
3. Answers 200 without doing anything else if that delivery was already
   processed (a redelivery).
4. Otherwise starts process_delivery with the delivery ID and answers 202.

It does no processing itself, so it answers in well under GitHub's 10-second
limit.
"""

import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from render import RenderAsync

import db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

SLUG = os.getenv("WORKFLOW_SLUG", "webhook-processing")

# The events process_delivery handles. GitHub sends only the events you pick
# in the webhook's settings; anything else gets a 200 and is dropped.
HANDLED_EVENTS = {"push", "issues"}

render: RenderAsync | None = None
secret = b""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global render, secret
    # Without a secret, anyone who finds the URL could send fake events.
    # Refuse to start rather than accept unsigned requests.
    if not os.getenv("GITHUB_WEBHOOK_SECRET"):
        raise RuntimeError("GITHUB_WEBHOOK_SECRET is not set")
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is not set")
    secret = os.environ["GITHUB_WEBHOOK_SECRET"].encode()
    await db.apply_schema()
    # Reads RENDER_API_KEY, or RENDER_USE_LOCAL_DEV to use the local task server.
    render = RenderAsync()
    yield


app = FastAPI(title="Webhook processing", lifespan=lifespan)


def signature_ok(body: bytes, header: str | None) -> bool:
    """Check X-Hub-Signature-256: "sha256=" + the HMAC-SHA256 hex digest of the body."""
    if not header:
        return False
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    # compare_digest takes the same time wherever the strings differ, so the
    # response time doesn't leak how much of a forged signature was right.
    return hmac.compare_digest(expected, header)


@app.post("/webhooks/github")
async def github_webhook(request: Request):
    started = time.perf_counter()

    # Verify against the exact bytes GitHub signed. Parsing and re-encoding
    # the JSON first would change them.
    body = await request.body()
    if not signature_ok(body, request.headers.get("X-Hub-Signature-256")):
        logger.warning("[RECEIVER] Rejected a request with a missing or bad signature")
        return JSONResponse(status_code=401, content={"detail": "Bad signature"})

    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    if not event or not delivery_id:
        return JSONResponse(status_code=400, content={"detail": "Missing GitHub headers"})

    # GitHub sends a ping when you create the webhook.
    if event == "ping":
        return {"ok": True}
    if event not in HANDLED_EVENTS:
        return {"ignored": event}

    if not request.headers.get("Content-Type", "").startswith("application/json"):
        return JSONResponse(
            status_code=415,
            content={"detail": "Set the webhook's content type to application/json"},
        )
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "Body isn't JSON"})

    # Store the payload and pass only the delivery ID to the task. GitHub
    # payloads can be up to 25 MB; task arguments are capped at 4 MB.
    async with await db.connect() as conn:
        cur = await conn.execute(
            """
            INSERT INTO webhook_deliveries (delivery_id, event, payload)
            VALUES (%s, %s, %s)
            ON CONFLICT (delivery_id)
              DO UPDATE SET receipts = webhook_deliveries.receipts + 1
            RETURNING status, receipts
            """,
            (delivery_id, event, Jsonb(payload)),
        )
        row = await cur.fetchone()

        # A redelivery of a delivery that's already processed: acknowledge it
        # and start nothing.
        if row["status"] == "processed":
            logger.info(f"[RECEIVER] {delivery_id} ({event}) duplicate, receipt {row['receipts']}")
            return {"delivery_id": delivery_id, "duplicate": True}

        # New, or sent again before it was processed (for example after a
        # failed run): start processing. If two runs end up processing the
        # same delivery, process_delivery applies it only once.
        try:
            run = await render.workflows.start_task(f"{SLUG}/process_delivery", [delivery_id])
        except Exception:
            # Fail the delivery so it shows as failed in GitHub and can be
            # redelivered. The payload is stored, so replay_pending can also
            # pick it up.
            logger.exception(f"[RECEIVER] Couldn't start process_delivery for {delivery_id}")
            return JSONResponse(status_code=503, content={"detail": "Couldn't start processing"})

        await conn.execute(
            "UPDATE webhook_deliveries SET last_run_id = %s WHERE delivery_id = %s",
            (run.id, delivery_id),
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    logger.info(f"[RECEIVER] {delivery_id} ({event}) -> {run.id} in {elapsed_ms} ms")
    return JSONResponse(status_code=202, content={"delivery_id": delivery_id, "run_id": run.id})


@app.get("/healthz")
async def healthz():
    return {"ok": True}
