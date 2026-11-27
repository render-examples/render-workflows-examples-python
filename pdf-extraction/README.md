# PDF Extraction on Render Workflows

Extract structured invoice fields from text-layer PDFs with an LLM inside a Render Workflows task, and save the results to Postgres.

## What it shows

- Reading a PDF's text layer page by page inside a task, from a reference (a URL or a bundled file), not from file bytes in the task arguments
- Calling OpenAI with a strict JSON schema (Structured Outputs) where every field carries the page it came from
- Re-prompting the model a bounded number of times inside the task when its answer fails validation
- Business checks that turn missing required fields and totals that don't add up into exceptions for review, never silent empty values
- Retrying on network and server errors, and returning errors a retry can't fix (missing API key, unknown model, unreadable PDF) as results
- Triggering one run per document from another service with an idempotency key, and upserting each result into Postgres

## Task tree

```
client.py (your app)                       one run per document, idempotency key per document
  └── extract_document                     (retries twice on network and server errors)
        ├── load_pdf                       URL or bundled file
        ├── read_pages                     pypdf text layer, one string per page
        ├── extract_invoice                OpenAI Structured Outputs, up to 3 asks
        │     └── find_problems            bad dates, pages out of range, invoice number not on its page → re-prompt
        └── business_checks                missing required fields, totals that don't add up → exceptions
  └── upsert into invoice_extractions      (schema.sql)
```

`extract_document` returns:

```json
{
  "document_id": "acm-55017",
  "record": {
    "invoice_number": {"value": "ACM-55017", "page": 1},
    "total": {"value": 2436.13, "page": 3},
    "line_items": [{"description": "Hex bolt M8 x 40, zinc (box of 100)", "quantity": 10, "unit_price": 14.8, "amount": 148.0, "page": 1}, "..."],
    "...": "..."
  },
  "exceptions": []
}
```

Each exception is `{"code", "field", "message"}`. `record` is `null` when nothing could be extracted (for example, a scanned PDF with no text layer).

## Sample invoices

`invoices/` holds five small PDFs, listed in `invoices.json`:

| File | What it tests |
|---|---|
| `nw-2026-0142.pdf` | One page, table layout, dates written out ("November 3, 2026") |
| `bf-88213.pdf` | European layout, EUR and VAT, `dd.mm.yyyy` dates, totals printed before the line items |
| `acm-55017.pdf` | Three pages: 28 line items across pages 1 and 2, totals on page 3 |
| `pc-3390.pdf` | The printed total doesn't equal subtotal + tax: `total_mismatch` |
| `harbor-statement-oct.pdf` | A statement with no invoice number and no line items: two `missing_field` exceptions |

## Prerequisites

- Python 3.11+
- The [Render CLI](https://render.com/docs/cli)
- An [OpenAI API key](https://platform.openai.com/api-keys)
- For `client.py`: a Render API key and a Postgres database

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set OPENAI_API_KEY. OPENAI_MODEL defaults to gpt-6.2-sol.
```

Start the local task server. It loads `.env` when it starts:

```bash
render workflows dev -- python main.py
```

In another terminal, extract the three-page invoice. Task input is a JSON array of the task's arguments:

```bash
render workflows tasks runs start extract_document --local \
  --input='[{"document_id": "acm-55017", "source": "invoices/acm-55017.pdf"}]'
```

Try `invoices/harbor-statement-oct.pdf` to see missing fields come back as exceptions. `source` can also be an `https://` URL, such as a presigned object storage URL.

### Save results to a local Postgres

`client.py` can talk to the local task server instead of Render. Start a Postgres, create the table, and run the client:

```bash
docker run -d --name invoices-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:18
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres
psql "$DATABASE_URL" -f schema.sql

RENDER_LOCAL_DEV_URL=http://localhost:8120 python client.py
psql "$DATABASE_URL" -c "SELECT document_id, exceptions FROM invoice_extractions"
```

## Deploy to Render

1. Push this folder to a Git repository Render can access.
2. In the Render Dashboard, click **New > Workflow** and connect the repository.
3. Set these fields:

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `pdf-extraction` (the folder that holds `main.py`). Leave it empty if this folder is the repository root. |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python main.py` |
   | Region | Any. Pick the region of your Postgres database if the client runs on Render too. |
   | Environment variables | `OPENAI_API_KEY`, and optionally `OPENAI_MODEL` and `EXTRACTION_MAX_ATTEMPTS` |

4. Create the workflow. Once the first version is ready, run `extract_document` from the Dashboard with an entry from `invoices.json` wrapped in a JSON array. The sample invoices ship with the service, so their relative paths work on Render.

The workflow service doesn't connect to Postgres. Your app does, after each run finishes. To create a database, see [Render Postgres](https://render.com/docs/postgresql-creating-connecting).

## Trigger from another service

`client.py` starts one run per document in `invoices.json`, waits for them, and upserts each result:

```bash
export RENDER_API_KEY=your-render-api-key
export WORKFLOW_SLUG=your-workflow-slug   # defaults to pdf-extraction
export DATABASE_URL=your-postgres-url
psql "$DATABASE_URL" -f schema.sql
python client.py
```

The core of it:

```python
from render import RenderAsync

render = RenderAsync()  # reads RENDER_API_KEY

run = await render.workflows.run_task(
    f"{SLUG}/extract_document",
    [doc],
    idempotency_key=f"extract-{doc['document_id']}",
)
result = run.results[0]  # results is a list holding the return value
conn.execute(UPSERT, (result["document_id"], Jsonb(result["record"]), Jsonb(result["exceptions"]), run.id))
```

The table (`schema.sql`) has one row per `document_id`, so extracting a document again updates its row.

## Limits and gotchas

- **A retry reruns every model call.** `extract_document` retries twice on network errors, timeouts, rate limits and OpenAI 5xx errors. Each attempt starts over: it downloads the PDF and asks the model again. That's safe because the task doesn't write anything; the caller saves the result. See [retries](https://render.com/docs/workflows-defining-tasks#retry-settings).
- **Re-prompts happen inside one attempt.** When the model's answer fails validation, the task asks again with the problems listed, up to `EXTRACTION_MAX_ATTEMPTS` (default 3) times. Problems left after the last ask come back as `invalid_output` exceptions. With Structured Outputs the answer always matches the JSON schema, so re-prompts catch what a schema can't express: dates that aren't `YYYY-MM-DD`, page numbers past the end of the document, an invoice number that isn't printed on the page the model cites.
- **Timeouts apply per attempt.** `timeout_seconds=300` covers every model call in one attempt, and a retry gets a fresh 300 seconds. See [timeouts](https://render.com/docs/workflows-defining-tasks#timeout).
- **Pass references, not files.** The combined arguments of a task run can't exceed 4 MB, so pass a URL or a path. The task refuses PDFs over 20 MB. See [limits](https://render.com/docs/workflows-limits).
- **Idempotency keys last 24 hours and keep failures too.** Within 24 hours, the same key returns the original run, even one that failed. To extract a document again sooner, change the key. Keys are scoped to the workflow service. The local task server doesn't apply them. See [idempotency keys](https://render.com/docs/workflows-running#idempotency-keys).
- **Render keeps run results for 30 days.** Save what you need to your own database, as `client.py` does. See [state retention](https://render.com/docs/workflows-limits#state-retention).
- **No OCR.** Scanned PDFs have no text layer and come back as `no_text_layer`. Run them through OCR first.
- **Errors a retry can't fix are returned, not raised.** A missing `OPENAI_API_KEY`, an OpenAI 400/401/403/404/422, a source that returns 404, or a file that isn't a PDF comes back as `record: null` with one exception.

## Links

- [Render Workflows](https://render.com/docs/workflows)
- [Defining tasks](https://render.com/docs/workflows-defining-tasks)
- [Triggering runs](https://render.com/docs/workflows-running)
- [Workflows SDK for Python](https://render.com/docs/workflows-sdk-python)
- [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)
- [pypdf](https://pypdf.readthedocs.io/)
