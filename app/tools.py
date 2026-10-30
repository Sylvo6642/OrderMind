"""The five tools the agent can call.

Each tool takes a ToolContext built by the backend from the verified JWT,
plus only the arguments the model chose. The model never supplies user_id.
Every tool returns {"ok": True, "data": ...} or {"ok": False, "error": ..., "message": ...}.
Results contain only JSON-ready values, so Stage 3 can pass them straight to the model.
"""
import inspect
import secrets
from datetime import datetime, timedelta

from app.actions import propose_action
from app.config import get_settings
from app.context import ToolContext, fail, find_own_order, not_found, ok
from app.models import ORDER_STATUSES
from app.rules import check_cancel, check_return, clean_skus, needs_review, refund_amount, return_deadline


def _date(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d") if dt else None


def _when(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else None


def order_summary(order: dict) -> dict:
    """Compact view for list_orders: no tracking, no SKUs."""
    return {
        "order_no": order["order_no"],
        "status": order["status"],
        "placed": _date(order["placed_at"]),
        "total": order["total"],
        "items": [f"{i['qty']} x {i['name']}" for i in order["items"]],
    }


def order_detail(ctx: ToolContext, order: dict) -> dict:
    """Full view for get_order, with eligibility worked out in code, not by the model."""
    deadline = return_deadline(order)
    detail = {
        "order_no": order["order_no"],
        "status": order["status"],
        "total": order["total"],
        "placed": _date(order["placed_at"]),
        "shipped": _date(order["shipped_at"]),
        "delivered": _date(order["delivered_at"]),
        "items": [
            {k: i[k] for k in ("sku", "name", "qty", "price", "returnable")} for i in order["items"]
        ],
        "can_cancel": check_cancel(order) is None,
        "can_return": order["status"] == "delivered" and deadline is not None and ctx.now <= deadline,
        "return_deadline": _date(deadline),
    }
    if tracking := order.get("tracking"):
        detail["tracking"] = {
            "carrier": tracking["carrier"],
            "eta": _date(tracking["eta"]),
            "events": [
                {"at": _when(e["at"]), "status": e["status"], "location": e["location"]}
                for e in tracking["events"]
            ],
        }
    if "refund_amount" in order:
        detail["refund_amount"] = order["refund_amount"]
    return_request = ctx.db.return_requests.find_one({"order_no": order["order_no"], "user_id": ctx.user_id})
    if return_request:
        detail["return_request"] = {
            "status": return_request["status"],
            "refund_amount": return_request["amount"],
            "item_skus": return_request["item_skus"],
        }
    return detail


def list_orders(ctx: ToolContext, status: str | None = None) -> dict:
    query = {"user_id": ctx.user_id}
    if status is not None:
        if status not in ORDER_STATUSES:
            return fail("invalid_argument", f"status must be one of: {', '.join(ORDER_STATUSES)}.")
        query["status"] = status
    orders = [order_summary(o) for o in ctx.db.orders.find(query).sort("placed_at", -1)]
    return ok({"count": len(orders), "orders": orders})


def get_order(ctx: ToolContext, order_id: str) -> dict:
    order = find_own_order(ctx, order_id)
    if order is None:
        return not_found()
    return ok(order_detail(ctx, order))


def cancel_order(ctx: ToolContext, order_id: str) -> dict:
    order = find_own_order(ctx, order_id)
    if order is None:
        return not_found()
    if error := check_cancel(order):
        return error
    items = ", ".join(i["name"] for i in order["items"])
    summary = f"Cancel order {order['order_no']} ({items}) and refund ${order['total']:.2f}."
    return propose_action(ctx, "cancel_order", order["order_no"], {}, summary)


def request_return(ctx: ToolContext, order_id: str, item_skus: list[str], reason: str) -> dict:
    order = find_own_order(ctx, order_id)
    if order is None:
        return not_found()
    reason = str(reason).strip()
    if not reason or len(reason) > 500:
        return fail("invalid_argument", "reason is required and must be at most 500 characters.")
    skus = clean_skus(item_skus)
    if error := check_return(order, skus, ctx.now):
        return error

    amount = refund_amount(order, skus)
    names = ", ".join(i["name"] for i in order["items"] if i["sku"] in skus)
    outcome = "a team member will review it" if needs_review(amount) else "approved automatically"
    summary = f"Return {names} from order {order['order_no']} for a refund of ${amount:.2f} ({outcome})."
    return propose_action(ctx, "request_return", order["order_no"], {"item_skus": skus, "reason": reason}, summary)


def create_support_ticket(ctx: ToolContext, summary: str) -> dict:
    summary = str(summary).strip()
    if not 10 <= len(summary) <= 1000:
        return fail("invalid_argument", "summary must be between 10 and 1000 characters.")

    limit = get_settings().max_tickets_per_day
    since = ctx.now - timedelta(hours=24)
    if ctx.db.tickets.count_documents({"user_id": ctx.user_id, "created_at": {"$gt": since}}) >= limit:
        return fail("rate_limited", f"You can open at most {limit} tickets in 24 hours. Please wait for a reply.")

    ticket = {
        "ticket_no": f"TCK-{secrets.randbelow(900_000) + 100_000}",
        "user_id": ctx.user_id,
        "conversation_id": ctx.conversation_id,
        "summary": summary,
        "status": "open",
        "created_at": ctx.now,
    }
    ctx.db.tickets.insert_one(ticket)
    return ok({"ticket_no": ticket["ticket_no"], "status": "open", "message": "A human agent will follow up by email."})


TOOLS = {
    "list_orders": list_orders,
    "get_order": get_order,
    "cancel_order": cancel_order,
    "request_return": request_return,
    "create_support_ticket": create_support_ticket,
}


def run_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    """Call a tool with model-chosen arguments. Anything not in its signature is refused."""
    tool = TOOLS.get(name)
    if tool is None:
        return fail("unknown_tool", f"There is no tool called {name}.")

    params = {p.name: p for p in inspect.signature(tool).parameters.values() if p.name != "ctx"}
    unexpected = sorted(set(args) - set(params))
    missing = sorted(n for n, p in params.items() if p.default is p.empty and n not in args)
    if unexpected:
        return fail("invalid_argument", f"Unexpected argument(s): {', '.join(unexpected)}.")
    if missing:
        return fail("invalid_argument", f"Missing argument(s): {', '.join(missing)}.")
    return tool(ctx, **args)
