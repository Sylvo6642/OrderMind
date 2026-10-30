"""What every tool gets from the backend, and the shape of every tool result."""
import re
from dataclasses import dataclass
from datetime import datetime

from bson import ObjectId
from pymongo.database import Database


@dataclass(frozen=True)
class ToolContext:
    """Built by the backend from the verified JWT. The model never sees or sets these."""
    db: Database
    user_id: ObjectId
    conversation_id: ObjectId
    now: datetime


def ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def fail(error: str, message: str) -> dict:
    return {"ok": False, "error": error, "message": message}


def not_found() -> dict:
    # Same answer for "does not exist" and "belongs to someone else",
    # so a tool can never confirm that another customer's order exists.
    return fail("not_found", "No order with that number was found on this account.")


_ORDER_NO_RE = re.compile(r"(?:ORD[- ]?)?(\d{6})")


def normalise_order_no(raw) -> str | None:
    """Accept ORD-482913, ord-482913, ORD 482913 or 482913."""
    match = _ORDER_NO_RE.fullmatch(str(raw).strip().upper())
    return f"ORD-{match.group(1)}" if match else None


def find_own_order(ctx: ToolContext, order_id) -> dict | None:
    """The only way tools load an order: always filtered by the logged-in user."""
    order_no = normalise_order_no(order_id)
    if order_no is None:
        return None
    return ctx.db.orders.find_one({"order_no": order_no, "user_id": ctx.user_id})
