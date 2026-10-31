"""Chat over HTTP: conversations, messages and the Confirm / Cancel buttons.

The user id always comes from the JWT. A conversation or action that belongs
to someone else answers 404, exactly like one that does not exist.
"""
from dataclasses import replace
from datetime import datetime, timezone
from functools import lru_cache

import anthropic
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from pymongo.database import Database

from app.actions import cancel_action, confirm_action
from app.agent import handle_message
from app.auth import get_current_user
from app.context import ToolContext
from app.conversations import action_note, add_message, get_conversation, start_conversation
from app.db import get_db

router = APIRouter(tags=["chat"])


@lru_cache
def get_claude() -> anthropic.Anthropic:
    """FastAPI dependency. Tests override it with a scripted fake."""
    return anthropic.Anthropic()


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ButtonIn(BaseModel):
    conversation_id: str  # the chat the button was shown in; the action must belong to it


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


def _own_conversation(db: Database, conversation_id: str, user: dict) -> dict:
    conversation = get_conversation(db, _oid(conversation_id), user["_id"])
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return conversation


def press_button(ctx: ToolContext, action_id: str, confirmed: bool) -> dict:
    """Run a Confirm or Cancel click and leave a backend note for Claude's next turn.

    Shared by the API and the terminal chat, so both behave the same.
    """
    action = ctx.db.pending_actions.find_one(
        {"_id": ObjectId(action_id), "user_id": ctx.user_id, "conversation_id": ctx.conversation_id}
    ) if ObjectId.is_valid(action_id) else None
    outcome = confirm_action(ctx, action_id) if confirmed else cancel_action(ctx, action_id)
    # A repeat click on a finished action changes nothing, so it gets no second note.
    if action and (outcome["ok"] or confirmed and outcome["error"] != "not_pending"):
        proposal = {"action": action["type"], "order_no": action["order_no"]}
        add_message(ctx.db, ctx.conversation_id, "system", action_note(proposal, outcome, confirmed), ctx.now)
    return outcome


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def new_conversation(user: dict = Depends(get_current_user), db: Database = Depends(get_db)) -> dict:
    return {"conversation_id": str(start_conversation(db, user["_id"], _now()))}


@router.get("/conversations/{conversation_id}")
def read_conversation(conversation_id: str, user: dict = Depends(get_current_user),
                      db: Database = Depends(get_db)) -> dict:
    conversation = _own_conversation(db, conversation_id, user)
    # Backend notes are for Claude, not the customer, so only the chat itself is returned.
    messages = [{"role": m["role"], "text": m["text"], "at": m["at"]}
                for m in conversation["messages"] if m["role"] in ("user", "assistant")]
    return {"conversation_id": conversation_id, "messages": messages}


@router.post("/conversations/{conversation_id}/messages")
def send_message(conversation_id: str, body: MessageIn, user: dict = Depends(get_current_user),
                 db: Database = Depends(get_db), claude=Depends(get_claude)) -> dict:
    conversation = _own_conversation(db, conversation_id, user)
    ctx = ToolContext(db, user["_id"], conversation["_id"], _now())
    try:
        turn = handle_message(claude, ctx, body.text.strip())
    except anthropic.APIError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The assistant is unavailable. Please try again.")
    actions = [{k: p[k] for k in ("action_id", "action", "order_no", "summary", "expires_in_minutes")}
               for p in turn.pending_actions]
    return {"reply": turn.reply, "actions": actions}


def _button(action_id: str, body: ButtonIn, user: dict, db: Database, confirmed: bool) -> dict:
    conversation = _own_conversation(db, body.conversation_id, user)
    ctx = ToolContext(db, user["_id"], conversation["_id"], _now())
    outcome = press_button(ctx, action_id, confirmed)
    if outcome["ok"]:
        return outcome["data"]
    code = status.HTTP_404_NOT_FOUND if outcome["error"] == "not_found" else status.HTTP_409_CONFLICT
    raise HTTPException(code, outcome["message"])


@router.post("/actions/{action_id}/confirm")
def confirm(action_id: str, body: ButtonIn, user: dict = Depends(get_current_user),
            db: Database = Depends(get_db)) -> dict:
    return _button(action_id, body, user, db, confirmed=True)


@router.post("/actions/{action_id}/cancel")
def cancel(action_id: str, body: ButtonIn, user: dict = Depends(get_current_user),
           db: Database = Depends(get_db)) -> dict:
    return _button(action_id, body, user, db, confirmed=False)
