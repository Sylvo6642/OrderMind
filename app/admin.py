"""Admin endpoints: review returns over the auto-approve limit and close support tickets.

Every route checks is_admin on the server; the /admin page is only a viewer.
Each decision records who made it, when and why.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.database import Database

from app.auth import get_current_user
from app.db import get_db
from app.models import ReturnStatus

router = APIRouter(prefix="/admin", tags=["admin"])
PAGE = Path(__file__).parent / "static" / "admin.html"


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return user


class DecisionIn(BaseModel):
    note: str = Field(default="", max_length=500)


class RejectIn(BaseModel):
    note: str = Field(min_length=5, max_length=500)  # the customer deserves a reason


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


def _emails(db: Database, user_ids: set) -> dict:
    return {u["_id"]: u["email"] for u in db.users.find({"_id": {"$in": list(user_ids)}}, {"email": 1})}


@router.get("", include_in_schema=False)
def admin_page() -> FileResponse:
    """The page holds no data. It asks for an admin login and then calls the routes below."""
    return FileResponse(PAGE)


@router.get("/returns")
def list_returns(state: ReturnStatus = "pending_review", admin: dict = Depends(require_admin),
                 db: Database = Depends(get_db)) -> list[dict]:
    returns = list(db.return_requests.find({"status": state}).sort("created_at", 1))
    emails = _emails(db, {r["user_id"] for r in returns})
    orders = {o["order_no"]: o for o in db.orders.find({"order_no": {"$in": [r["order_no"] for r in returns]}})}
    out = []
    for r in returns:
        items = orders[r["order_no"]]["items"] if r["order_no"] in orders else []
        out.append({
            "id": str(r["_id"]),
            "order_no": r["order_no"],
            "customer": emails.get(r["user_id"], "unknown"),
            "items": [f"{i['qty']} x {i['name']}" for i in items if i["sku"] in r["item_skus"]],
            "amount": r["amount"],
            "reason": r["reason"],
            "status": r["status"],
            "created_at": r["created_at"],
            **{k: r[k] for k in ("decided_by", "decided_at", "decision_note") if r.get(k)},
        })
    return out


def _decide(db: Database, return_id: str, admin: dict, approve: bool, note: str) -> dict:
    now = _now()
    decision = {"status": "approved" if approve else "rejected", "decided_by": admin["email"],
                "decided_at": now, "decision_note": note.strip()}
    # Atomic: only a return still waiting for review can be decided, so two admins cannot both decide it.
    ret = db.return_requests.find_one_and_update(
        {"_id": _oid(return_id), "status": "pending_review"}, {"$set": decision},
        return_document=ReturnDocument.AFTER,
    )
    if ret is None:
        if db.return_requests.find_one({"_id": _oid(return_id)}) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        raise HTTPException(status.HTTP_409_CONFLICT, "This return has already been decided.")
    if not approve:
        # A rejection is final: the order can never get a second return request.
        db.orders.update_one({"order_no": ret["order_no"], "status": "return_requested"},
                             {"$set": {"status": "return_rejected"}})
    return {"id": return_id, "order_no": ret["order_no"], "status": ret["status"], "amount": ret["amount"]}


@router.post("/returns/{return_id}/approve")
def approve_return(return_id: str, body: DecisionIn, admin: dict = Depends(require_admin),
                   db: Database = Depends(get_db)) -> dict:
    return _decide(db, return_id, admin, True, body.note)


@router.post("/returns/{return_id}/reject")
def reject_return(return_id: str, body: RejectIn, admin: dict = Depends(require_admin),
                  db: Database = Depends(get_db)) -> dict:
    return _decide(db, return_id, admin, False, body.note)


@router.get("/tickets")
def list_tickets(state: Literal["open", "closed"] = "open", admin: dict = Depends(require_admin),
                 db: Database = Depends(get_db)) -> list[dict]:
    tickets = list(db.tickets.find({"status": state}).sort("created_at", 1))
    emails = _emails(db, {t["user_id"] for t in tickets})
    return [{
        "id": str(t["_id"]),
        "ticket_no": t["ticket_no"],
        "customer": emails.get(t["user_id"], "unknown"),
        "summary": t["summary"],
        "status": t["status"],
        "created_at": t["created_at"],
        **{k: t[k] for k in ("closed_by", "closed_at", "close_note") if t.get(k)},
    } for t in tickets]


@router.post("/tickets/{ticket_id}/close")
def close_ticket(ticket_id: str, body: DecisionIn, admin: dict = Depends(require_admin),
                 db: Database = Depends(get_db)) -> dict:
    ticket = db.tickets.find_one_and_update(
        {"_id": _oid(ticket_id), "status": "open"},
        {"$set": {"status": "closed", "closed_by": admin["email"], "closed_at": _now(), "close_note": body.note.strip()}},
        return_document=ReturnDocument.AFTER,
    )
    if ticket is None:
        if db.tickets.find_one({"_id": _oid(ticket_id)}) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        raise HTTPException(status.HTTP_409_CONFLICT, "This ticket is already closed.")
    return {"id": ticket_id, "ticket_no": ticket["ticket_no"], "status": "closed"}
