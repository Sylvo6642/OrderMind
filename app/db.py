"""MongoDB connection and indexes. Plain PyMongo, no ODM."""
from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from app.config import get_settings


@lru_cache
def _client() -> MongoClient:
    # tz_aware so datetimes come back as UTC-aware, matching what we store.
    return MongoClient(get_settings().mongodb_uri, tz_aware=True)


def get_db() -> Database:
    """FastAPI dependency. Tests override this with an in-memory database."""
    return _client()[get_settings().mongodb_db]


def ensure_indexes(db: Database) -> None:
    db.users.create_index("email", unique=True)
    db.orders.create_index("order_no", unique=True)
    db.orders.create_index([("user_id", ASCENDING), ("placed_at", DESCENDING)])
    db.return_requests.create_index("order_no")
    db.return_requests.create_index("status")
    # No TTL index on pending_actions: we keep finished ones as an audit trail,
    # and the code checks expires_at itself.
    db.pending_actions.create_index([("user_id", ASCENDING), ("order_no", ASCENDING), ("status", ASCENDING)])
    db.tickets.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    db.tickets.create_index("status")
    db.conversations.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
