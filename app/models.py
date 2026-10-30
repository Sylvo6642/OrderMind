"""Document shapes for MongoDB. These are type hints only: we store plain dicts.

Collections added in later stages (conversations, traces) get their shapes
here when we build them.
"""
from datetime import datetime
from typing import Literal, NotRequired, TypedDict

from bson import ObjectId

OrderStatus = Literal[
    "placed", "processing", "shipped", "delivered", "cancelled", "return_requested", "returned"
]
ORDER_STATUSES: tuple[str, ...] = OrderStatus.__args__

ReturnStatus = Literal["auto_approved", "pending_review", "approved", "rejected"]


class MemoryProfile(TypedDict):
    preferred_language: str | None
    notes: list[str]  # max 10
    last_issue: str | None


class User(TypedDict):
    _id: ObjectId
    email: str
    password_hash: str
    name: str
    is_admin: bool
    memory_profile: MemoryProfile
    created_at: datetime


class OrderItem(TypedDict):
    sku: str
    name: str
    qty: int
    price: float  # unit price
    returnable: bool


class TrackingEvent(TypedDict):
    at: datetime
    status: str
    location: str


class Tracking(TypedDict):
    carrier: str
    eta: datetime | None
    events: list[TrackingEvent]


class Order(TypedDict):
    _id: ObjectId
    order_no: str  # e.g. ORD-482913, the only id the model ever sees
    user_id: ObjectId
    items: list[OrderItem]
    total: float
    status: OrderStatus
    placed_at: datetime
    shipped_at: datetime | None
    delivered_at: datetime | None
    tracking: Tracking | None
    cancelled_at: NotRequired[datetime]  # set when the customer cancels
    refund_amount: NotRequired[float]  # set when the customer cancels


class ReturnRequest(TypedDict):
    _id: ObjectId
    order_no: str
    user_id: ObjectId
    item_skus: list[str]
    reason: str
    amount: float
    status: ReturnStatus
    decided_by: str | None  # "auto" or an admin email
    created_at: datetime


ActionStatus = Literal["pending", "executing", "done", "failed", "cancelled", "expired", "superseded"]


class PendingAction(TypedDict):
    _id: ObjectId
    user_id: ObjectId
    conversation_id: ObjectId
    type: Literal["cancel_order", "request_return"]
    order_no: str
    params: dict  # e.g. {"item_skus": [...], "reason": "..."}
    summary: str  # what the Confirm button will do, in plain words
    status: ActionStatus
    created_at: datetime
    expires_at: datetime
    result: NotRequired[dict]  # the executor's result, once confirmed
    finished_at: NotRequired[datetime]


class Ticket(TypedDict):
    _id: ObjectId
    ticket_no: str  # e.g. TCK-104233
    user_id: ObjectId
    conversation_id: ObjectId
    summary: str
    status: Literal["open", "closed"]
    created_at: datetime


def empty_memory_profile() -> MemoryProfile:
    return {"preferred_language": None, "notes": [], "last_issue": None}
