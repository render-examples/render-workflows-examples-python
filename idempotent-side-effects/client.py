"""
Trigger handle_refund from another service or script.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to idempotent-side-effects).
"""

import json
import os
import pathlib

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "idempotent-side-effects")
tickets = json.loads((pathlib.Path(__file__).parent / "tickets.json").read_text())
ticket = tickets[0]

# Render() reads RENDER_API_KEY from the environment.
render = Render()

# The run's idempotency key comes from the ticket, the same way the credit's
# key comes from the account and order. If this script runs again for the
# same ticket within 24 hours (a retried webhook, a double click), Render
# returns the first run instead of starting another one.
for call in (1, 2):
    run = render.workflows.run_task(
        f"{SLUG}/handle_refund",
        [ticket],
        idempotency_key=f"handle_refund:{ticket['id']}",
    )
    print(f"Call {call}: run {run.id} ended as {run.status}")

if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

# results is a list holding the task's return value.
result = run.results[0]
print(json.dumps(result, indent=2))
