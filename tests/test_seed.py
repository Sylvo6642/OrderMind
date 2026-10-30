import re
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.config import get_settings
from app.seed import seed_orders_for_user
from scripts.seed_demo import POISONED_PRODUCT_NAME, seed_demo

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def _seed(db):
    user_id = ObjectId()
    return user_id, seed_orders_for_user(db, user_id, now=NOW)


def _delivered_days_ago(orders, days):
    return [o for o in orders if o["delivered_at"] and NOW - o["delivered_at"] == timedelta(days=days)]


def test_order_numbers_are_well_formed_and_unique(db):
    _, first = _seed(db)
    _, second = _seed(db)
    numbers = [o["order_no"] for o in first + second]
    assert all(re.fullmatch(r"ORD-\d{6}", n) for n in numbers)
    assert len(set(numbers)) == len(numbers)


def test_totals_match_items(db):
    _, orders = _seed(db)
    for order in orders:
        assert order["total"] == round(sum(i["price"] * i["qty"] for i in order["items"]), 2)


def test_return_window_cases_exist(db):
    _, orders = _seed(db)
    window = get_settings().return_window_days
    recent = _delivered_days_ago(orders, 3)
    old = _delivered_days_ago(orders, 40)
    assert len(recent) == 1 and recent[0]["status"] == "delivered" and 3 < window
    assert len(old) == 1 and old[0]["status"] == "delivered" and 40 > window


def test_recent_order_covers_both_refund_paths(db):
    """One cheap item is auto-approvable, all returnable items need admin review."""
    _, orders = _seed(db)
    order = _delivered_days_ago(orders, 3)[0]
    limit = get_settings().auto_approve_refund_limit
    returnable = [i for i in order["items"] if i["returnable"]]

    assert any(not i["returnable"] for i in order["items"])
    assert min(i["price"] for i in returnable) < limit
    assert sum(i["price"] * i["qty"] for i in returnable) > limit


def test_shipping_fields_match_status(db):
    _, orders = _seed(db)
    for order in orders:
        not_shipped = order["status"] in ("placed", "processing", "cancelled")
        assert (order["tracking"] is None) == not_shipped
        assert (order["shipped_at"] is None) == not_shipped


def test_returned_orders_have_return_requests(db):
    user_id, orders = _seed(db)
    by_status = {o["status"]: o for o in orders}
    returned = db.return_requests.find_one({"order_no": by_status["returned"]["order_no"]})
    pending = db.return_requests.find_one({"order_no": by_status["return_requested"]["order_no"]})
    assert returned["status"] == "auto_approved" and returned["user_id"] == user_id
    assert pending["status"] == "pending_review"
    assert pending["amount"] > get_settings().auto_approve_refund_limit


def test_seed_demo_creates_users_once(db):
    created = seed_demo(db, "demo-password-123")
    assert len(created) == 4
    assert seed_demo(db, "demo-password-123") == []
    assert db.users.find_one({"email": "admin@demo.local"})["is_admin"] is True

    carol = db.users.find_one({"email": "carol@demo.local"})
    names = [i["name"] for o in db.orders.find({"user_id": carol["_id"]}) for i in o["items"]]
    assert POISONED_PRODUCT_NAME in names


def test_seed_demo_reset_recreates_users(db):
    seed_demo(db, "demo-password-123")
    old_alice = db.users.find_one({"email": "alice@demo.local"})["_id"]
    assert len(seed_demo(db, "demo-password-123", reset=True)) == 4
    assert db.orders.count_documents({"user_id": old_alice}) == 0
