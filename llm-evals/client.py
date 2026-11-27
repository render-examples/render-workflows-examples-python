"""
Run the eval from another service or script and print the scorecard.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to llm-evals).
"""

import json
import os
import pathlib

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "llm-evals")
here = pathlib.Path(__file__).parent
tickets = json.loads((here / "tickets.json").read_text())
cases = json.loads((here / "cases.json").read_text())

# Render() reads RENDER_API_KEY from the environment.
render = Render()

# Task input is a list of the task's arguments. run_task waits for the run to
# finish and returns its details.
run = render.workflows.run_task(f"{SLUG}/run_eval", [tickets, cases])
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

# results is a list holding the task's return value.
report = run.results[0]
if "error" in report:
    raise SystemExit(report["error"])

print(f"{report['cases']} cases, judged by {report['judge']}\n")
print(f"{'model':36} {'pass rate':>9} {'score':>6} {'p50 ms':>7} {'errors':>6}")
for s in report["scorecard"]:
    print(
        f"{s['model']:36} {s['pass_rate'] if s['pass_rate'] is not None else '-':>9} "
        f"{s['avg_score'] if s['avg_score'] is not None else '-':>6} "
        f"{s['latency_ms']['p50'] or '-':>7} {len(s['errors']):>6}"
    )
    for failure in s["failures"]:
        print(f"    {failure['ticket_id']}: {'; '.join(failure['reasons'])}")
