"""The five tools: ownership checks, state checks and what the model gets to see."""
from dataclasses import replace
from datetime import timedelta

import pytest

from app.tools import cancel_order, create_support_ticket, get_order, list_orders, request_return, run_tool
from tests.conftest import order_no

RECENT = 3  # delivered 3 days ago, inside the 14-day window
OLD = 40  # delivered 40 days ago, outside it


# Ownership

@pytest.mark.parametrize("call", [
    lambda ctx, no: get_order(ctx, no),
    lambda ctx, no: cancel_order(ctx, no),
    lambda ctx, no: request_return(ctx, no, ["CLOTH-TEE-M"], "Too big"),
])
def test_other_users_orders_look_exactly_like_missing_ones(ann, ben, call):
    bens_order = order_no(ben, "delivered", RECENT)
    assert call(ann, bens_order) == call(ann, "ORD-000000")
    assert call(ann, bens_order)["error"] == "not_found"


def test_list_orders_only_returns_own_orders(ann, ben):
    anns = {o["order_no"] for o in list_orders(ann)["data"]["orders"]}
    bens = {o["order_no"] for o in list_orders(ben)["data"]["orders"]}
    assert anns and bens and anns.isdisjoint(bens)


def test_model_cannot_pass_user_id(ann, ben):
    result = run_tool(ann, "get_order", {"order_id": order_no(ben, "placed"), "user_id": str(ben.user_id)})
    assert result["error"] == "invalid_argument"
    assert "user_id" in result["message"]


def test_cancel_and_return_change_nothing_for_other_users(ann, ben):
    before = list(ben.db.orders.find({"user_id": ben.user_id}))
    cancel_order(ann, order_no(ben, "placed"))
    request_return(ann, order_no(ben, "delivered", RECENT), ["CLOTH-TEE-M"], "Too big")
    assert list(ben.db.orders.find({"user_id": ben.user_id})) == before
    assert ben.db.pending_actions.count_documents({}) == 0


# Reads

def test_list_orders_is_compact(ann):
    result = list_orders(ann)
    assert result["ok"] and result["data"]["count"] == 11
    assert set(result["data"]["orders"][0]) == {"order_no", "status", "placed", "total", "items"}


def test_list_orders_filter(ann):
    delivered = list_orders(ann, status="delivered")["data"]["orders"]
    assert len(delivered) == 4 and all(o["status"] == "delivered" for o in delivered)
    assert list_orders(ann, status="lost")["error"] == "invalid_argument"


def test_get_order_works_out_eligibility(ann):
    recent = get_order(ann, order_no(ann, "delivered", RECENT))["data"]
    assert recent["can_return"] is True and recent["can_cancel"] is False
    assert recent["return_deadline"] == "2026-11-10"
    assert "tracking" in recent and "user_id" not in recent

    old = get_order(ann, order_no(ann, "delivered", OLD))["data"]
    assert old["can_return"] is False

    placed = get_order(ann, order_no(ann, "placed"))["data"]
    assert placed["can_cancel"] is True and "tracking" not in placed


def test_get_order_shows_existing_return(ann):
    detail = get_order(ann, order_no(ann, "return_requested"))["data"]
    assert detail["return_request"]["status"] == "pending_review"


@pytest.mark.parametrize("spell", [
    lambda no: no,                     # ORD-482913
    lambda no: no.lower(),             # ord-482913
    lambda no: f"  {no} ",             # extra spaces
    lambda no: no[4:],                 # 482913
    lambda no: no.replace("-", " "),   # ORD 482913
])
def test_order_number_spellings(ann, spell):
    real = order_no(ann, "placed")
    assert get_order(ann, spell(real))["data"]["order_no"] == real


def test_garbage_order_numbers_are_not_found(ann):
    # The last one is a MongoDB operator; it must never reach a query as an operator.
    for junk in ["", "ORD-12", "1234567", "ORD-ABCDEF", {"$ne": None}]:
        assert get_order(ann, junk)["error"] == "not_found"


# Cancel

@pytest.mark.parametrize("status", ["placed", "processing"])
def test_cancel_needs_confirmation_and_changes_nothing(ann, status):
    no = order_no(ann, status)
    result = cancel_order(ann, no)
    assert result["ok"] and result["data"]["needs_confirmation"] is True
    assert ann.db.orders.find_one({"order_no": no})["status"] == status
    assert ann.db.pending_actions.find_one({"order_no": no})["status"] == "pending"


@pytest.mark.parametrize("status", ["shipped", "delivered", "cancelled", "returned", "return_requested"])
def test_cannot_cancel_after_shipping(ann, status):
    assert cancel_order(ann, order_no(ann, status))["error"] == "not_allowed"
    assert ann.db.pending_actions.count_documents({}) == 0


# Return

def test_return_inside_window(ann):
    result = request_return(ann, order_no(ann, "delivered", RECENT), ["cloth-tee-m"], "Too big")
    assert result["ok"] and result["data"]["needs_confirmation"] is True
    assert "$39.98" in result["data"]["summary"] and "automatically" in result["data"]["summary"]


def test_large_return_says_it_needs_review(ann):
    no = order_no(ann, "delivered", RECENT)
    result = request_return(ann, no, ["CLOTH-JACKET-L", "CLOTH-TEE-M"], "Changed my mind")
    assert "$129.97" in result["data"]["summary"] and "review" in result["data"]["summary"]


def test_return_outside_window(ann):
    result = request_return(ann, order_no(ann, "delivered", OLD), ["SPORT-YOGA-MAT"], "Worn out")
    assert result["error"] == "not_allowed" and "window" in result["message"]


def test_return_window_edge(ann):
    no = order_no(ann, "delivered", RECENT)
    last_moment = replace(ann, now=ann.now + timedelta(days=11))
    too_late = replace(ann, now=ann.now + timedelta(days=11, seconds=1))
    assert request_return(last_moment, no, ["CLOTH-TEE-M"], "Too big")["ok"]
    assert request_return(too_late, no, ["CLOTH-TEE-M"], "Too big")["error"] == "not_allowed"


@pytest.mark.parametrize("status", ["placed", "shipped", "cancelled", "return_requested", "returned"])
def test_only_delivered_orders_can_be_returned(ann, status):
    assert request_return(ann, order_no(ann, status), ["ANY"], "Reason")["error"] == "not_allowed"


def test_non_returnable_item_rejects_whole_request(ann):
    result = request_return(ann, order_no(ann, "delivered", RECENT), ["CLOTH-TEE-M", "ELEC-EARBUDS-01"], "Nope")
    assert result["error"] == "not_allowed" and "Wireless Earbuds" in result["message"]
    assert ann.db.pending_actions.count_documents({}) == 0


@pytest.mark.parametrize("skus", [["NOT-IN-ORDER"], [], "CLOTH-TEE-M", [42]])
def test_bad_item_lists(ann, skus):
    result = request_return(ann, order_no(ann, "delivered", RECENT), skus, "Reason")
    assert result["error"] == "invalid_argument"
    assert "CLOTH-TEE-M" in result["message"]  # tells the model which SKUs exist


def test_return_needs_a_reason(ann):
    assert request_return(ann, order_no(ann, "delivered", RECENT), ["CLOTH-TEE-M"], "  ")["error"] == "invalid_argument"


# Support tickets

def test_ticket_limit_is_three_per_rolling_day(ann, ben):
    for _ in range(3):
        assert create_support_ticket(ann, "My parcel arrived damaged.")["ok"]
    assert create_support_ticket(ann, "My parcel arrived damaged.")["error"] == "rate_limited"
    assert create_support_ticket(ben, "Ben has his own limit.")["ok"]
    next_day = replace(ann, now=ann.now + timedelta(hours=24, seconds=1))
    assert create_support_ticket(next_day, "My parcel arrived damaged.")["ok"]


def test_ticket_needs_a_real_summary(ann):
    assert create_support_ticket(ann, "help")["error"] == "invalid_argument"


# Dispatcher

def test_run_tool_rejects_unknown_tools_and_missing_args(ann):
    assert run_tool(ann, "delete_everything", {})["error"] == "unknown_tool"
    assert run_tool(ann, "get_order", {})["error"] == "invalid_argument"
    assert run_tool(ann, "list_orders", {})["ok"]
