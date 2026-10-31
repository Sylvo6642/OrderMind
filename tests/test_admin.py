"""Admin review of returns and tickets."""
import pytest
from bson import ObjectId

from app.context import ToolContext
from app.tools import create_support_ticket, get_order, request_return
from tests.conftest import NOW, register


@pytest.fixture
def ann(client):
    return register(client, email="ann@example.com", name="Ann")


@pytest.fixture
def admin(client, db):
    headers = register(client, email="boss@example.com", name="Boss")
    db.users.update_one({"email": "boss@example.com"}, {"$set": {"is_admin": True}})
    return headers


def anns_review(db) -> dict:
    """The seeded return that is over the auto-approve limit and waits for review."""
    user = db.users.find_one({"email": "ann@example.com"})
    return db.return_requests.find_one({"user_id": user["_id"], "status": "pending_review"})


def listed_for_ann(client, admin, path):
    # Registering seeds orders for every account, the admin's included, so look at Ann's rows only.
    return [row for row in client.get(path, headers=admin).json() if row["customer"] == "ann@example.com"]


def anns_ctx(db) -> ToolContext:
    return ToolContext(db, db.users.find_one({"email": "ann@example.com"})["_id"], ObjectId(), NOW)


def test_admin_routes_need_an_admin(client, ann):
    rid = str(ObjectId())
    for method, path in [("get", "/admin/returns"), ("post", f"/admin/returns/{rid}/approve"),
                         ("get", "/admin/tickets"), ("post", f"/admin/tickets/{rid}/close")]:
        kwargs = {"json": {}} if method == "post" else {}
        assert getattr(client, method)(path, **kwargs).status_code == 401
        assert getattr(client, method)(path, headers=ann, **kwargs).status_code == 403


def test_admin_page_holds_no_data_and_never_uses_innerhtml(client):
    page = client.get("/admin")
    assert page.status_code == 200 and "Admin login" in page.text
    assert ".innerHTML" not in page.text and "insertAdjacentHTML" not in page.text  # textContent only


def test_lists_returns_waiting_for_review(client, db, ann, admin):
    [item] = listed_for_ann(client, admin, "/admin/returns")
    assert item["customer"] == "ann@example.com" and item["order_no"] == anns_review(db)["order_no"]
    assert item["amount"] >= 100 and item["items"] and item["reason"]


def test_approve_records_who_and_when(client, db, ann, admin):
    rid = str(anns_review(db)["_id"])
    response = client.post(f"/admin/returns/{rid}/approve", json={"note": "Photos checked"}, headers=admin)
    assert response.status_code == 200 and response.json()["status"] == "approved"

    stored = db.return_requests.find_one({"_id": ObjectId(rid)})
    assert stored["decided_by"] == "boss@example.com" and stored["decided_at"] and stored["decision_note"] == "Photos checked"
    assert get_order(anns_ctx(db), stored["order_no"])["data"]["return_request"]["status"] == "approved"
    assert listed_for_ann(client, admin, "/admin/returns") == []


def test_a_return_is_decided_once(client, db, ann, admin):
    rid = str(anns_review(db)["_id"])
    assert client.post(f"/admin/returns/{rid}/approve", json={}, headers=admin).status_code == 200
    again = client.post(f"/admin/returns/{rid}/reject", json={"note": "Changed my mind"}, headers=admin)
    assert again.status_code == 409
    assert db.return_requests.find_one({"_id": ObjectId(rid)})["status"] == "approved"


def test_reject_needs_a_reason(client, db, ann, admin):
    rid = str(anns_review(db)["_id"])
    assert client.post(f"/admin/returns/{rid}/reject", json={"note": ""}, headers=admin).status_code == 422
    assert db.return_requests.find_one({"_id": ObjectId(rid)})["status"] == "pending_review"


def test_rejection_is_final_for_the_order(client, db, ann, admin):
    review = anns_review(db)
    response = client.post(f"/admin/returns/{review['_id']}/reject", json={"note": "Damage not covered"}, headers=admin)
    assert response.status_code == 200

    ctx = anns_ctx(db)
    detail = get_order(ctx, review["order_no"])["data"]
    assert detail["status"] == "return_rejected" and detail["can_return"] is False
    again = request_return(ctx, review["order_no"], review["item_skus"], "Please reconsider")
    assert again["error"] == "not_allowed"


@pytest.mark.parametrize("rid", ["0" * 24, "not-an-id"], ids=["missing", "bad_id"])
def test_unknown_return_is_404(client, admin, rid):
    assert client.post(f"/admin/returns/{rid}/approve", json={}, headers=admin).status_code == 404


def test_tickets_can_be_listed_and_closed_once(client, db, ann, admin):
    ticket = create_support_ticket(anns_ctx(db), "My parcel says delivered but it is not here.")["data"]
    [listed] = listed_for_ann(client, admin, "/admin/tickets")
    assert listed["ticket_no"] == ticket["ticket_no"] and listed["customer"] == "ann@example.com"

    closed = client.post(f"/admin/tickets/{listed['id']}/close", json={"note": "Carrier found it"}, headers=admin)
    assert closed.status_code == 200
    stored = db.tickets.find_one({"_id": ObjectId(listed["id"])})
    assert stored["status"] == "closed" and stored["closed_by"] == "boss@example.com"
    assert client.post(f"/admin/tickets/{listed['id']}/close", json={}, headers=admin).status_code == 409
    assert listed_for_ann(client, admin, "/admin/tickets") == []
