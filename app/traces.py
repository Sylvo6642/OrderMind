"""One trace document per model call and per tool call. Used for debugging and, later, evals."""
from datetime import datetime, timezone

from app.context import ToolContext


def log_trace(ctx: ToolContext, event: str, latency_ms: int, **fields) -> None:
    ctx.db.traces.insert_one({
        "timestamp": datetime.now(timezone.utc),
        "user_id": ctx.user_id,
        "conversation_id": ctx.conversation_id,
        "event": event,  # "model_call" or "tool_call"
        "latency_ms": latency_ms,
        **fields,
    })


def usage_dict(usage) -> dict:
    """Token counts from a Claude response, including prompt cache reads and writes."""
    names = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return {name: getattr(usage, name, 0) or 0 for name in names}
