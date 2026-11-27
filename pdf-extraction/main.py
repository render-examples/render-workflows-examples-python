"""
PDF Extraction on Render Workflows

Extract structured invoice fields from text-layer PDFs with an LLM, inside a
workflow task. The task reads each page's text, asks the model for a strict
JSON schema, re-prompts a bounded number of times when the answer doesn't
check out, and runs business checks. Anything a human should look at comes
back as an exception, never as a silently empty field.

Scanned PDFs have no text layer. Run those through OCR first (see the
batch-ocr example).
"""

import asyncio
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import date
from io import BytesIO
from pathlib import Path

from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from render import Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Read the model from the environment so a retired model is a config change,
# not a code change.
MODEL = os.getenv("OPENAI_MODEL", "gpt-6.2-sol")

# How many times to ask the model per task attempt: the first ask plus
# re-prompts when the answer fails validation.
MAX_MODEL_ATTEMPTS = int(os.getenv("EXTRACTION_MAX_ATTEMPTS", "3"))

# Refuse to download more than this. Large files belong in object storage,
# passed to the task as a URL.
MAX_PDF_BYTES = 20 * 1024 * 1024

# OpenAI errors that mean the request itself is wrong: unknown model, bad key,
# invalid input. A retry can't fix them, so the task returns them as a result.
PERMANENT_ERROR_STATUSES = {400, 401, 403, 404, 422}

# Fields every invoice must have. If the model can't find one on any page,
# the result carries a missing_field exception for it.
REQUIRED_FIELDS = ["invoice_number", "vendor_name", "invoice_date", "total"]

# Sources that aren't URLs are read from this folder, which holds the sample
# invoices and ships with the service.
BASE_DIR = Path(__file__).resolve().parent

app = Workflows()


# ============================================================================
# The schema the model fills in. Every field carries the page it came from.
# ============================================================================


class TextField(BaseModel):
    value: str | None = Field(description="The value as printed, or null if absent.")
    page: int | None = Field(description="1-based page the value is on, or null if absent.")


class DateField(BaseModel):
    value: str | None = Field(description="The date as YYYY-MM-DD, or null if absent.")
    page: int | None = Field(description="1-based page the value is on, or null if absent.")


class AmountField(BaseModel):
    value: float | None = Field(description="The amount as a number, or null if absent.")
    page: int | None = Field(description="1-based page the value is on, or null if absent.")


class LineItem(BaseModel):
    description: str
    quantity: float | None
    unit_price: float | None
    amount: float = Field(description="The line total.")
    page: int = Field(description="1-based page the line is on.")


class Invoice(BaseModel):
    invoice_number: TextField
    vendor_name: TextField = Field(description="Who issued the invoice.")
    customer_name: TextField = Field(description="Who the invoice is billed to.")
    invoice_date: DateField
    due_date: DateField
    currency: TextField = Field(description="ISO 4217 code, such as USD or EUR.")
    subtotal: AmountField = Field(description="Total of the line items before tax and shipping.")
    tax: AmountField
    shipping: AmountField = Field(description="Shipping, freight or delivery charges.")
    total: AmountField = Field(description="The amount due.")
    line_items: list[LineItem] = Field(description="Every line item, across all pages.")


INSTRUCTIONS = """\
You extract invoice data from the text layer of a PDF. The text of each page
follows a "=== Page N ===" marker.

- Copy values from the document. Never guess or compute a value that isn't printed.
- If a field isn't in the document, set its value and page to null.
- Set page to the page number where you found the value.
- Write dates as YYYY-MM-DD and currencies as ISO 4217 codes.
- Write amounts as plain numbers, without currency symbols or thousands separators.
- Include every line item from every page, in order.
"""


# ============================================================================
# Reading the PDF
# ============================================================================


class PermanentError(Exception):
    """A problem with the input that a retry can't fix."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _download(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read(MAX_PDF_BYTES + 1)
    except urllib.error.HTTPError as e:
        # 408 and 429 can pass on their own, and so can 5xx: raise so the task retries.
        if 400 <= e.code < 500 and e.code not in (408, 429):
            raise PermanentError("fetch_failed", f"GET {url} returned {e.code}") from e
        raise
    if len(data) > MAX_PDF_BYTES:
        raise PermanentError("too_large", f"{url} is larger than {MAX_PDF_BYTES} bytes")
    return data


async def load_pdf(source: str) -> bytes:
    """Fetch the PDF a document reference points to."""
    if source.startswith(("https://", "http://")):
        return await asyncio.to_thread(_download, source)

    path = (BASE_DIR / source).resolve()
    if not path.is_relative_to(BASE_DIR) or not path.is_file():
        raise PermanentError("source_not_found", f"No file at {source}")
    return path.read_bytes()


def read_pages(pdf_bytes: bytes) -> list[str]:
    """Return the text layer of each page."""
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        return [page.extract_text() or "" for page in reader.pages]
    except PyPdfError as e:
        raise PermanentError("unreadable_pdf", f"Can't read the PDF: {e}") from e


# ============================================================================
# Validation and business checks
# ============================================================================


def exception(code: str, field: str | None, message: str) -> dict:
    return {"code": code, "field": field, "message": message}


def squash(text: str) -> str:
    """Lowercase and drop whitespace, for loose text matching."""
    return re.sub(r"\s+", "", text).lower()


def is_iso_date(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def find_problems(invoice: Invoice, pages: list[str]) -> list[str]:
    """Problems with the model's answer that a re-prompt can fix."""
    problems = []
    page_count = len(pages)
    fields = {name: getattr(invoice, name) for name in Invoice.model_fields if name != "line_items"}

    for name, field in fields.items():
        if field.value is None:
            continue
        if field.page is None or not 1 <= field.page <= page_count:
            problems.append(f"{name}.page must be between 1 and {page_count}")
        if isinstance(field, DateField) and not is_iso_date(field.value):
            problems.append(f"{name} must be a YYYY-MM-DD date, got {field.value!r}")

    if invoice.currency.value is not None and not re.fullmatch(r"[A-Z]{3}", invoice.currency.value):
        problems.append(f"currency must be an ISO 4217 code, got {invoice.currency.value!r}")

    # The invoice number must be printed on the page the model cites.
    number = invoice.invoice_number
    if number.value is not None and number.page is not None and 1 <= number.page <= page_count:
        if squash(number.value) not in squash(pages[number.page - 1]):
            problems.append(
                f"invoice_number {number.value!r} does not appear on page {number.page}. "
                "Copy it exactly, or set it to null if the document has none."
            )

    for i, item in enumerate(invoice.line_items):
        if not 1 <= item.page <= page_count:
            problems.append(f"line_items[{i}].page must be between 1 and {page_count}")

    return problems


def close(a: float, b: float) -> bool:
    return abs(a - b) < 0.01


def business_checks(invoice: Invoice) -> list[dict]:
    """Checks a re-prompt can't fix. Each failure is an exception for review."""
    found = []

    for name in REQUIRED_FIELDS:
        if getattr(invoice, name).value is None:
            found.append(exception("missing_field", name, f"No {name} on any page"))
    if not invoice.line_items:
        found.append(exception("missing_field", "line_items", "No line items on any page"))

    for i, item in enumerate(invoice.line_items):
        if item.quantity is not None and item.unit_price is not None:
            if not close(item.quantity * item.unit_price, item.amount):
                found.append(
                    exception(
                        "line_amount_mismatch",
                        f"line_items[{i}]",
                        f"{item.quantity} x {item.unit_price} is not {item.amount}",
                    )
                )

    subtotal = invoice.subtotal.value
    if subtotal is not None and invoice.line_items:
        line_sum = round(sum(item.amount for item in invoice.line_items), 2)
        if not close(line_sum, subtotal):
            found.append(
                exception(
                    "subtotal_mismatch",
                    "subtotal",
                    f"Line items add up to {line_sum}, the invoice says {subtotal}",
                )
            )

    total = invoice.total.value
    if subtotal is not None and total is not None:
        expected = round(subtotal + (invoice.tax.value or 0) + (invoice.shipping.value or 0), 2)
        if not close(expected, total):
            found.append(
                exception(
                    "total_mismatch",
                    "total",
                    f"Subtotal + tax + shipping is {expected}, the invoice says {total}",
                )
            )

    issued, due = invoice.invoice_date.value, invoice.due_date.value
    if issued and due and due < issued:
        found.append(exception("due_before_issue", "due_date", f"Due {due}, issued {issued}"))

    return found


# ============================================================================
# Talking to the model
# ============================================================================


def format_pages(pages: list[str]) -> str:
    return "\n\n".join(f"=== Page {n} ===\n{text}" for n, text in enumerate(pages, start=1))


async def extract_invoice(client: AsyncOpenAI, document_id: str, pages: list[str]):
    """Ask the model for the invoice, re-prompting on answers that fail validation.

    Returns the last invoice the model produced (or None) and the problems it
    still had after the last attempt.
    """
    conversation = [{"role": "user", "content": format_pages(pages)}]
    invoice, problems = None, []

    for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
        try:
            response = await client.responses.parse(
                model=MODEL,
                instructions=INSTRUCTIONS,
                input=conversation,
                text_format=Invoice,
            )
            invoice = response.output_parsed
            problems = (
                find_problems(invoice, pages)
                if invoice
                else ["The response held no invoice. Return the invoice as JSON."]
            )
        except ValidationError as e:
            # The output didn't match the schema (for example, it was cut off).
            problems = [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]

        if not problems:
            logger.info(f"[EXTRACT] {document_id}: valid answer on attempt {attempt}")
            return invoice, []

        logger.warning(f"[EXTRACT] {document_id}: attempt {attempt} had {len(problems)} problems")
        conversation.append(
            {
                "role": "user",
                "content": "Your answer had these problems:\n- "
                + "\n- ".join(problems)
                + "\nExtract the invoice again and fix them.",
            }
        )

    return invoice, problems


# ============================================================================
# Task
# ============================================================================


# extract_document calls OpenAI and may download the PDF, so it retries.
# A retry reruns every model call, which is safe because nothing here writes.
# The caller saves the result.
@app.task(
    retry=Retry(max_retries=2, wait_duration_ms=2000, backoff_scaling=2),
    timeout_seconds=300,
)
async def extract_document(ctx: TaskContext, doc_ref: dict) -> dict:
    """Extract invoice fields from one text-layer PDF.

    doc_ref is {"document_id": ..., "source": ...}, where source is an http(s)
    URL or a path inside this folder. Pass references, not file contents:
    a task run's arguments are capped at 4 MB.
    """
    doc_ref = doc_ref if isinstance(doc_ref, dict) else {}
    document_id = doc_ref.get("document_id")
    source = doc_ref.get("source")

    def failed(code: str, message: str) -> dict:
        # A problem a retry can't fix: return it as a result instead of raising.
        logger.error(f"[EXTRACT] {document_id}: {code}: {message}")
        return {
            "document_id": document_id,
            "record": None,
            "exceptions": [exception(code, None, message)],
        }

    if not document_id or not source:
        return failed("bad_input", "The input needs a document_id and a source")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return failed("config_error", "OPENAI_API_KEY is not set")

    logger.info(f"[EXTRACT] {document_id}: reading {source}")
    try:
        pages = read_pages(await load_pdf(source))
    except PermanentError as e:
        return failed(e.code, str(e))

    if not any(text.strip() for text in pages):
        return failed("no_text_layer", "The PDF has no text layer. Run it through OCR first.")

    # Render retries this task, so turn off the OpenAI client's own retries to
    # keep one retry policy in charge.
    client = AsyncOpenAI(api_key=api_key, max_retries=0)
    try:
        invoice, problems = await extract_invoice(client, document_id, pages)
    except APIStatusError as e:
        if e.status_code in PERMANENT_ERROR_STATUSES:
            return failed("llm_error", f"OpenAI error {e.status_code}: {e.message}")
        raise

    # Problems left after the last re-prompt go to review with the rest.
    exceptions = [exception("invalid_output", None, p) for p in problems]
    if invoice is None:
        return {"document_id": document_id, "record": None, "exceptions": exceptions}

    exceptions += business_checks(invoice)
    logger.info(
        f"[EXTRACT] {document_id}: {len(invoice.line_items)} line items, "
        f"{len(exceptions)} exceptions"
    )
    return {
        "document_id": document_id,
        "record": invoice.model_dump(),
        "exceptions": exceptions,
    }


if __name__ == "__main__":
    app.start()
