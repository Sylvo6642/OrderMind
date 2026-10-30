"""Settings. Secrets come from .env; business rules have defaults here.

The system prompt and the tools both read the business rules from this
one place, so they can never disagree.
"""
import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str
    mongodb_db: str
    jwt_secret: str
    jwt_expire_minutes: int
    anthropic_model: str

    # Business rules
    return_window_days: int
    auto_approve_refund_limit: float
    max_tool_calls_per_turn: int
    history_messages: int


@lru_cache
def get_settings() -> Settings:
    jwt_secret = os.getenv("JWT_SECRET", "")
    if len(jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET must be set in .env and be at least 32 characters long")

    return Settings(
        mongodb_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
        mongodb_db=os.getenv("MONGODB_DB", "ordermind"),
        jwt_secret=jwt_secret,
        jwt_expire_minutes=int(os.getenv("JWT_EXPIRE_MINUTES", "60")),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5"),
        return_window_days=int(os.getenv("RETURN_WINDOW_DAYS", "14")),
        auto_approve_refund_limit=float(os.getenv("AUTO_APPROVE_REFUND_LIMIT", "100")),
        max_tool_calls_per_turn=int(os.getenv("MAX_TOOL_CALLS_PER_TURN", "5")),
        history_messages=int(os.getenv("HISTORY_MESSAGES", "12")),
    )
