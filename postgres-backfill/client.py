"""
Seed the table, start the backfill, and follow its progress from another
service or script.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to postgres-backfill). Set FAIL_CHUNK to a chunk number to watch
that chunk fail once and retry. Set RESUME_FROM to the flow run ID of a run that
didn't finish to pick up where it stopped (the table isn't reseeded then).
"""

import os
import time

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "postgres-backfill")
ROW_COUNT = int(os.getenv("ROW_COUNT", "200000"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "5000"))
FAIL_CHUNK = int(os.environ["FAIL_CHUNK"]) if os.getenv("FAIL_CHUNK") else None
RESUME_FROM = os.getenv("RESUME_FROM") or None

# Render() reads RENDER_API_KEY from the environment.
render = Render()

if not RESUME_FROM:
    # Fill the demo table. Task input is a list of the task's arguments.
    seed = render.workflows.run_task(f"{SLUG}/seed_orders", [ROW_COUNT])
    if seed.status != "completed":
        raise SystemExit(f"Seed run {seed.id} ended as {seed.status}: {seed.error}")
    print(f"Seeded: {seed.results[0]}")

# Start the backfill flow without waiting for it. An idempotency key makes a
# repeated start (a retried request, a double click) return the same run.
run = render.workflows.start_flow(
    f"{SLUG}/backfill_orders",
    [CHUNK_SIZE, RESUME_FROM, FAIL_CHUNK],
    idempotency_key=f"backfill-orders-{int(time.time())}",
)
print(f"Started flow run {run.id}")

# Follow it. Progress lives in the backfill_runs table; backfill_status reads it.
while True:
    time.sleep(10)
    details = render.workflows.get_flow_run(run.id)
    status = details.status.value
    if status in ("succeeded", "failed", "canceled"):
        break
    progress = render.workflows.run_task(f"{SLUG}/backfill_status", [run.id])
    p = progress.results[0] if progress.status == "completed" else {}
    print(f"{status}: {p.get('rows_scanned')}/{p.get('rows_total')} rows ({p.get('percent')}%), chunks {p.get('chunks_done')}")

if status != "succeeded":
    raise SystemExit(f"Flow run {run.id} ended as {status}: {details.error}")
# results is a list holding the flow's return value.
print(f"Done: {details.results[0]}")
