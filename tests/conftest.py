import os

# Set before any app import so the real .env never leaks into tests.
os.environ["JWT_SECRET"] = "test-secret-that-is-at-least-32-characters-long"
os.environ["MONGODB_URI"] = "mongodb://unused-in-tests"

from datetime import datetime, timezone

import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.context import ToolContext
from app.db import ensure_indexes, get_db
from app.main import app
from app.seed import seed_orders_for_user

PASSWORD = "correct-horse-battery"
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    database = mongomock.MongoClient(tz_aware=True)["ordermind_test"]
    ensure_indexes(database)
    return database


@pytest.fixture
def client(db):
    # No "with" block, so the lifespan (which talks to the real DB) does not run.
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def make_ctx(db) -> ToolContext:
    """A new customer with seeded orders, as the backend would build it from their JWT."""
    user_id = ObjectId()
    seed_orders_for_user(db, user_id, now=NOW)
    return ToolContext(db=db, user_id=user_id, conversation_id=ObjectId(), now=NOW)


@pytest.fixture
def ann(db) -> ToolContext:
    return make_ctx(db)


@pytest.fixture
def ben(db) -> ToolContext:
    return make_ctx(db)


def order_no(ctx: ToolContext, status: str, delivered_days_ago: int | None = None) -> str:
    """Find one of the user's seeded orders by status (and delivery age)."""
    for order in ctx.db.orders.find({"user_id": ctx.user_id, "status": status}):
        if delivered_days_ago is None or (NOW - order["delivered_at"]).days == delivered_days_ago:
            return order["order_no"]
    raise LookupError(status)


def register(client, email="ann@example.com", name="Ann", password=PASSWORD) -> dict:
    """Register a user and return auth headers for them."""
    response = client.post("/auth/register", json={"email": email, "name": name, "password": password})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
