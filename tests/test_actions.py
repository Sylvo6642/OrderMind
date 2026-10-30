"""Pending actions: nothing changes until the right user confirms, once, in time."""
from dataclasses import replace
from datetime import timedelta

from bson import ObjectId

from app.actions import cancel_action, confirm_action
from app.tools import cancel_order, request_return
from tests.conftest import order_no


def _propose_cancel(ctx, status="placed"):
    no = order_no(ctx, status)
    return no, cancel_order(ctx, no)["data"]["action_id"]


def _order(ctx, no):
    return ctx.db.orders.find_one({"order_no": no})


def test_confirm_cancels_and_records_refund(ann):
    no, action_id = _propose_cancel(ann)
    result = confirm_action(ann, action_id)
    assert result["ok"] and result["data"]["status"] == "cancelled"
    order = _order(ann, no)
    assert order["status"] == "cancelled" and order["refund_amount"] == order["total"]
    assert ann.db.pending_actions.find_one({"_id": ObjectId(action_id)})["status"] == "done"


def test_confirm_is_single_use(ann):
    _, action_id = _propose_cancel(ann)
    assert confirm_action(ann, action_id)["ok"]
    second = confirm_action(ann, action_id)
    assert second["error"] == "not_pending" and "done" in second["message"]


def test_other_user_cannot_confirm_or_cancel(ann, ben):
    no, action_id = _propose_cancel(ann)
    assert confirm_action(ben, action_id)["error"] == "not_found"
    assert cancel_action(ben, action_id)["error"] == "not_found"
    assert _order(ann, no)["status"] == "placed"
    assert confirm_action(ann, action_id)["ok"]  # still usable by the owner


def test_action_is_bound_to_its_conversation(ann):
    no, action_id = _propose_cancel(ann)
    other_chat = replace(ann, conversation_id=ObjectId())
    assert confirm_action(other_chat, action_id)["error"] == "not_found"
    assert _order(ann, no)["status"] == "placed"


def test_action_expires_after_five_minutes(ann):
    no, action_id = _propose_cancel(ann)
    late = replace(ann, now=ann.now + timedelta(minutes=5, seconds=1))
    assert confirm_action(late, action_id)["error"] == "expired"
    assert _order(ann, no)["status"] == "placed"
    assert confirm_action(ann, action_id)["error"] == "not_pending"  # stays expired


def test_rules_are_checked_again_at_confirm_time(ann):
    no, action_id = _propose_cancel(ann, "processing")
    ann.db.orders.update_one({"order_no": no}, {"$set": {"status": "shipped"}})  # warehouse was faster
    result = confirm_action(ann, action_id)
    assert result["error"] == "not_allowed"
    assert _order(ann, no)["status"] == "shipped"
    assert ann.db.pending_actions.find_one({"_id": ObjectId(action_id)})["status"] == "failed"


def test_new_proposal_supersedes_old_one(ann):
    no = order_no(ann, "delivered", 3)
    first = request_return(ann, no, ["CLOTH-JACKET-L"], "Too big")["data"]["action_id"]
    second = request_return(ann, no, ["CLOTH-TEE-M"], "Actually just the shirts")["data"]["action_id"]
    assert confirm_action(ann, first)["error"] == "not_pending"
    assert confirm_action(ann, second)["ok"]
    assert ann.db.return_requests.find_one({"order_no": no})["item_skus"] == ["CLOTH-TEE-M"]


def test_user_can_cancel_a_proposal(ann):
    no, action_id = _propose_cancel(ann)
    assert cancel_action(ann, action_id)["ok"]
    assert confirm_action(ann, action_id)["error"] == "not_pending"
    assert _order(ann, no)["status"] == "placed"


def test_small_return_is_auto_approved(ann):
    no = order_no(ann, "delivered", 3)
    action_id = request_return(ann, no, ["CLOTH-TEE-M"], "Too big")["data"]["action_id"]
    result = confirm_action(ann, action_id)
    assert result["data"]["return_status"] == "auto_approved" and result["data"]["refund_amount"] == 39.98
    assert _order(ann, no)["status"] == "return_requested"
    request = ann.db.return_requests.find_one({"order_no": no})
    assert request["status"] == "auto_approved" and request["decided_by"] == "auto"


def test_large_return_waits_for_review(ann):
    no = order_no(ann, "delivered", 3)
    action_id = request_return(ann, no, ["CLOTH-JACKET-L", "CLOTH-TEE-M"], "Too big")["data"]["action_id"]
    result = confirm_action(ann, action_id)
    assert result["data"]["return_status"] == "pending_review" and result["data"]["refund_amount"] == 129.97
    assert ann.db.return_requests.find_one({"order_no": no})["decided_by"] is None


def test_second_return_on_same_order_is_refused(ann):
    no = order_no(ann, "delivered", 3)
    confirm_action(ann, request_return(ann, no, ["CLOTH-TEE-M"], "Too big")["data"]["action_id"])
    assert request_return(ann, no, ["CLOTH-JACKET-L"], "Also this")["error"] == "not_allowed"


def test_bad_action_ids(ann):
    for junk in ["", "not-an-id", str(ObjectId()), {"$ne": None}]:
        assert confirm_action(ann, junk)["error"] == "not_found"
        assert cancel_action(ann, junk)["error"] == "not_found"
