"""The agent loop: Claude asks for tools, our code runs them for the logged-in user.

Within one turn, Claude's full replies (thinking and tool calls) are sent back
unchanged, as the API requires. Between turns, only text is stored (see
conversations.py). The loop stops when Claude answers, or after the tool-call
cap, when Claude must answer with what it has.
"""
import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.context import ToolContext, fail
from app.conversations import add_message, get_conversation, to_api_messages
from app.tool_schemas import TOOL_SCHEMAS
from app.tools import run_tool
from app.traces import log_trace, usage_dict

PROMPTS = Path(__file__).parent / "prompts"

REFUSAL_REPLY = "Sorry, I can't help with that. I can help with your orders, returns and store policy."
FALLBACK_REPLY = "Sorry, something went wrong on my side. Please try again, or ask me to create a support ticket."


@dataclass
class TurnResult:
    reply: str
    stop: str  # end_turn, tool_cap, refusal, max_tokens
    tool_calls: int = 0
    pending_actions: list[dict] = field(default_factory=list)  # needs_confirmation results


@lru_cache
def static_system_prompt() -> str:
    """Rules plus policy, with the numbers filled in from config.

    It never changes between requests, so it sits in the cached prefix together
    with the tools. Anything per-request goes after the cache breakpoint.
    """
    s = get_settings()
    policy = (PROMPTS / "policy.md").read_text(encoding="utf-8").format(
        return_window_days=s.return_window_days,
        auto_approve_refund_limit=s.auto_approve_refund_limit,
        max_tickets_per_day=s.max_tickets_per_day,
    )
    rules = (PROMPTS / "system.md").read_text(encoding="utf-8")
    return f"{rules}\n\n{policy}"


def system_blocks(ctx: ToolContext) -> list[dict]:
    return [
        {"type": "text", "text": static_system_prompt(), "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"Today's date is {ctx.now:%Y-%m-%d} (UTC)."},
    ]


def call_model(client, ctx: ToolContext, messages: list, tools_allowed: bool):
    s = get_settings()
    request = {
        "model": s.anthropic_model,
        "max_tokens": s.anthropic_max_tokens,
        "output_config": {"effort": s.anthropic_effort},
        "system": system_blocks(ctx),
        "tools": TOOL_SCHEMAS,  # always sent, so the cached prefix stays the same
        "messages": messages,
    }
    if not tools_allowed:
        request["tool_choice"] = {"type": "none"}

    start = time.perf_counter()
    response = client.messages.create(**request)
    log_trace(
        ctx, "model_call", int((time.perf_counter() - start) * 1000),
        model=s.anthropic_model, stop_reason=response.stop_reason, tokens=usage_dict(response.usage),
    )
    return response


def execute_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    start = time.perf_counter()
    result = run_tool(ctx, name, args)
    log_trace(ctx, "tool_call", int((time.perf_counter() - start) * 1000), tool=name, args=args, result=result)
    return result


def run_agent(client, ctx: ToolContext, messages: list) -> TurnResult:
    """Run one customer turn. `messages` ends with the customer's message."""
    cap = get_settings().max_tool_calls_per_turn
    turn = TurnResult(reply="", stop="end_turn")

    for _ in range(cap + 2):  # each round uses at least one tool call, so this is a hard stop
        response = call_model(client, ctx, messages, tools_allowed=turn.tool_calls < cap)

        if response.stop_reason == "refusal":
            turn.reply, turn.stop = REFUSAL_REPLY, "refusal"
            return turn
        if response.stop_reason == "max_tokens":
            turn.reply, turn.stop = FALLBACK_REPLY, "max_tokens"
            return turn

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            turn.reply = "".join(b.text for b in response.content if b.type == "text").strip() or FALLBACK_REPLY
            turn.stop = "tool_cap" if turn.tool_calls >= cap else "end_turn"
            return turn

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in tool_uses:
            turn.tool_calls += 1
            if turn.tool_calls > cap:
                result = fail("tool_limit", "Tool call limit for this turn reached. Answer with what you have.")
            else:
                result = execute_tool(ctx, block.name, block.input)
            if result["ok"] and result["data"].get("needs_confirmation"):
                turn.pending_actions.append(result["data"])
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False),
                "is_error": not result["ok"],
            })
        messages.append({"role": "user", "content": results})

    turn.reply, turn.stop = FALLBACK_REPLY, "tool_cap"
    return turn


def handle_message(client, ctx: ToolContext, text: str) -> TurnResult:
    """Load the conversation, run the agent, store the customer's text and the reply."""
    conversation = get_conversation(ctx.db, ctx.conversation_id, ctx.user_id)
    if conversation is None:
        raise LookupError("Conversation not found for this user")

    entries = conversation["messages"] + [{"role": "user", "text": text}]
    messages = to_api_messages(entries, get_settings().history_messages)
    turn = run_agent(client, ctx, messages)

    add_message(ctx.db, ctx.conversation_id, "user", text, ctx.now)
    add_message(ctx.db, ctx.conversation_id, "assistant", turn.reply, ctx.now)
    return turn
