"""Tool definitions sent to Claude. They describe only what the model may choose.

There is no user_id parameter anywhere: the backend supplies the user from the
verified JWT when a tool runs. strict mode makes Claude's arguments always match
these schemas, which is why every parameter is required.
"""
from app.models import ORDER_STATUSES

ORDER_ID = {"type": "string", "description": "The order number, for example ORD-482913."}


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOL_SCHEMAS: list[dict] = [
    _tool(
        "list_orders",
        "List the customer's orders, newest first, as short summaries: order number, status, date, "
        "total and item names. Use get_order for tracking, SKUs, return eligibility and refunds.",
        {"status": {
            "type": "string",
            "enum": ["any", *ORDER_STATUSES],
            "description": "Only list orders with this status, or 'any' for all orders.",
        }},
    ),
    _tool(
        "get_order",
        "Get full details of one of the customer's orders: items with SKUs and returnable flags, "
        "dates, tracking events, whether it can be cancelled or returned, the return deadline, "
        "and any refund or return request.",
        {"order_id": ORDER_ID},
    ),
    _tool(
        "cancel_order",
        "Prepare the cancellation of an order that is placed or processing. This does NOT cancel it: "
        "it creates a pending action that the customer must confirm with a button. Only call it when "
        "the customer has clearly asked to cancel that specific order.",
        {"order_id": ORDER_ID},
    ),
    _tool(
        "request_return",
        "Prepare a return of items from a delivered order inside the return window. This does NOT "
        "start the return: it creates a pending action that the customer must confirm with a button. "
        "Call get_order first to get the SKUs. All units of each chosen SKU are returned.",
        {
            "order_id": ORDER_ID,
            "item_skus": {
                "type": "array",
                "items": {"type": "string"},
                "description": "SKUs of the items to return, exactly as get_order shows them.",
            },
            "reason": {"type": "string", "description": "The customer's reason, in a few words."},
        },
    ),
    _tool(
        "create_support_ticket",
        "Hand the issue to a human agent, who replies by email. Use it when the customer asks for a "
        "person or the problem cannot be solved with the other tools, after the customer agrees.",
        {"summary": {
            "type": "string",
            "description": "A short, neutral description of the issue for the human agent. "
                           "Never include passwords or payment details.",
        }},
    ),
]
