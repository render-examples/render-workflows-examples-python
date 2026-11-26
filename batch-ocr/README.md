# Batch OCR on Render Workflows

Read the text out of many scanned PDFs with [Tesseract](https://github.com/tesseract-ocr/tesseract) and [Poppler](https://poppler.freedesktop.org/), one task run per document, on a Docker-based workflow service.

## What it shows

- A workflow service that builds from a `Dockerfile`, because Tesseract and Poppler are system binaries
- A small orchestrator task on `flex` and a CPU-heavy OCR task on `2c-4g`
- Passing documents by reference (a URL or a file name), not as bytes
- Classifying errors: bad input is returned as a failed result, a killed process or a network error is raised so Render retries it
- Fanning out in chunks with `ctx.run` and `asyncio.gather`, without failing the batch on one document
- Triggering the tasks from another service with the Render SDK client

## Task tree

```
ocr_batch            (flex, fans out one chunk at a time, no retries)
  └── ocr_document   (2c-4g, one per document, retries twice)
        ├── fetch the PDF        ← https:// URL, or a file in samples/
        ├── pdfinfo              ← page count
        ├── pdftoppm             ← one PNG per page
        └── tesseract            ← text of each page, two pages at a time
```

How `ocr_document` handles each failure:

| What happened | Example | What the task does |
|---|---|---|
| The input is bad | Corrupt PDF, a tool exits with code > 0, HTTP 404, too many pages | Returns `{"doc_ref", "error"}`. No retry |
| The tool isn't installed | `tesseract` missing when you run outside Docker | Returns `{"doc_ref", "error"}`. No retry |
| A tool was killed by a signal | The out-of-memory killer sends SIGKILL to `tesseract` | Raises. Render retries the run |
| The network failed | DNS error, connection reset, timeout, HTTP 429 or 5xx | Raises. Render retries the run |

A task has no "don't retry this error" option: any exception it raises uses up an attempt. So errors a retry can't fix are returned as results instead.

## Prerequisites

- Python 3.11+
- The [Render CLI](https://render.com/docs/cli)
- Tesseract and Poppler, to run locally (`brew install tesseract poppler`, or `apt-get install tesseract-ocr poppler-utils`). On Render, the `Dockerfile` installs them.
- No API keys

## Run locally

Render's local task server doesn't build Dockerfiles, so run the service with the tools installed on your machine:

```bash
brew install tesseract poppler
pip install -r requirements.txt
render workflows dev -- python main.py
```

In another terminal, OCR one document. Task input is a JSON array of the task's arguments, and a document is a file name in `samples/` or an `https://` URL:

```bash
render workflows tasks runs start ocr_document --local --input='["invoice-1042.pdf"]'
```

OCR the four samples at once. `corrupt.pdf` isn't a valid PDF, so it comes back under `failed` after one attempt:

```bash
render workflows tasks runs start ocr_batch --local \
  --input='[["invoice-1042.pdf", "delivery-receipt-2207.pdf", "plan-amendment.pdf", "corrupt.pdf"]]'
```

To see a retry, OCR a URL that can't resolve. The run raises a network error on all three attempts, then fails:

```bash
render workflows tasks runs start ocr_document --local --input='["https://scans.example.invalid/invoice.pdf"]'
```

Optional settings, with their defaults, are in `.env.example`. Copy it to `.env` to change them.

## Deploy to Render

Docker-based workflow services can only be created in the Render Dashboard, not with the CLI or API. They build from a Dockerfile in your repository; they can't pull a prebuilt image from a registry.

1. Push this folder to a Git repository Render can access.
2. In the Render Dashboard, click **New > Workflow** and connect the repository.
3. Set these fields:

   | Field | Value |
   |---|---|
   | Language | Docker |
   | Root Directory | `batch-ocr` (the folder that holds the `Dockerfile`). Leave it empty if this folder is the repository root. |
   | Dockerfile Path | `./Dockerfile` |
   | Start Command | `python main.py` (required for Docker-based workflows, even though the `Dockerfile` has a `CMD`) |
   | Region | Any |
   | Environment variables | None required. Optionally the settings in `.env.example` |

   There's no Build Command for Docker-based workflows: the `Dockerfile` does the build.

4. Create the workflow. Once the first version is ready, run `ocr_batch` from the Dashboard with `[["invoice-1042.pdf", "delivery-receipt-2207.pdf", "plan-amendment.pdf", "corrupt.pdf"]]`.

`ocr_document` runs on the `2c-4g` plan, which bills per second of each run. See [compute plans](https://render.com/docs/workflows-limits#compute-plans).

## Trigger from another service

`client.py` triggers both tasks with the Render SDK:

```bash
export RENDER_API_KEY=your-render-api-key
export WORKFLOW_SLUG=your-workflow-slug   # defaults to batch-ocr
python client.py
```

The core of it:

```python
from render import Render

render = Render()  # reads RENDER_API_KEY

run = render.workflows.run_task(f"{SLUG}/ocr_batch", [["invoice-1042.pdf", "https://..."]])
if run.status == "completed":
    batch = run.results[0]  # results is a list holding the return value
    print(batch["succeeded"], batch["failed"])
```

In your own app, upload each scan to object storage and pass a presigned `https://` URL. `run_task` waits for the run to finish. To start a run and check on it later, use `start_task` instead.

## Limits and gotchas

- **Pass references, not files.** The combined arguments of a task run can't exceed 4 MB, so pass URLs or keys, not PDF bytes. See [limits](https://render.com/docs/workflows-limits#additional-limits).
- **Results stay on Render for 30 days.** `ocr_document` returns the text of every page, and `ocr_batch` returns all of it. For large documents, write the text to your own storage and return its key. See [state retention](https://render.com/docs/workflows-limits#state-retention).
- **Chained runs are rate limited.** A workspace can start 1,000 chained runs per minute. Over that, `ctx.run` fails instead of queueing. `ocr_batch` starts `BATCH_CHUNK_SIZE` runs (default 20), waits for them, then starts the next chunk. A document whose run couldn't start is listed under `failed`. See [rate limits](https://render.com/docs/workflows-limits#chained-runs).
- **Timeouts apply per attempt.** Each attempt of `ocr_document` gets 600 seconds and each retry gets a fresh 600. `ocr_batch` gets 3,600 seconds for the whole batch: every chunk lasts as long as its slowest document, retries included. Raise it for bigger batches. See [timeouts](https://render.com/docs/workflows-defining-tasks#timeout).
- **A retry reruns the whole document.** Retries start over on a new instance, so a 50-page PDF that fails on page 49 is OCRed again from page 1. Reading and OCR have no side effects, so that's safe. See [retries](https://render.com/docs/workflows-defining-tasks#retry-settings).
- **Retries only help when the next attempt can pass.** An out-of-memory kill caused by one huge page will happen again on the same plan. If a document fails on every attempt with `killed by signal 9`, lower `OCR_DPI`, lower `OCR_PAGE_WORKERS` or move `ocr_document` to a plan with more memory.
- **OCR isn't exact.** Tesseract misreads some characters, especially on noisy scans. Check the fields you depend on.

## Links

- [Render Workflows](https://render.com/docs/workflows)
- [Using Docker with workflows](https://render.com/docs/workflows-tutorial#using-docker)
- [Defining tasks](https://render.com/docs/workflows-defining-tasks)
- [Limits and pricing](https://render.com/docs/workflows-limits)
- [Workflows SDK for Python](https://render.com/docs/workflows-sdk-python)
- [Tesseract documentation](https://tesseract-ocr.github.io/)
