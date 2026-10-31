"""Stored conversations and how they are turned into Claude messages.

Between turns we keep only plain text: what the customer wrote, the assistant's
final reply, and backend notes. Tool calls and thinking stay inside the turn
that produced them, so trimming old history can never break a later request.
"""
from datetime import datetime

from bson import ObjectId
from pymongo.database import Database


def start_conversation(db: Database, user_id: ObjectId, now: datetime) -> ObjectId:
    result = db.conversations.insert_one(
        {"user_id": user_id, "messages": [], "summary": None, "status": "active", "created_at": now, "updated_at": now}
    )
    return result.inserted_id


def get_conversation(db: Database, conversation_id: ObjectId, user_id: ObjectId) -> dict | None:
    return db.conversations.find_one({"_id": conversation_id, "user_id": user_id})


def add_message(db: Database, conversation_id: ObjectId, role: str, text: str, now: datetime) -> None:
    """role is "user", "assistant" or "system" (a backend note, never customer text)."""
    db.conversations.update_one(
        {"_id": conversation_id},
        {"$push": {"messages": {"role": role, "text": text, "at": now}}, "$set": {"updated_at": now}},
    )


def to_api_messages(entries: list[dict], limit: int) -> list[dict]:
    """Keep the last `limit` entries and shape them for the Messages API.

    A mid-conversation system message must come right after a user message,
    so a backend note is moved to just after the next customer message.
    """
    window = entries[-limit:]
    while window and window[0]["role"] != "user":
        window = window[1:]

    messages, waiting_notes = [], []
    for entry in window:
        if entry["role"] == "system":
            waiting_notes.append(entry["text"])
            continue
        messages.append({"role": entry["role"], "content": entry["text"]})
        if entry["role"] == "user" and waiting_notes:
            messages.append({"role": "system", "content": "\n".join(waiting_notes)})
            waiting_notes = []
    return messages


def action_note(proposal: dict, outcome: dict, confirmed: bool) -> str:
    """The backend note Claude sees after a Confirm or Cancel click.

    Built only from fields our code controls (action type, order number, status,
    amounts, error codes), never from product names or other stored text.
    """
    action, order_no = proposal["action"], proposal["order_no"]
    # Live testing showed Claude can mistake a note placed after the customer's message for
    # text it wrote itself, so the note says who sent it and when the click happened.
    button = "Confirm" if confirmed else "Cancel"
    prefix = (f"Backend note from the store's system (not written by you): before their latest message, "
              f"the customer pressed {button} for the {action} on order {order_no}.")
    if not confirmed:
        return f"{prefix} Nothing was changed."
    if not outcome["ok"]:
        return f"{prefix} It failed with error '{outcome['error']}'. Nothing was changed."
    data = outcome["data"]
    if action == "cancel_order":
        return f"{prefix} The order is now cancelled. Refund: ${data['refund_amount']:.2f}."
    return (f"{prefix} The return was created with status '{data['return_status']}'. "
            f"Refund amount: ${data['refund_amount']:.2f}.")
