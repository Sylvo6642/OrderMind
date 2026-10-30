from datetime import datetime, timedelta, timezone

import jwt

from tests.conftest import PASSWORD, register


def test_register_returns_working_token(client):
    headers = register(client)
    me = client.get("/me", headers=headers).json()
    assert me["email"] == "ann@example.com"
    assert me["is_admin"] is False
    assert "password_hash" not in me


def test_email_is_normalised_and_unique(client):
    register(client, email="Ann@Example.com ")
    response = client.post(
        "/auth/register", json={"email": "ann@example.com", "name": "Ann 2", "password": PASSWORD}
    )
    assert response.status_code == 409


def test_password_is_stored_as_argon2_hash(client, db):
    register(client)
    stored = db.users.find_one({"email": "ann@example.com"})["password_hash"]
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


def test_new_user_has_empty_memory_profile(client, db):
    register(client)
    profile = db.users.find_one({"email": "ann@example.com"})["memory_profile"]
    assert profile == {"preferred_language": None, "notes": [], "last_issue": None}


def test_register_rejects_bad_input(client):
    bad_bodies = [
        {"email": "not-an-email", "name": "Ann", "password": PASSWORD},
        {"email": "ann@example.com", "name": "Ann", "password": "short"},
        {"email": "ann@example.com", "name": "   ", "password": PASSWORD},
    ]
    for body in bad_bodies:
        assert client.post("/auth/register", json=body).status_code == 422


def test_login(client):
    register(client)
    ok = client.post("/auth/login", json={"email": "ANN@example.com", "password": PASSWORD})
    assert ok.status_code == 200
    assert client.get("/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"}).status_code == 200


def test_login_failures_look_the_same(client):
    register(client)
    wrong_password = client.post("/auth/login", json={"email": "ann@example.com", "password": "wrong-password"})
    unknown_email = client.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def _token(secret: str, minutes: int, sub: str = "000000000000000000000000") -> dict:
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": sub, "exp": now + timedelta(minutes=minutes)}, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_bad_tokens_are_rejected(client, db):
    register(client)
    user_id = str(db.users.find_one()["_id"])
    real_secret = "test-secret-that-is-at-least-32-characters-long"

    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert client.get("/me", headers=_token(real_secret, -1, user_id)).status_code == 401  # expired
    assert client.get("/me", headers=_token("x" * 40, 10, user_id)).status_code == 401  # wrong key
    assert client.get("/me", headers=_token(real_secret, 10)).status_code == 401  # user does not exist
    assert client.get("/me", headers=_token(real_secret, 10, user_id)).status_code == 200
