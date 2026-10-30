"""Fake orders for a new user.

Dates are counted back from "now", so a user who registers today always has
one order inside the return window and one outside it.
"""
import secrets
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from pymongo.database import Database

from app.catalog import line_item

DAY = timedelta(days=1)

# One template per order. "placed", "delivered" and "eta_in" are days.
ORDER_TEMPLATES: list[dict] = [
    {"status": "placed", "placed": 0,
     "items": [line_item("HOME-MUG-01", 2), line_item("HOME-CANDLE-01")]},
    {"status": "processing", "placed": 1,
     "items": [line_item("ELEC-KEYB-01"), line_item("ELEC-MOUSE-01")]},
    {"status": "shipped", "placed": 3, "eta_in": 2, "carrier": "UPS",
     "items": [line_item("ELEC-HDPHN-01")]},
    {"status": "shipped", "placed": 5, "eta_in": 1,
     "items": [line_item("BOOK-PYTHON-01"), line_item("BOOK-NOVEL-01", 2)]},
    # Inside the return window. One T-shirt (19.99) is under the auto-approve
    # limit; jacket plus both T-shirts (129.97) is over it. Earbuds are not returnable.
    {"status": "delivered", "placed": 7, "delivered": 3,
     "items": [line_item("CLOTH-JACKET-L"), line_item("CLOTH-TEE-M", 2), line_item("ELEC-EARBUDS-01")]},
    {"status": "delivered", "placed": 14, "delivered": 10, "carrier": "FedEx",
     "items": [line_item("HOME-LAMP-01"), line_item("ELEC-CABLE-USBC", 3)]},
    # Outside the return window.
    {"status": "delivered", "placed": 45, "delivered": 40,
     "items": [line_item("SPORT-YOGA-MAT"), line_item("SPORT-BOTTLE-01", 2)]},
    {"status": "delivered", "placed": 95, "delivered": 90, "carrier": "UPS",
     "items": [line_item("SPORT-RUNSHOE-42"), line_item("CLOTH-SOCKS-3")]},
    {"status": "cancelled", "placed": 20,
     "items": [line_item("HOME-KETTLE-01")]},
    # Already returned, and one waiting for admin review.
    {"status": "returned", "placed": 55, "delivered": 50,
     "items": [line_item("CLOTH-HOODIE-M")],
     "return": {"status": "auto_approved", "reason": "Too small"}},
    {"status": "return_requested", "placed": 10, "delivered": 6,
     "items": [line_item("ELEC-MONITOR-27")],
     "return": {"status": "pending_review", "reason": "Dead pixels in the corner"}},
]


def new_order_no(db: Database, taken: set[str]) -> str:
    """Random ORD-NNNNNN, unique across all users."""
    while True:
        order_no = f"ORD-{secrets.randbelow(900_000) + 100_000}"
        if order_no not in taken and db.orders.find_one({"order_no": order_no}, {"_id": 1}) is None:
            taken.add(order_no)
            return order_no


def build_order(user_id: ObjectId, order_no: str, template: dict, now: datetime) -> dict:
    status = template["status"]
    items = [dict(item) for item in template["items"]]
    placed_at = now - template["placed"] * DAY
    delivered_at = now - template["delivered"] * DAY if "delivered" in template else None

    shipped_at = None
    tracking = None
    if status not in ("placed", "processing", "cancelled"):
        shipped_at = placed_at + DAY
        events = [
            {"at": placed_at, "status": "Label created", "location": "Warehouse, Rotterdam"},
            {"at": shipped_at, "status": "Picked up by carrier", "location": "Warehouse, Rotterdam"},
        ]
        if delivered_at:
            events.append({"at": delivered_at, "status": "Delivered", "location": "Front door"})
            eta = delivered_at
        else:
            events.append({"at": shipped_at + DAY / 2, "status": "In transit", "location": "Sorting center"})
            eta = now + template.get("eta_in", 2) * DAY
        tracking = {"carrier": template.get("carrier", "DHL"), "eta": eta, "events": events}

    return {
        "order_no": order_no,
        "user_id": user_id,
        "items": items,
        "total": round(sum(i["price"] * i["qty"] for i in items), 2),
        "status": status,
        "placed_at": placed_at,
        "shipped_at": shipped_at,
        "delivered_at": delivered_at,
        "tracking": tracking,
    }


def build_return_request(order: dict, template_return: dict) -> dict:
    returnable = [i for i in order["items"] if i["returnable"]]
    status = template_return["status"]
    return {
        "order_no": order["order_no"],
        "user_id": order["user_id"],
        "item_skus": [i["sku"] for i in returnable],
        "reason": template_return["reason"],
        "amount": round(sum(i["price"] * i["qty"] for i in returnable), 2),
        "status": status,
        "decided_by": "auto" if status == "auto_approved" else None,
        "created_at": order["delivered_at"] + 2 * DAY,
    }


def seed_orders_for_user(db: Database, user_id: ObjectId, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    taken: set[str] = set()
    orders = [build_order(user_id, new_order_no(db, taken), t, now) for t in ORDER_TEMPLATES]
    db.orders.insert_many(orders)

    returns = [
        build_return_request(order, t["return"])
        for order, t in zip(orders, ORDER_TEMPLATES)
        if "return" in t
    ]
    if returns:
        db.return_requests.insert_many(returns)
    return orders
