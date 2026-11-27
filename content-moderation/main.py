"""
Content moderation on Render Workflows

A moderation pipeline for user-generated posts. A parent task fans out one
task run per post. Each run sends the post's text and image to the OpenAI
moderation endpoint and applies a two-threshold policy: approve, flag for
human review, or reject. Flagged posts get a short note for the reviewer from
a chat model. The parent returns counts per decision and the review queue.
"""

import asyncio
import base64
import logging
import os

import httpx2
import openai
from render import Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# The moderation model. A dated snapshot keeps scores stable, so the
# thresholds below keep meaning the same thing. omni-moderation-latest follows
# OpenAI's newest model instead.
MODERATION_MODEL = os.getenv("MODERATION_MODEL", "omni-moderation-2024-09-26")

# The model that writes notes for human reviewers. Only flagged posts use it.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6.2-sol")

# The policy. The moderation endpoint scores each category from 0 to 1. A post
# is rejected if any category scores at or above REJECT_THRESHOLD, flagged for
# review if any scores at or above FLAG_THRESHOLD, and approved otherwise.
FLAG_THRESHOLD = float(os.getenv("FLAG_THRESHOLD", "0.4"))
REJECT_THRESHOLD = float(os.getenv("REJECT_THRESHOLD", "0.9"))

# Render Workflows has no concurrency cap of its own, so moderate_posts caps
# how many runs it has in flight. Raise it if your OpenAI rate limits allow.
MAX_CONCURRENT_POSTS = int(os.getenv("MAX_CONCURRENT_POSTS", "8"))

# This example's own cap on image downloads, to keep requests small. The
# image is sent base64-encoded, which adds about a third.
MAX_IMAGE_BYTES = 5_000_000

# Some image hosts, Wikimedia among them, reject requests without one.
USER_AGENT = "render-workflows-examples/1.0 (content-moderation example)"

# OpenAI errors that mean the request itself is wrong: unknown model, bad key,
# an image it can't read. A retry can't fix them, so the tasks return them as
# results instead of raising. 429s and 5xx errors raise, and Render retries.
PERMANENT_ERROR_STATUSES = {400, 401, 403, 404, 413, 422}

app = Workflows()


class PermanentError(Exception):
    """A problem with the post itself that a retry can't fix."""


def openai_error(e: Exception) -> str | None:
    """Return a message if e is an OpenAI error a retry can't fix."""
    if not isinstance(e, openai.APIStatusError) or e.status_code not in PERMANENT_ERROR_STATUSES:
        return None
    body = e.body if isinstance(e.body, dict) else {}
    return f"OpenAI error {e.status_code}: {body.get('message') or e.message}"


def client() -> openai.AsyncOpenAI:
    # Render retries the task, so turn off the client's own retries to keep
    # one retry policy in charge.
    return openai.AsyncOpenAI(max_retries=0, timeout=30)


# ============================================================================
# Image download
# ============================================================================


async def fetch_image(url: str) -> str:
    """Download an image and return it as a data URL.

    The task downloads the image itself instead of passing the URL to OpenAI,
    so it can tell a missing image (permanent) from a host that's slow or
    down (worth a retry). Raises PermanentError for the first kind; network
    errors and 408, 429 and 5xx responses raise httpx2 errors, so Render
    retries.
    """
    if not url.startswith(("https://", "http://")):
        raise PermanentError(f"Not an http(s) URL: {url}")

    async with httpx2.AsyncClient(
        timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as http:
        async with http.stream("GET", url) as response:
            status = response.status_code
            if status in (408, 429) or status >= 500:
                response.raise_for_status()
            if status >= 400:
                raise PermanentError(f"Image URL returned HTTP {status}")

            content_type = response.headers.get("content-type", "").split(";")[0].strip()
            if not content_type.startswith("image/"):
                raise PermanentError(f"Image URL returned {content_type or 'no content type'}, not an image")

            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise PermanentError(f"Image is larger than {MAX_IMAGE_BYTES} bytes")

    return f"data:{content_type};base64,{base64.b64encode(data).decode()}"


# ============================================================================
# Policy
# ============================================================================


def apply_policy(scores: dict, input_types: dict) -> tuple[str, list[dict]]:
    """Turn category scores into a decision and the reasons for it."""
    reasons = [
        {"category": category, "score": round(score, 3), "inputs": input_types.get(category, [])}
        for category, score in scores.items()
        if score is not None and score >= FLAG_THRESHOLD
    ]
    reasons.sort(key=lambda r: r["score"], reverse=True)

    if any(r["score"] >= REJECT_THRESHOLD for r in reasons):
        return "reject", reasons
    if reasons:
        return "flag", reasons
    return "approve", reasons


# ============================================================================
# Tasks
# ============================================================================


# moderate_post calls the moderation endpoint, so it retries: a 429, a 5xx or
# a network error gets three more attempts, 2, 4 and 8 seconds apart. The
# policy decision is the task's result, whatever it is. Nothing is written, so
# a retry is safe.
@app.task(
    retry=Retry(max_retries=3, wait_duration_ms=2000, backoff_scaling=2),
    timeout_seconds=60,
)
async def moderate_post(ctx: TaskContext, post: dict) -> dict:
    """Moderate one post and decide: approve, flag or reject."""
    post_id = post.get("id", "unknown")
    result = {"post_id": post_id}

    if not os.getenv("OPENAI_API_KEY"):
        return {**result, "error": "OPENAI_API_KEY is not set"}
    text, image_url = post.get("text"), post.get("image_url")
    if not text and not image_url:
        return {**result, "error": "Post has no text or image_url"}

    try:
        # One request covers the text and the image. The endpoint takes at
        # most one image per request.
        inputs = []
        if text:
            inputs.append({"type": "text", "text": text})
        if image_url:
            inputs.append({"type": "image_url", "image_url": {"url": await fetch_image(image_url)}})

        response = await client().moderations.create(model=MODERATION_MODEL, input=inputs)
    except PermanentError as e:
        logger.warning(f"[MODERATE] {post_id}: {e}, not retrying")
        return {**result, "error": str(e)}
    except Exception as e:
        if message := openai_error(e):
            logger.error(f"[MODERATE] {post_id}: {message}, not retrying")
            return {**result, "error": message}
        raise

    moderation = response.results[0]
    decision, reasons = apply_policy(
        moderation.category_scores.model_dump(by_alias=True),
        moderation.category_applied_input_types.model_dump(by_alias=True),
    )
    top = f" ({reasons[0]['category']} {reasons[0]['score']})" if reasons else ""
    logger.info(f"[MODERATE] {post_id}: {decision}{top}")
    return {**result, "decision": decision, "reasons": reasons}


NOTE_INSTRUCTIONS = (
    "You help a human content moderator. A post was held for review because a "
    "moderation model scored it above the review threshold in the categories "
    "listed. In at most two sentences, say what in the post most likely caused "
    "the hold and whether it reads as a joke, an idiom, a heated opinion, or a "
    "real attack. Don't decide the outcome. The reviewer does."
)


# write_review_note calls a chat model, so it retries the same way.
@app.task(
    retry=Retry(max_retries=3, wait_duration_ms=2000, backoff_scaling=2),
    timeout_seconds=60,
)
async def write_review_note(ctx: TaskContext, post: dict, reasons: list[dict]) -> dict:
    """Write a short note that helps a human reviewer decide on a flagged post."""
    post_id = post.get("id", "unknown")
    if not os.getenv("OPENAI_API_KEY"):
        return {"post_id": post_id, "error": "OPENAI_API_KEY is not set"}

    flagged = ", ".join(f"{r['category']} {r['score']} (from {'/'.join(r['inputs'])})" for r in reasons)
    lines = [f"Post text: {post.get('text') or '(none)'}"]
    if post.get("image_url"):
        lines.append("The post also has an image, which isn't shown here.")
    lines.append(f"Categories over the threshold: {flagged}")
    prompt = "\n".join(lines)

    try:
        response = await client().responses.create(
            model=OPENAI_MODEL, instructions=NOTE_INSTRUCTIONS, input=prompt
        )
    except Exception as e:
        if message := openai_error(e):
            logger.error(f"[NOTE] {post_id}: {message}, not retrying")
            return {"post_id": post_id, "error": message}
        raise

    if response.status != "completed" or not response.output_text:
        return {"post_id": post_id, "error": f"No note (status {response.status})"}
    logger.info(f"[NOTE] {post_id}: {len(response.output_text)} characters")
    return {"post_id": post_id, "note": response.output_text}


# moderate_posts only fans out and adds up, so it doesn't retry: a retry would
# moderate every post again. Each moderate_post and write_review_note run
# retries on its own.
@app.task(
    retry=Retry(max_retries=0, wait_duration_ms=1000, backoff_scaling=1.5),
    timeout_seconds=1800,
)
async def moderate_posts(ctx: TaskContext, posts: list[dict]) -> dict:
    """Moderate every post and return counts per decision and the review queue."""
    logger.info(f"[BATCH] {len(posts)} posts, model {MODERATION_MODEL}")
    policy = {"flag_threshold": FLAG_THRESHOLD, "reject_threshold": REJECT_THRESHOLD}

    # Without a key every post would fail the same way, so stop here.
    if not os.getenv("OPENAI_API_KEY"):
        return {"model": MODERATION_MODEL, "error": "OPENAI_API_KEY is not set"}

    limit = asyncio.Semaphore(MAX_CONCURRENT_POSTS)

    async def moderate(post: dict) -> dict:
        # ctx.run starts each call as its own task run on its own compute.
        async with limit:
            outcome = await ctx.run(moderate_post, post)
        if outcome.get("decision") != "flag":
            return outcome
        # A flagged post goes to the review queue either way. The note only
        # helps the reviewer, so a failed note doesn't change the decision.
        try:
            async with limit:
                note = await ctx.run(write_review_note, post, outcome["reasons"])
        except Exception as e:
            note = {"error": str(e)}
        if "note" in note:
            return {**outcome, "review_note": note["note"]}
        return {**outcome, "note_error": note["error"]}

    # return_exceptions keeps one failed run from failing the batch. A run that
    # raised used up its retries; it's listed as an error.
    outcomes = await asyncio.gather(*(moderate(p) for p in posts), return_exceptions=True)

    report = {
        "model": MODERATION_MODEL,
        "policy": policy,
        "counts": {"approve": 0, "flag": 0, "reject": 0, "error": 0},
        "flagged": [],
        "rejected": [],
        "approved": [],
        "errors": [],
    }
    for post, outcome in zip(posts, outcomes):
        post_id = post.get("id", "unknown")
        if isinstance(outcome, BaseException):
            outcome = {"post_id": post_id, "error": str(outcome)}
        decision = outcome.get("decision", "error")
        report["counts"][decision] += 1
        if decision == "flag":
            report["flagged"].append({**outcome, "author": post.get("author"), "text": post.get("text")})
        elif decision == "reject":
            report["rejected"].append(outcome)
        elif decision == "approve":
            report["approved"].append(post_id)
        else:
            report["errors"].append(outcome)

    logger.info(f"[BATCH] {report['counts']}")
    return report


if __name__ == "__main__":
    app.start()
