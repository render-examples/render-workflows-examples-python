"""
Order saga on Render Workflows

A checkout flow: reserve inventory, charge the payment, create the shipment.
If a later step fails for good, the flow undoes the earlier ones in reverse
order (refund the payment, release the inventory). This is the saga pattern.

It's built as a flow, so every step is checkpointed. If the instance running
the flow dies, Render replays the flow on a new instance, and steps that
already finished return their recorded results instead of running again. A
card isn't charged twice because the process crashed after charging it.

The inventory, payment and shipping services are in-process fakes (see
fake_services.py). The orchestration is real.
"""

import logging
import os

import fake_services as svc
from render import FlowContext, Retry, Workflows
from render.client.errors import StepError

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = Workflows()

# Forward steps call other services, so they retry a couple of times on errors
# like timeouts. Each retry runs in this process, with the same arguments.
SERVICE_RETRY = Retry(max_retries=2, wait_duration_ms=500, backoff_scaling=2.0)
# Compensating steps retry harder: giving up on a refund leaves a customer
# charged for an order that won't ship.
UNDO_RETRY = Retry(max_retries=4, wait_duration_ms=1000, backoff_scaling=2.0)
# The simulated crash uses up one attempt of its step, so it needs one retry.
CRASH_RETRY = Retry(max_retries=1, wait_duration_ms=1000, backoff_scaling=2.0)


def idempotency_key(order_id: str, action: str) -> str:
    """One key per order and action, the same on every attempt and replay.

    A key built from the run ID, the attempt or the time would change between
    attempts and stop nothing.
    """
    return f"{order_id}:{action}"


def order_total_cents(order: dict) -> int:
    return sum(item["quantity"] * item["unit_price_cents"] for item in order["items"])


# ============================================================================
# Steps. Each is a plain function that ctx.step runs inside the flow's process.
# They get no context, and their arguments and results must be JSON-safe.
# ============================================================================


def reserve_inventory(order: dict) -> dict:
    logger.info("[%s] reserve_inventory %s", svc.PROCESS_ID, order["order_id"])
    return svc.inventory_reserve(
        order["order_id"], order["items"], idempotency_key(order["order_id"], "reserve")
    )


def charge_payment(order: dict, amount_cents: int) -> dict:
    logger.info("[%s] charge_payment %s %d", svc.PROCESS_ID, order["order_id"], amount_cents)
    return svc.payments_charge(
        order["order_id"],
        amount_cents,
        order["payment_method"],
        idempotency_key(order["order_id"], "charge"),
        fault=order.get("simulate"),
    )


def create_shipment(order: dict) -> dict:
    logger.info("[%s] create_shipment %s", svc.PROCESS_ID, order["order_id"])
    return svc.shipping_create(
        order["order_id"],
        order["shipping_address"],
        idempotency_key(order["order_id"], "ship"),
        fault=order.get("simulate"),
    )


def release_inventory(reservation: dict) -> dict:
    logger.info("[%s] release_inventory %s", svc.PROCESS_ID, reservation["reservation_id"])
    return svc.inventory_release(
        reservation["reservation_id"], idempotency_key(reservation["order_id"], "release")
    )


def refund_payment(charge: dict) -> dict:
    logger.info("[%s] refund_payment %s", svc.PROCESS_ID, charge["charge_id"])
    return svc.payments_refund(
        charge["charge_id"], charge["amount_cents"], idempotency_key(charge["order_id"], "refund")
    )


def simulate_crash(charged_in_process: str) -> dict:
    """Kill this process once, right after the charge, to force a replay.

    Only with SIMULATE_CRASH=1, for the local task server: it replays a flow
    whose process exits. On Render, a process that exits on its own fails the
    run; Render replays a flow when the instance running it fails.

    The first time, this runs in the same process that charged the card, so it
    exits without reporting back, and the flow replays in a new process. In the
    replay, reserve_inventory and charge_payment return their recorded results
    (with the first process's ID), and this step runs again in the new process,
    which has a different ID, so it returns.
    """
    if os.getenv("SIMULATE_CRASH") != "1":
        return {"crashed": False, "process": svc.PROCESS_ID}
    if charged_in_process == svc.PROCESS_ID:
        logger.warning("[%s] simulate_crash: exiting the process mid-flow", svc.PROCESS_ID)
        logging.shutdown()
        os._exit(1)
    return {"crashed": True, "charged_in": charged_in_process, "process": svc.PROCESS_ID}


# ============================================================================
# The flow
# ============================================================================


def compensate(
    ctx: FlowContext,
    order_id: str,
    steps: dict,
    undo: list[tuple[str, object, dict]],
    failed_step: str,
    reason: str,
) -> dict:
    """Run the compensating steps, newest first, and report the order as undone."""
    logger.info("%s: %s failed (%s), compensating", order_id, failed_step, reason)
    failed = []
    for name, fn, arg in reversed(undo):
        try:
            steps[name] = ctx.step(name, fn, arg, retry=UNDO_RETRY)
        except StepError as error:
            # Keep going: one failed undo shouldn't leave the others undone.
            failed.append(f"{name}: {error}")
    if failed:
        # Raising fails the flow run, so it shows as failed in the Dashboard
        # and someone can fix the order by hand. Render doesn't replay a flow
        # that raised.
        raise RuntimeError(
            f"{order_id}: {failed_step} failed ({reason}), then compensation failed: {failed}"
        )
    return {
        "order_id": order_id,
        "status": "compensated",
        "failed_step": failed_step,
        "reason": reason,
        "compensated": [name for name, _, _ in reversed(undo)],
        "steps": steps,
    }


@app.flow(timeout_seconds=300)
def checkout(ctx: FlowContext, order: dict) -> dict:
    """Reserve, charge, ship. Undo what's done if a later step fails for good.

    Flow code must make the same ctx calls in the same order on every replay,
    so anything that talks to another service happens inside a step.
    """
    order_id = order["order_id"]
    steps: dict[str, dict] = {}
    # Compensations for the steps that succeeded so far, oldest first.
    undo: list[tuple[str, object, dict]] = []

    reservation = ctx.step("reserve_inventory", reserve_inventory, order, retry=SERVICE_RETRY)
    steps["reserve_inventory"] = reservation
    if not reservation["ok"]:
        # Nothing happened yet, so there's nothing to undo.
        return {"order_id": order_id, "status": "rejected", "failed_step": "reserve_inventory",
                "reason": reservation["reason"], "compensated": [], "steps": steps}
    undo.append(("release_inventory", release_inventory, reservation))

    amount_cents = order_total_cents(order)
    try:
        charge = ctx.step("charge_payment", charge_payment, order, amount_cents, retry=SERVICE_RETRY)
    except StepError as error:
        # Still failing after its retries, such as a gateway that keeps timing out.
        return compensate(ctx, order_id, steps, undo, "charge_payment", str(error))
    steps["charge_payment"] = charge
    if not charge["ok"]:
        # A decline comes back as a result, not an error, so it isn't retried.
        return compensate(ctx, order_id, steps, undo, "charge_payment", charge["reason"])
    undo.append(("refund_payment", refund_payment, charge))

    if order.get("simulate") == "crash_after_charge":
        # A step cut off by a crash counts as a failed attempt. With no
        # retries left, the replay would fail the run instead of resuming it.
        steps["simulate_crash"] = ctx.step(
            "simulate_crash", simulate_crash, charge["process"], retry=CRASH_RETRY
        )

    try:
        shipment = ctx.step("create_shipment", create_shipment, order, retry=SERVICE_RETRY)
    except StepError as error:
        return compensate(ctx, order_id, steps, undo, "create_shipment", str(error))
    steps["create_shipment"] = shipment

    return {"order_id": order_id, "status": "completed", "amount_cents": amount_cents,
            "compensated": [], "steps": steps}


if __name__ == "__main__":
    app.start()
