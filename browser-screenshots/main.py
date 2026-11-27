"""
Browser screenshots on Render Workflows

Checks many web pages with headless Chromium (Playwright). For each URL, one
task run loads the page and records its HTTP status, title, load time, console
errors and broken images, then takes a screenshot. An orchestrator fans out
one run per URL, with a cap on how many run at once.

Chromium runs on Render's native Python runtime: the build command downloads
Playwright's headless Chromium into the project, so there's no Docker image.

To stay self-contained, each run returns a small JPEG thumbnail (base64) and
discards the full screenshot. A real app would upload the full screenshot to
object storage such as S3 in store_screenshot().
"""

import os

# Look for the browser inside the installed playwright package, where the build
# command put it ("PLAYWRIGHT_BROWSERS_PATH=0 playwright install"). The default
# location, ~/.cache, isn't kept from the build to task runs on Render.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

import asyncio
import base64
import logging
import time
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from render import Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# How many check_page runs check_pages keeps going at once. Render has no
# per-task concurrency setting, so the cap lives in code. It also keeps you
# from flooding the sites you check.
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "5"))
# The most URLs one check_pages run accepts. Every page's thumbnail comes back
# in the batch result (about 10-20 KB each), so this keeps results small.
MAX_URLS = int(os.getenv("MAX_URLS", "100"))
# How long a page may take to fire its load event, per attempt.
NAVIGATION_TIMEOUT_MS = int(os.getenv("NAVIGATION_TIMEOUT_MS", "30000"))

VIEWPORT = {"width": 1280, "height": 800}
THUMBNAIL_WIDTH = 320
# Keep at most this many console errors and broken image URLs per page.
MAX_LISTED = 10

# Navigation errors a retry won't fix. Anything else (timeouts, connection
# resets, a crashed browser) is raised so Render retries the run.
PERMANENT_NET_ERRORS = (
    "net::ERR_NAME_NOT_RESOLVED",
    "net::ERR_CERT_",
    "net::ERR_SSL_",
    "net::ERR_BAD_SSL_CLIENT_AUTH_CERT",
    "net::ERR_INVALID_URL",
    "net::ERR_UNSAFE_PORT",
    "net::ERR_TOO_MANY_REDIRECTS",
    "net::ERR_ABORTED",
)

app = Workflows()


class PermanentError(Exception):
    """A failure a retry can't fix. check_page returns it instead of raising."""


# ============================================================================
# Helpers
# ============================================================================


def check_url(url: object) -> str:
    """Return url if it's an absolute http(s) URL, or raise PermanentError."""
    if not isinstance(url, str) or not url.strip():
        raise PermanentError("Each URL must be a non-empty string")
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise PermanentError("URL must start with http:// or https://")
    return url


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# Runs in a blank page. Draws the PNG screenshot onto a canvas THUMBNAIL_WIDTH
# px wide and returns it as base64 JPEG. Chromium is already running, so it
# makes the thumbnail too: no image library needed.
THUMBNAIL_JS = """
async ([png, width]) => {
  const img = new Image();
  img.src = "data:image/png;base64," + png;
  await img.decode();
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = Math.round((img.height * width) / img.width);
  const draw = canvas.getContext("2d");
  draw.imageSmoothingQuality = "high";
  draw.drawImage(img, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.7).split(",")[1];
}
"""


async def store_screenshot(context, url: str, png: bytes) -> dict:
    """Decide what to keep of the full screenshot before the run ends.

    This example keeps only a small JPEG thumbnail, returned as base64, and
    discards the full PNG.

    In a real app, upload png here instead, for example with boto3:
        key = f"screenshots/{hashlib.sha256(url.encode()).hexdigest()}.png"
        s3.put_object(Bucket=BUCKET, Key=key, Body=png, ContentType="image/png")
    and return the key. A key derived from the URL means a retry overwrites
    its earlier upload instead of leaving a duplicate.
    """
    # A fresh blank page, so the checked site's Content-Security-Policy can't
    # block the data: URL.
    page = await context.new_page()
    try:
        thumbnail = await page.evaluate(THUMBNAIL_JS, [base64.b64encode(png).decode(), THUMBNAIL_WIDTH])
    finally:
        await page.close()
    return {"screenshot_bytes": len(png), "thumbnail_jpeg_base64": thumbnail}


async def load_and_inspect(browser, url: str) -> dict:
    """Load url in a new browser context and collect what the checks need."""
    context = await browser.new_context(viewport=VIEWPORT)
    try:
        page = await context.new_page()

        console_errors: list[str] = []
        broken_images: set[str] = set()

        def on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text[:300])

        def on_page_error(error):
            console_errors.append(f"Uncaught: {error}"[:300])

        # An image is broken if its request failed or got an HTTP error.
        def on_request_failed(request):
            if request.resource_type == "image":
                broken_images.add(request.url[:300])

        def on_response(response):
            if response.request.resource_type == "image" and response.status >= 400:
                broken_images.add(response.url[:300])

        page.on("console", on_console)
        page.on("pageerror", on_page_error)
        page.on("requestfailed", on_request_failed)
        page.on("response", on_response)

        started = time.monotonic()
        try:
            response = await page.goto(url, wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            raise  # maybe a slow moment: let Render retry
        except PlaywrightError as e:
            message = str(e).splitlines()[0]
            if any(code in message for code in PERMANENT_NET_ERRORS):
                raise PermanentError(message) from e
            raise
        load_ms = round((time.monotonic() - started) * 1000)

        status = response.status if response else None
        # 5xx and 429 often pass. Raise so Render retries the run.
        if status is not None and (status >= 500 or status == 429):
            raise RuntimeError(f"HTTP {status} from {url}")

        final_url = page.url
        title = await page.title()
        png = await page.screenshot(type="png")
        stored = await store_screenshot(context, url, png)
    finally:
        await context.close()

    problems = []
    if status is None or status >= 400:
        problems.append(f"HTTP {status}")
    if console_errors:
        problems.append(plural(len(console_errors), "console error"))
    if broken_images:
        problems.append(plural(len(broken_images), "broken image"))
    return {
        "url": url,
        "final_url": final_url,
        "ok": not problems,
        "problems": problems,
        "status": status,
        "title": title,
        "load_ms": load_ms,
        "console_error_count": len(console_errors),
        "console_errors": console_errors[:MAX_LISTED],
        "broken_image_count": len(broken_images),
        "broken_images": sorted(broken_images)[:MAX_LISTED],
        **stored,
    }


# ============================================================================
# Tasks
# ============================================================================


# check_page loads one URL in its own headless Chromium. Chromium fits in flex
# (up to 1 CPU and 4 GB, billed for what the run uses); move it to 2c-4g if
# your pages are heavy. It retries twice for errors that may pass: timeouts,
# dropped connections, 5xx responses. Loading a page has no side effects on
# our side, so a retry is safe. Each attempt may take up to 2 minutes.
@app.task(
    plan="flex",
    timeout_seconds=120,
    retry=Retry(max_retries=2, wait_duration_ms=5000, backoff_scaling=2),
)
async def check_page(ctx: TaskContext, url: str) -> dict:
    """Load one page, check it, and return the results with a thumbnail."""
    try:
        url = check_url(url)
    except PermanentError as e:
        return {"url": url, "ok": False, "error": str(e)}

    logger.info(f"[CHECK] {url}")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            result = await load_and_inspect(browser, url)
        except PermanentError as e:
            logger.error(f"[CHECK] {url}: {e} (not retrying)")
            return {"url": url, "ok": False, "error": str(e)}
        finally:
            await browser.close()

    logger.info(
        f"[CHECK] {url}: HTTP {result['status']} in {result['load_ms']} ms, "
        f"{result['problems'] or 'no problems'}"
    )
    return result


# check_pages only fans out and waits, so it runs on flex and never retries: a
# retry would recheck every URL, including the ones that finished. Each
# check_page run retries on its own. The timeout covers the whole batch.
@app.task(
    plan="flex",
    timeout_seconds=60 * 60,
    retry=Retry(max_retries=0, wait_duration_ms=1000, backoff_scaling=1.5),
)
async def check_pages(ctx: TaskContext, urls: list[str]) -> dict:
    """Check many pages, one check_page run each, at most MAX_CONCURRENCY at once."""
    if not isinstance(urls, list):
        return {"error": "Input must be a list of URLs"}
    urls = list(dict.fromkeys(urls))  # drop duplicates, keep order
    if len(urls) > MAX_URLS:
        return {"error": f"Got {len(urls)} URLs; split them into batches of at most {MAX_URLS}"}

    logger.info(f"[BATCH] {len(urls)} URLs, at most {MAX_CONCURRENCY} at once")
    limit = asyncio.Semaphore(MAX_CONCURRENCY)

    async def check(url: str) -> dict:
        # A new run starts only when one of the running ones finishes.
        async with limit:
            return await ctx.run(check_page, url)

    # return_exceptions keeps one failed URL from failing the batch.
    results = await asyncio.gather(*(check(url) for url in urls), return_exceptions=True)

    pages, failed = [], []
    for url, result in zip(urls, results):
        if isinstance(result, BaseException):
            # The run raised on every attempt (retries used up).
            failed.append({"url": url, "ok": False, "error": str(result)})
        elif "error" in result:
            failed.append(result)
        else:
            pages.append(result)

    with_problems = [p["url"] for p in pages if not p["ok"]]
    logger.info(f"[BATCH] {len(pages)} checked ({len(with_problems)} with problems), {len(failed)} failed")
    return {
        "total": len(urls),
        "checked": len(pages),
        "with_problems": with_problems,
        "failed": failed,
        "pages": pages,
    }


if __name__ == "__main__":
    app.start()
