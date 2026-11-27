"""
Start a CSV enrichment from another service or script, and check on it later.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to batch-enrichment). Pass a CSV URL as the first argument, or
leave it out to enrich the bundled companies.csv.

A batch can take up to 24 hours, so this starts the run, saves its ID, and
checks its status instead of holding a connection open.
"""

import json
import os
import sys
import time

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "batch-enrichment")
csv_url = sys.argv[1] if len(sys.argv) > 1 else None

# Render() reads RENDER_API_KEY from the environment.
render = Render()

# Task input is a list of the task's arguments. Starting the same CSV twice
# doesn't pay for two batches: submit_batch reuses the batch it already made.
run = render.workflows.start_task(f"{SLUG}/enrich_csv", [csv_url] if csv_url else [])
print(f"Started run {run.id}. Store this ID; you can check it from anywhere.")

# Check every 30 seconds. In a real app, store run.id and check from a cron
# job or a status page instead of looping.
while True:
    details = render.workflows.get_task_run(run.id)
    if details.status not in ("pending", "running"):
        break
    print(f"  {details.status}...")
    time.sleep(30)

if details.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {details.status}: {details.error}")

# results is a list holding the task's return value.
result = details.results[0]
if "error" in result:
    raise SystemExit(f"Enrichment failed: {result}")

print(f"\nEnriched {result['enriched']} of {result['total']} rows (batch {result['batch_id']})")
for row in result["rows"]:
    print(f"  {row['id']}  {row['industry']:<22} {row['size_bucket']:<9} {row['summary']}")
for failure in result["failed"]:
    print(f"  failed: {json.dumps(failure)}")
