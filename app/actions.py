"""Pending actions: how a write tool asks for the user's confirmation.

A write tool never changes an order. It stores a pending action that is bound
to the user and the conversation, expires after a few minutes and can be used
once. Only confirm_action, called from the user's Confirm button, executes it.
"""
from datetime import timedelta

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument

from app.config import get_settings
from app.context import ToolContext, fail, not_found, ok
from app.rules import CANCELLABLE, check_cancel, check_return, needs_review, refund_amount


def propose_action(ctx: ToolContext, action_type: str, order_no: str, params: dict, summary: str) -> dict:
    ttl = get_settings().pending_action_ttl_minutes
    # One open action per order: a new proposal replaces the old one.
    ctx.db.pending_actions.update_many(
        {"user_id": ctx.user_id, "order_no": order_no, "status": "pending"},
        {"$set": {"status": "superseded"}},
    )
    action = {
        "user_id": ctx.user_id,
        "conversation_id": ctx.conversation_id,
        "type": action_type,
        "order_no": order_no,
        "params": params,
        "summary": summary,
        "status": "pending",
        "created_at": ctx.now,
        "expires_at": ctx.now + timedelta(minutes=ttl),
    }
    ctx.db.pending_actions.insert_one(action)
    return ok({
        "needs_confirmation": True,
        "action_id": str(action["_id"]),
        "summary": summary,
        "expires_in_minutes": ttl,
        "note": "Nothing has changed yet. The user must press Confirm in the chat. Do not say it is done.",
    })


def confirm_action(ctx: ToolContext, action_id: str) -> dict:
    # Atomic claim: only one request can move the action from pending to executing,
    # so a double click cannot run it twice.
    action = _claim(ctx, action_id, {"expires_at": {"$gt": ctx.now}}, {"status": "executing"})
    if action is None:
        return _why_not(ctx, action_id)

    try:
        result = EXECUTORS[action["type"]](ctx, action)
    except Exception:
        _finish(ctx, action, "failed", fail("error", "Unexpected error."))
        raise
    _finish(ctx, action, "done" if result["ok"] else "failed", result)
    return result


def cancel_action(ctx: ToolContext, action_id: str) -> dict:
    action = _claim(ctx, action_id, {}, {"status": "cancelled", "finished_at": ctx.now})
    if action is None:
        return _why_not(ctx, action_id)
    return ok({"action_id": str(action["_id"]), "status": "cancelled"})


def _execute_cancel(ctx: ToolContext, action: dict) -> dict:
    order = ctx.db.orders.find_one({"order_no": action["order_no"], "user_id": ctx.user_id})
    if order is None:
        return not_found()
    if error := check_cancel(order):
        return error

    # The status condition guards against a change between the check and the write.
    updated = ctx.db.orders.update_one(
        {"_id": order["_id"], "status": {"$in": list(CANCELLABLE)}},
        {"$set": {"status": "cancelled", "cancelled_at": ctx.now, "refund_amount": order["total"]}},
    )
    if updated.modified_count == 0:
        return fail("conflict", "The order changed while cancelling. Nothing was done.")
    return ok({"order_no": order["order_no"], "status": "cancelled", "refund_amount": order["total"]})


def _execute_return(ctx: ToolContext, action: dict) -> dict:
    order = ctx.db.orders.find_one({"order_no": action["order_no"], "user_id": ctx.user_id})
    if order is None:
        return not_found()
    skus = action["params"]["item_skus"]
    if error := check_return(order, skus, ctx.now):
        return error

    updated = ctx.db.orders.update_one(
        {"_id": order["_id"], "status": "delivered"}, {"$set": {"status": "return_requested"}}
    )
    if updated.modified_count == 0:
        return fail("conflict", "The order changed while starting the return. Nothing was done.")

    amount = refund_amount(order, skus)
    status = "pending_review" if needs_review(amount) else "auto_approved"
    ctx.db.return_requests.insert_one({
        "order_no": order["order_no"],
        "user_id": ctx.user_id,
        "item_skus": skus,
        "reason": action["params"]["reason"],
        "amount": amount,
        "status": status,
        "decided_by": "auto" if status == "auto_approved" else None,
        "created_at": ctx.now,
    })
    return ok({
        "order_no": order["order_no"],
        "return_status": status,
        "refund_amount": amount,
        "message": "Refund approved automatically." if status == "auto_approved"
        else "A team member will review this return.",
    })


EXECUTORS = {"cancel_order": _execute_cancel, "request_return": _execute_return}


def _parse_id(action_id) -> ObjectId | None:
    try:
        return ObjectId(str(action_id))
    except InvalidId:
        return None


def _owned(ctx: ToolContext, oid: ObjectId) -> dict:
    return {"_id": oid, "user_id": ctx.user_id, "conversation_id": ctx.conversation_id}


def _claim(ctx: ToolContext, action_id, extra_filter: dict, new_fields: dict) -> dict | None:
    oid = _parse_id(action_id)
    if oid is None:
        return None
    return ctx.db.pending_actions.find_one_and_update(
        {**_owned(ctx, oid), "status": "pending", **extra_filter},
        {"$set": new_fields},
        return_document=ReturnDocument.AFTER,
    )


def _finish(ctx: ToolContext, action: dict, status: str, result: dict) -> None:
    ctx.db.pending_actions.update_one(
        {"_id": action["_id"]}, {"$set": {"status": status, "result": result, "finished_at": ctx.now}}
    )


def _why_not(ctx: ToolContext, action_id) -> dict:
    """Explain why an action could not be claimed. Other users' actions look like missing ones."""
    oid = _parse_id(action_id)
    action = ctx.db.pending_actions.find_one(_owned(ctx, oid)) if oid else None
    if action is None:
        return fail("not_found", "No such action.")
    if action["status"] == "pending":  # still pending, so it can only have expired
        ctx.db.pending_actions.update_one({"_id": oid, "status": "pending"}, {"$set": {"status": "expired"}})
        return fail("expired", "This confirmation has expired. Ask the assistant again.")
    return fail("not_pending", f"This action is already {action['status']}.")
