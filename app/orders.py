"""Read-only order endpoints for the logged-in user."""
from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.auth import get_current_user
from app.db import get_db
from app.models import OrderStatus

router = APIRouter(tags=["orders"])


def public_order(order: dict) -> dict:
    """The order as the customer (and later the model) may see it: no internal ids."""
    return {key: value for key, value in order.items() if key not in ("_id", "user_id")}


@router.get("/orders")
def list_orders(
    status: OrderStatus | None = None,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> list[dict]:
    query = {"user_id": user["_id"]}
    if status:
        query["status"] = status
    return [public_order(order) for order in db.orders.find(query).sort("placed_at", -1)]
