"""
Start one checkout flow run per order in orders.json, then wait for them all.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to order-saga).
"""

import json
import os
import pathlib
import time

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "order-saga")

# The order steps can run in, for printing. Results come back with sorted keys.
STEP_ORDER = [
    "reserve_inventory", "charge_payment", "simulate_crash", "create_shipment",
    "refund_payment", "release_inventory",
]
orders = json.loads((pathlib.Path(__file__).parent / "orders.json").read_text())

# Render() reads RENDER_API_KEY from the environment.
render = Render()

# One flow run per order. The idempotency key comes from the order ID, so
# running this script again within 24 hours returns the runs it already started
# instead of checking out the same orders twice.
run_ids = {}
for order in orders:
    run = render.workflows.start_flow(
        f"{SLUG}/checkout", [order], idempotency_key=f"checkout-{order['order_id']}"
    )
    run_ids[order["order_id"]] = run.id
    print(f"{order['order_id']}: flow run {run.id}")

# Poll until every run reaches a final status. run_flow does this for a single
# run; polling get_flow_run lets the runs go in parallel.
finished = {}
while len(finished) < len(run_ids):
    time.sleep(2)
    for order_id, run_id in run_ids.items():
        if order_id in finished:
            continue
        run = render.workflows.get_flow_run(run_id)
        if run.status.value in ("succeeded", "failed", "canceled"):
            finished[order_id] = run

print()
for order_id, run in finished.items():
    if run.status.value != "succeeded":
        print(f"{order_id}: flow run {run.status.value}: {run.error}")
        continue
    # results is a list holding the flow's return value.
    result = run.results[0]
    line = f"{order_id}: {result['status']}"
    if result["status"] != "completed":
        undone = ", ".join(result["compensated"]) or "nothing"
        line += f" after {result['failed_step']} failed ({result['reason']}); undid {undone}"
    print(line)
    print(f"  flow attempts: {len(run.attempts)}")
    for name in STEP_ORDER:
        if name not in result["steps"]:
            continue
        step = result["steps"][name]
        note = " (idempotent replay)" if step.get("idempotent_replayed") else ""
        print(f"  {name:<18} ran in process {step.get('process', '-')}{note}")
