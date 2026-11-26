"""
Trigger the triage tasks from another service or script.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to claude-agent-sdk).
"""

import json
import os
import pathlib

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "claude-agent-sdk")
tickets = json.loads((pathlib.Path(__file__).parent / "tickets.json").read_text())

# Render() reads RENDER_API_KEY from the environment.
render = Render()

# One ticket. Task input is a list of the task's arguments. run_task waits for
# the run to finish and returns its details.
run = render.workflows.run_task(f"{SLUG}/triage_ticket", [tickets[0]])
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

# results is a list holding the task's return value.
result = run.results[0]
print(f"{result['ticket_id']} -> {result.get('final_output', {}).get('category')}")
print(json.dumps(result.get("final_output") or result, indent=2))

# All tickets, triaged in parallel.
run = render.workflows.run_task(f"{SLUG}/triage_batch", [tickets])
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

batch = run.results[0]
print(f"\nTriaged {batch['triaged']} of {batch['total']}: {batch['by_category']}")
print(f"Model cost: ${batch['total_cost_usd']}")
for failure in batch["failed"]:
    print(f"  failed: {failure}")
