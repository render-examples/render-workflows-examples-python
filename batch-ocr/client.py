"""
Trigger the OCR tasks from another service or script.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to batch-ocr).
"""

import os

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "batch-ocr")

# References, not file bytes: names of files in samples/ or https:// URLs.
# corrupt.pdf fails on purpose.
DOCS = ["invoice-1042.pdf", "delivery-receipt-2207.pdf", "plan-amendment.pdf", "corrupt.pdf"]

# Render() reads RENDER_API_KEY from the environment.
render = Render()

# One document. Task input is a list of the task's arguments. run_task waits for
# the run to finish and returns its details.
run = render.workflows.run_task(f"{SLUG}/ocr_document", [DOCS[0]])
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

# results is a list holding the task's return value.
doc = run.results[0]
print(f"{doc['doc_ref']}: {doc.get('pages')} pages")
print(doc["text"][0] if "text" in doc else doc["error"])

# All documents, OCRed in parallel.
run = render.workflows.run_task(f"{SLUG}/ocr_batch", [DOCS])
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

batch = run.results[0]
print(f"\nOCRed {batch['succeeded']} of {batch['total']} documents ({batch['pages']} pages)")
for failure in batch["failed"]:
    print(f"  failed: {failure['doc_ref']}: {failure['error']}")
