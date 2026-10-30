"""Creating users. Shared by the register endpoint and the demo seed script."""
from datetime import datetime, timezone

from pymongo.database import Database

from app.models import empty_memory_profile
from app.security import hash_password
from app.seed import seed_orders_for_user


def create_user(db: Database, email: str, name: str, password: str, is_admin: bool = False) -> dict:
    """Insert a user and seed their fake orders. Raises DuplicateKeyError if the email is taken."""
    user = {
        "email": email.strip().lower(),
        "password_hash": hash_password(password),
        "name": name,
        "is_admin": is_admin,
        "memory_profile": empty_memory_profile(),
        "created_at": datetime.now(timezone.utc),
    }
    db.users.insert_one(user)
    seed_orders_for_user(db, user["_id"])
    return user
