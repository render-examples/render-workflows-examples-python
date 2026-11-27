"""
Trigger the sandbox tasks from another service or script.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to sandbox-fanout).
"""

import json
import os
import pathlib

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "sandbox-fanout")
jobs = json.loads((pathlib.Path(__file__).parent / "jobs.json").read_text())

# Render() reads RENDER_API_KEY from the environment.
render = Render()

# One job. Task input is a list of the task's arguments. run_task waits for
# the run to finish and returns its details.
run = render.workflows.run_task(f"{SLUG}/run_job", [jobs[0]])
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

# results is a list holding the task's return value.
result = run.results[0]
print(f"{result['job_id']} -> {json.dumps(result.get('output') or result)}")

# All jobs, one sandbox each. One job fails on purpose.
run = render.workflows.run_task(f"{SLUG}/run_batch", [jobs])
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

batch = run.results[0]
print(f"\n{batch['succeeded']} of {batch['total']} jobs succeeded")
for r in batch["results"]:
    print(f"  {r['job_id']}: {json.dumps(r['output'])}")
for failure in batch["failed"]:
    print(f"  failed: {failure['job_id']}: {failure['error']}")
