"""The chat API and the Confirm / Cancel endpoints, with a scripted Claude."""
from datetime import timedelta

import anthropic
import httpx2
import pytest
from bson import ObjectId

from app.chat import get_claude
from app.main import app
from tests.conftest import register
from tests.fake_claude import FakeClient, calls, reply


def use_claude(*responses) -> FakeClient:
    fake = FakeClient(*responses)
    app.dependency_overrides[get_claude] = lambda: fake
    return fake


def placed_order(db, email: str) -> str:
    user = db.users.find_one({"email": email})
    return db.orders.find_one({"user_id": user["_id"], "status": "placed"})["order_no"]


@pytest.fixture
def ann(client):
    return register(client, email="ann@example.com", name="Ann")


@pytest.fixture
def ben(client):
    return register(client, email="ben@example.com", name="Ben")


def start(client, headers) -> str:
    response = client.post("/conversations", headers=headers)
    assert response.status_code == 201
    return response.json()["conversation_id"]


def say(client, headers, cid, text):
    return client.post(f"/conversations/{cid}/messages", json={"text": text}, headers=headers)


@pytest.fixture
def proposed(client, db, ann):
    """Ann has asked to cancel her placed order, and Claude has proposed it."""
    no = placed_order(db, "ann@example.com")
    use_claude(calls(("cancel_order", {"order_id": no})), reply("Press Confirm to cancel."))
    cid = start(client, ann)
    [action] = say(client, ann, cid, f"cancel {no}").json()["actions"]
    return {"cid": cid, "order_no": no, "action_id": action["action_id"], "action": action}


def press(client, headers, proposed, button="confirm", cid=None):
    return client.post(f"/actions/{proposed['action_id']}/{button}",
                       json={"conversation_id": cid or proposed["cid"]}, headers=headers)


def order_status(db, order_no) -> str:
    return db.orders.find_one({"order_no": order_no})["status"]


def notes(db, cid) -> list[str]:
    return [m["text"] for m in db.conversations.find_one({"_id": ObjectId(cid)})["messages"] if m["role"] == "system"]


# Conversations

def test_chat_round_trip(client, ann):
    use_claude(reply("Hi Ann, how can I help?"))
    cid = start(client, ann)
    response = say(client, ann, cid, "hello")
    assert response.status_code == 200
    assert response.json() == {"reply": "Hi Ann, how can I help?", "actions": []}
    history = client.get(f"/conversations/{cid}", headers=ann).json()["messages"]
    assert [(m["role"], m["text"]) for m in history] == [("user", "hello"), ("assistant", "Hi Ann, how can I help?")]


@pytest.mark.parametrize("cid", ["other", "not-an-id", "0" * 24], ids=["other_user", "bad_id", "missing"])
def test_conversation_must_be_yours(client, ann, ben, cid):
    use_claude()  # any call to Claude would fail the test: it must never be reached
    if cid == "other":
        cid = start(client, ben)
    assert client.get(f"/conversations/{cid}", headers=ann).status_code == 404
    assert say(client, ann, cid, "hello").status_code == 404


def test_chat_needs_login_and_a_sane_message(client, ann):
    use_claude()
    assert client.post("/conversations").status_code == 401
    cid = start(client, ann)
    assert say(client, ann, cid, "").status_code == 422
    assert say(client, ann, cid, "x" * 2001).status_code == 422


def test_claude_outage_is_a_clean_502(client, ann):
    class Down:
        def __init__(self):
            self.messages = self

        def create(self, **request):
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))

    app.dependency_overrides[get_claude] = Down
    response = say(client, ann, start(client, ann), "hello")
    assert response.status_code == 502 and "try again" in response.json()["detail"]


# Confirm and Cancel

def test_proposal_changes_nothing_until_confirm(client, db, ann, proposed):
    assert proposed["action"]["action"] == "cancel_order"
    assert order_status(db, proposed["order_no"]) == "placed"

    response = press(client, ann, proposed)
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert order_status(db, proposed["order_no"]) == "cancelled"


def test_confirm_note_reaches_claude_but_not_the_customer(client, db, ann, proposed):
    press(client, ann, proposed)
    fake = use_claude(reply("Yes, it is cancelled."))
    say(client, ann, proposed["cid"], "is it done?")

    last = fake.requests[-1]["messages"][-1]
    assert last["role"] == "system" and "pressed Confirm" in last["content"]
    history = client.get(f"/conversations/{proposed['cid']}", headers=ann).json()["messages"]
    assert all(m["role"] != "system" for m in history)


def test_double_click_runs_once(client, db, ann, proposed):
    assert press(client, ann, proposed).status_code == 200
    second = press(client, ann, proposed)
    assert second.status_code == 409 and "already done" in second.json()["detail"]
    assert len(notes(db, proposed["cid"])) == 1


def test_another_user_cannot_press_your_button(client, db, ben, proposed):
    use_claude()
    response = press(client, ben, proposed, cid=start(client, ben))
    assert response.status_code == 404
    assert order_status(db, proposed["order_no"]) == "placed"


def test_action_is_bound_to_its_conversation(client, db, ann, proposed):
    other_chat = start(client, ann)
    assert press(client, ann, proposed, cid=other_chat).status_code == 404
    assert order_status(db, proposed["order_no"]) == "placed"


def test_cancel_button(client, db, ann, proposed):
    response = press(client, ann, proposed, button="cancel")
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert order_status(db, proposed["order_no"]) == "placed"  # the action was cancelled, not the order
    [note] = notes(db, proposed["cid"])
    assert "pressed Cancel" in note and "Nothing was changed" in note


def test_expired_confirm_is_refused_and_claude_is_told(client, db, ann, proposed):
    action = {"_id": ObjectId(proposed["action_id"])}
    created = db.pending_actions.find_one(action)["created_at"]
    db.pending_actions.update_one(action, {"$set": {"expires_at": created - timedelta(minutes=1)}})
    response = press(client, ann, proposed)
    assert response.status_code == 409 and "expired" in response.json()["detail"]
    assert order_status(db, proposed["order_no"]) == "placed"
    [note] = notes(db, proposed["cid"])
    assert "'expired'" in note
