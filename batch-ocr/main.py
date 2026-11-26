"""
Batch OCR on Render Workflows

OCR over many scanned PDFs. ocr_batch fans out one ocr_document run per
document. Each ocr_document run rasterizes its PDF with Poppler (pdftoppm) and
reads every page with Tesseract. Both are system binaries, so this workflow
service builds from the Dockerfile next to this file.

The lesson is error classification:
- Bad input (a corrupt PDF, a tool that exits with an error code, a 404) won't
  get better on a retry, so ocr_document returns it as a failed result.
- A tool killed by a signal (for example, out of memory) or a network error
  might pass on the next attempt, so ocr_document raises and Render retries it.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from render import Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Documents that aren't URLs are looked up in this folder, which the
# Dockerfile copies into the image.
SAMPLES_DIR = Path(__file__).parent / "samples"

# 300 DPI is the resolution Tesseract reads best at.
OCR_DPI = int(os.getenv("OCR_DPI", "300"))
OCR_LANG = os.getenv("OCR_LANG", "eng")
MAX_PAGES = int(os.getenv("OCR_MAX_PAGES", "50"))
MAX_PDF_BYTES = int(os.getenv("OCR_MAX_PDF_MB", "50")) * 1024 * 1024

# ocr_document runs on the 2c-4g plan (2 CPUs), so it OCRs two pages at a time.
# The Dockerfile sets OMP_THREAD_LIMIT=1 so each Tesseract process uses one CPU.
PAGE_WORKERS = int(os.getenv("OCR_PAGE_WORKERS", "2"))

# How many ocr_document runs ocr_batch starts at once. Chained runs are capped
# per workspace and fail instead of queueing when you go over, so the batch
# starts one chunk, waits for it, then starts the next.
CHUNK_SIZE = int(os.getenv("BATCH_CHUNK_SIZE", "20"))

app = Workflows()


class PermanentError(Exception):
    """A failure a retry can't fix. ocr_document returns it instead of raising."""


# ============================================================================
# Fetching a document by reference
# ============================================================================


async def fetch_pdf(doc_ref: str, dest: Path) -> None:
    """Copy the PDF that doc_ref points to into dest.

    doc_ref is an https:// URL (for example, a presigned object storage URL)
    or the name of a file in samples/. Task arguments are capped at 4 MB, so
    documents travel by reference, never as bytes.
    """
    if urlparse(doc_ref).scheme in ("http", "https"):
        await download(doc_ref, dest)
        return

    path = (SAMPLES_DIR / doc_ref).resolve()
    if not path.is_relative_to(SAMPLES_DIR.resolve()) or not path.is_file():
        raise PermanentError(f"No such sample document: {doc_ref}")
    dest.write_bytes(path.read_bytes())


async def download(url: str, dest: Path) -> None:
    # A connection error or timeout raises httpx.TransportError, which this
    # function lets through: the network might be back on the next attempt.
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            if response.status_code == 429 or response.status_code >= 500:
                # The server is busy or broken right now. Retry.
                response.raise_for_status()
            if response.status_code >= 400:
                # 403, 404 and friends: the reference is wrong or expired.
                raise PermanentError(f"Download failed with HTTP {response.status_code}")

            size = 0
            with dest.open("wb") as f:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_PDF_BYTES:
                        raise PermanentError(
                            f"Document is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB"
                        )
                    f.write(chunk)


# ============================================================================
# Running Poppler and Tesseract
# ============================================================================


async def run_tool(*args: str) -> str:
    """Run a command-line tool and classify how it failed.

    Returns stdout. Raises PermanentError if the tool is missing or exits with
    an error code, and RuntimeError if a signal killed it.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        # A setup problem: the image doesn't have the tool. Retrying won't install it.
        raise PermanentError(f"{args[0]} is not installed")

    stdout, stderr = await proc.communicate()
    if proc.returncode < 0:
        # Killed by a signal, for example SIGKILL from the out-of-memory killer.
        # The input may be fine, so raise and let Render retry the document.
        raise RuntimeError(f"{args[0]} was killed by signal {-proc.returncode}")
    if proc.returncode > 0:
        # The tool ran and rejected the input. The same input fails the same way.
        message = stderr.decode(errors="replace").strip().splitlines()
        detail = message[-1] if message else "no error output"
        raise PermanentError(f"{args[0]} exited with code {proc.returncode}: {detail}")
    return stdout.decode(errors="replace")


def page_count(pdfinfo_output: str) -> int:
    for line in pdfinfo_output.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    raise PermanentError("pdfinfo didn't report a page count")


async def ocr_pdf(pdf: Path, workdir: Path) -> list[str]:
    """Return the OCR text of each page of pdf."""
    pages = page_count(await run_tool("pdfinfo", str(pdf)))
    if pages > MAX_PAGES:
        raise PermanentError(f"Document has {pages} pages, more than the limit of {MAX_PAGES}")

    # Rasterize every page to a grayscale PNG: page-1.png, page-2.png, ...
    await run_tool("pdftoppm", "-r", str(OCR_DPI), "-gray", "-png", str(pdf), str(workdir / "page"))
    images = sorted(workdir.glob("page-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))

    limit = asyncio.Semaphore(PAGE_WORKERS)

    async def read_page(image: Path) -> str:
        async with limit:
            text = await run_tool("tesseract", str(image), "stdout", "-l", OCR_LANG)
        # Tesseract ends each page with a form feed. Drop it and the blank lines.
        return text.strip()

    return list(await asyncio.gather(*(read_page(image) for image in images)))


# ============================================================================
# Tasks
# ============================================================================


# ocr_document runs Poppler and Tesseract, which need more CPU than flex's one.
# It retries twice, but only for the errors it raises: a tool killed by a
# signal, or a network error while downloading. Reading a file and running OCR
# have no side effects, so a retry is safe.
@app.task(
    plan="2c-4g",
    retry=Retry(max_retries=2, wait_duration_ms=2000, backoff_scaling=2),
    timeout_seconds=600,
)
async def ocr_document(ctx: TaskContext, doc_ref: str) -> dict:
    """OCR one scanned PDF and return the text of each page."""
    logger.info(f"[OCR] {doc_ref}")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        pdf = workdir / "input.pdf"
        try:
            await fetch_pdf(doc_ref, pdf)
            text = await ocr_pdf(pdf, workdir)
        except PermanentError as e:
            logger.warning(f"[OCR] {doc_ref}: {e}. Not retrying")
            return {"doc_ref": doc_ref, "error": str(e)}

    chars = sum(len(page) for page in text)
    logger.info(f"[OCR] {doc_ref}: {len(text)} pages, {chars} characters")
    return {"doc_ref": doc_ref, "pages": len(text), "chars": chars, "text": text}


# ocr_batch only fans out, so it doesn't retry: a retry would rerun every
# document, including the ones that already finished. Each ocr_document run
# retries on its own.
#
# Each attempt gets the full timeout. Size it for the whole batch: every chunk
# lasts as long as its slowest document, including that document's retries.
@app.task(
    plan="flex",
    retry=Retry(max_retries=0, wait_duration_ms=1000, backoff_scaling=1.5),
    timeout_seconds=3600,
)
async def ocr_batch(ctx: TaskContext, doc_refs: list[str]) -> dict:
    """OCR many documents, one ocr_document run each, a chunk at a time."""
    logger.info(f"[BATCH] {len(doc_refs)} documents, chunks of {CHUNK_SIZE}")

    succeeded, failed = [], []
    for start in range(0, len(doc_refs), CHUNK_SIZE):
        chunk = doc_refs[start : start + CHUNK_SIZE]
        # ctx.run starts each document as its own task run on its own compute.
        # return_exceptions keeps one failed document from failing the batch.
        results = await asyncio.gather(
            *(ctx.run(ocr_document, doc_ref) for doc_ref in chunk),
            return_exceptions=True,
        )
        for doc_ref, result in zip(chunk, results):
            if isinstance(result, BaseException):
                # ocr_document raised on every attempt (or the run couldn't start).
                failed.append({"doc_ref": doc_ref, "error": str(result)})
            elif "error" in result:
                failed.append(result)
            else:
                succeeded.append(result)

    logger.info(f"[BATCH] {len(succeeded)} succeeded, {len(failed)} failed")
    return {
        "total": len(doc_refs),
        "succeeded": len(succeeded),
        "pages": sum(r["pages"] for r in succeeded),
        "failed": failed,
        "results": succeeded,
    }


if __name__ == "__main__":
    app.start()
