from app.seed import ORDER_TEMPLATES
from tests.conftest import register


def test_new_user_can_fetch_seeded_orders(client):
    orders = client.get("/orders", headers=register(client)).json()
    assert len(orders) == len(ORDER_TEMPLATES)
    assert {o["status"] for o in orders} == {
        "placed", "processing", "shipped", "delivered", "cancelled", "return_requested", "returned"
    }


def test_orders_are_newest_first(client):
    orders = client.get("/orders", headers=register(client)).json()
    placed = [o["placed_at"] for o in orders]
    assert placed == sorted(placed, reverse=True)


def test_users_only_see_their_own_orders(client):
    ann = client.get("/orders", headers=register(client, email="ann@example.com")).json()
    ben = client.get("/orders", headers=register(client, email="ben@example.com")).json()
    assert {o["order_no"] for o in ann}.isdisjoint({o["order_no"] for o in ben})


def test_orders_hide_internal_ids(client):
    order = client.get("/orders", headers=register(client)).json()[0]
    assert "_id" not in order
    assert "user_id" not in order


def test_status_filter(client):
    headers = register(client)
    delivered = client.get("/orders", params={"status": "delivered"}, headers=headers).json()
    assert delivered and all(o["status"] == "delivered" for o in delivered)
    assert client.get("/orders", params={"status": "lost"}, headers=headers).status_code == 422


def test_orders_require_login(client):
    assert client.get("/orders").status_code == 401
