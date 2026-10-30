"""Business rules for write actions. Pure functions over an order dict.

Each check runs twice: when the model proposes the action, and again when
the user confirms it, because the order may have changed in between.
A check returns None when the action is allowed, or a fail(...) result.
"""
from datetime import datetime, timedelta

from app.config import get_settings
from app.context import fail

CANCELLABLE = ("placed", "processing")


def check_cancel(order: dict) -> dict | None:
    if order["status"] in CANCELLABLE:
        return None
    return fail(
        "not_allowed",
        f"Order {order['order_no']} is {order['status']}. "
        "Only orders that are placed or processing can be cancelled.",
    )


def return_deadline(order: dict) -> datetime | None:
    if order.get("delivered_at") is None:
        return None
    return order["delivered_at"] + timedelta(days=get_settings().return_window_days)


def clean_skus(raw) -> list[str] | None:
    """Upper-case, trim and de-duplicate. None if it is not a non-empty list of strings."""
    if not isinstance(raw, list) or not raw or not all(isinstance(s, str) for s in raw):
        return None
    return list(dict.fromkeys(s.strip().upper() for s in raw))


def check_return(order: dict, item_skus: list[str] | None, now: datetime) -> dict | None:
    order_no = order["order_no"]
    if order["status"] in ("return_requested", "returned"):
        return fail("not_allowed", f"Order {order_no} already has a return. Only one return per order is allowed.")
    if order["status"] != "delivered":
        return fail("not_allowed", f"Order {order_no} is {order['status']}. Only delivered orders can be returned.")

    deadline = return_deadline(order)
    if now > deadline:
        days = get_settings().return_window_days
        return fail("not_allowed", f"The {days}-day return window for order {order_no} closed on {deadline:%Y-%m-%d}.")

    in_order = {item["sku"]: item for item in order["items"]}
    if item_skus is None:
        return fail("invalid_argument", f"item_skus must be a non-empty list. Items in this order: {_describe(order)}.")
    unknown = [sku for sku in item_skus if sku not in in_order]
    if unknown:
        return fail(
            "invalid_argument",
            f"Not in order {order_no}: {', '.join(unknown)}. Items in this order: {_describe(order)}.",
        )
    blocked = [in_order[sku]["name"] for sku in item_skus if not in_order[sku]["returnable"]]
    if blocked:
        return fail("not_allowed", f"These items cannot be returned: {', '.join(blocked)}. Nothing was submitted.")
    return None


def refund_amount(order: dict, item_skus: list[str]) -> float:
    """All units of each chosen item, priced from the order, never from the model."""
    chosen = set(item_skus)
    return round(sum(i["price"] * i["qty"] for i in order["items"] if i["sku"] in chosen), 2)


def needs_review(amount: float) -> bool:
    """Refunds below the limit are approved automatically; the rest go to an admin."""
    return amount >= get_settings().auto_approve_refund_limit


def _describe(order: dict) -> str:
    return ", ".join(f"{i['sku']} ({i['name']})" for i in order["items"])
