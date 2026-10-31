"""Chat with OrderMind in the terminal as a real (demo) customer.

    python -m scripts.chat alice@demo.local

Commands: /new starts a new conversation, /quit exits.
Confirm and Cancel buttons arrive in Stage 4; until then this script asks you.
"""
import getpass
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone

import anthropic

from app.actions import cancel_action, confirm_action
from app.agent import handle_message
from app.context import ToolContext
from app.conversations import action_note, add_message, start_conversation
from app.db import ensure_indexes, get_db
from app.security import verify_password


def now() -> datetime:
    return datetime.now(timezone.utc)


def login(db, email: str) -> dict:
    user = db.users.find_one({"email": email.strip().lower()})
    password = getpass.getpass(f"Password for {email}: ")
    if not verify_password(user["password_hash"] if user else None, password):
        sys.exit("Invalid email or password")
    return user


def ask_about(ctx: ToolContext, proposal: dict) -> None:
    print(f"\n  Pending action: {proposal['summary']}")
    choice = input("  [c]onfirm / [x] cancel / [enter] skip > ").strip().lower()
    if choice not in ("c", "x"):
        print("  Skipped. It expires on its own.\n")
        return
    ctx = replace(ctx, now=now())
    confirmed = choice == "c"
    outcome = confirm_action(ctx, proposal["action_id"]) if confirmed else cancel_action(ctx, proposal["action_id"])
    print(f"  {'Done' if outcome['ok'] else 'Not done'}: {outcome.get('data') or outcome.get('message')}\n")
    if outcome["ok"] or confirmed:  # tell Claude what happened on its next turn
        add_message(ctx.db, ctx.conversation_id, "system", action_note(proposal, outcome, confirmed), ctx.now)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python -m scripts.chat EMAIL")

    db = get_db()  # importing app.config has already loaded .env
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY in .env first")
    ensure_indexes(db)
    user = login(db, sys.argv[1])
    client = anthropic.Anthropic()

    started = now()
    ctx = ToolContext(db, user["_id"], start_conversation(db, user["_id"], started), started)
    print(f"Hi {user['name']}. Ask about your orders. /new for a new conversation, /quit to exit.\n")

    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/new":
            ctx = replace(ctx, conversation_id=start_conversation(db, user["_id"], now()))
            print("(new conversation)\n")
            continue

        ctx = replace(ctx, now=now())
        try:
            turn = handle_message(client, ctx, text)
        except anthropic.APIError as error:
            print(f"(Claude API error: {error.__class__.__name__}. Try again.)\n")
            continue
        print(f"\nOrderMind> {turn.reply}\n   [{turn.tool_calls} tool call(s), stop: {turn.stop}]\n")
        for proposal in turn.pending_actions:
            ask_about(ctx, proposal)


if __name__ == "__main__":
    main()
