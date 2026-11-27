"""
In-process stand-ins for an inventory service, a payment provider such as
Stripe, and a shipping carrier.

They exist so the example runs with no accounts and no money involved. The
point of the example is the orchestration in main.py, not these classes. Swap
each function body for a real API call and keep the same arguments: every call
that changes something takes an idempotency key, the way Stripe's
Idempotency-Key header works.

State lives in this process only. A real provider keeps its idempotency records
on its side, so a key keeps working across processes and instances; here it
only works within one process, which is enough to show a step retry reusing
the key.
"""

import logging
import uuid

logger = logging.getLogger(__name__)

# One ID per process. Each step result carries it, so a flow's result shows
# which process ran each step. After a replay, steps that finished before the
# crash still show the first process's ID: Render returned their recorded
# results instead of running them again.
PROCESS_ID = uuid.uuid4().hex[:8]

KNOWN_SKUS = {"MUG-BLK", "MUG-WHT", "TEE-M", "TEE-L", "CAP-01"}
DECLINED_PAYMENT_METHODS = {"pm_card_declined"}

# Saved responses by idempotency key, like a provider's idempotency store.
_responses: dict[str, dict] = {}
# Fault switches that already fired, so a "fail once" fault fails once.
_fired_faults: set[str] = set()


class CarrierUnavailable(Exception):
    """The carrier's API didn't answer. A retry might succeed."""


class GatewayTimeout(Exception):
    """The payment gateway took the charge but the response never arrived."""


def _replay(idempotency_key: str) -> dict | None:
    saved = _responses.get(idempotency_key)
    if saved is None:
        return None
    logger.info("[%s] idempotency key %s seen before, returning the saved response", PROCESS_ID, idempotency_key)
    return {**saved, "idempotent_replayed": True}


def _save(idempotency_key: str, response: dict) -> dict:
    _responses[idempotency_key] = response
    return response


# ---- Inventory --------------------------------------------------------------


def inventory_reserve(order_id: str, items: list[dict], idempotency_key: str) -> dict:
    if (saved := _replay(idempotency_key)) is not None:
        return saved
    unknown = sorted(item["sku"] for item in items if item["sku"] not in KNOWN_SKUS)
    if unknown:
        return _save(idempotency_key, {"ok": False, "reason": f"unknown SKU: {', '.join(unknown)}"})
    return _save(
        idempotency_key,
        {
            "ok": True,
            "reservation_id": f"res_{uuid.uuid4().hex[:12]}",
            "order_id": order_id,
            "items": items,
            "process": PROCESS_ID,
        },
    )


def inventory_release(reservation_id: str, idempotency_key: str) -> dict:
    if (saved := _replay(idempotency_key)) is not None:
        return saved
    return _save(
        idempotency_key,
        {"released": True, "reservation_id": reservation_id, "process": PROCESS_ID},
    )


# ---- Payments (stand-in for Stripe or similar) -------------------------------


def payments_charge(
    order_id: str,
    amount_cents: int,
    payment_method: str,
    idempotency_key: str,
    fault: str | None = None,
) -> dict:
    if (saved := _replay(idempotency_key)) is not None:
        return saved
    if payment_method in DECLINED_PAYMENT_METHODS:
        # A decline is an answer, not an outage: retrying gets the same answer.
        return _save(idempotency_key, {"ok": False, "reason": "card_declined"})
    response = _save(
        idempotency_key,
        {
            "ok": True,
            "charge_id": f"ch_{uuid.uuid4().hex[:12]}",
            "order_id": order_id,
            "amount_cents": amount_cents,
            "process": PROCESS_ID,
        },
    )
    if fault == "payment_timeout_once" and idempotency_key not in _fired_faults:
        # The charge went through, then the connection dropped. The caller
        # can't tell, retries, and must not charge the card a second time.
        _fired_faults.add(idempotency_key)
        raise GatewayTimeout(f"no response for charge on {order_id} (the charge did go through)")
    return response


def payments_refund(charge_id: str, amount_cents: int, idempotency_key: str) -> dict:
    if (saved := _replay(idempotency_key)) is not None:
        return saved
    return _save(
        idempotency_key,
        {
            "refunded": True,
            "refund_id": f"re_{uuid.uuid4().hex[:12]}",
            "charge_id": charge_id,
            "amount_cents": amount_cents,
            "process": PROCESS_ID,
        },
    )


# ---- Shipping ----------------------------------------------------------------


def shipping_create(order_id: str, address: dict, idempotency_key: str, fault: str | None = None) -> dict:
    if fault == "carrier_outage":
        raise CarrierUnavailable(f"carrier API unavailable for {order_id}")
    if (saved := _replay(idempotency_key)) is not None:
        return saved
    return _save(
        idempotency_key,
        {
            "shipment_id": f"shp_{uuid.uuid4().hex[:12]}",
            "order_id": order_id,
            "country": address["country"],
            "process": PROCESS_ID,
        },
    )
