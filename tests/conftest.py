import os

# Set before any app import so the real .env never leaks into tests.
os.environ["JWT_SECRET"] = "test-secret-that-is-at-least-32-characters-long"
os.environ["MONGODB_URI"] = "mongodb://unused-in-tests"

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.db import ensure_indexes, get_db
from app.main import app

PASSWORD = "correct-horse-battery"


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


def register(client, email="ann@example.com", name="Ann", password=PASSWORD) -> dict:
    """Register a user and return auth headers for them."""
    response = client.post("/auth/register", json={"email": email, "name": name, "password": password})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
