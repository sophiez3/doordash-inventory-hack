"""
Run with:  pytest -v
Each test starts from a fresh, re-seeded database.
"""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import main  # imported here so it sees the temporary DB_PATH
    with TestClient(main.app) as c:  # "with" runs the startup (creates + seeds the DB)
        yield c


def stock(client, ingredient):
    rows = client.get("/inventory").json()
    return next(r["quantity_on_hand"] for r in rows if r["ingredient"] == ingredient)


def available(client):
    return {m["name"]: m["is_available"] for m in client.get("/menu").json()}


def order(client, *lines):
    return client.post(
        "/orders", json={"items": [{"menu_item_id": i, "quantity": q} for i, q in lines]}
    )


def test_everything_available_at_start(client):
    assert all(available(client).values())


def test_order_deducts_ingredients(client):
    r = order(client, (1, 1))  # 1 Classic Burger
    assert r.status_code == 201
    assert stock(client, "beef_patty") == 5
    assert stock(client, "burger_bun") == 9
    assert stock(client, "lettuce") == 9


def test_selling_out_flips_dependent_dishes(client):
    # 3 double cheeseburgers use all 6 patties.
    assert order(client, (3, 3)).status_code == 201
    menu = available(client)
    assert menu["Classic Burger"] is False       # needs a patty
    assert menu["Cheeseburger"] is False         # needs a patty
    assert menu["Double Cheeseburger"] is False
    assert menu["Chicken Sandwich"] is True      # unaffected
    assert menu["Fries"] is True


def test_dish_needing_two_is_unavailable_with_only_one_left(client):
    assert order(client, (1, 5)).status_code == 201   # 5 classics -> 1 patty left
    menu = available(client)
    assert menu["Double Cheeseburger"] is False       # needs 2, only 1 left
    assert menu["Cheeseburger"] is True               # needs 1, still OK


def test_failed_order_changes_nothing(client):
    # Fries are fine, but 7 classic burgers need 7 patties and we only have 6.
    r = order(client, (6, 2), (1, 7))
    assert r.status_code == 409
    assert stock(client, "potato") == 12      # fries were NOT deducted either
    assert stock(client, "beef_patty") == 6


def test_restock_brings_dishes_back(client):
    order(client, (3, 3))
    assert available(client)["Classic Burger"] is False
    r = client.post("/shipments",
                    json={"ingredient": "beef_patty", "quantity": 10, "unit_price_cents": 150})
    assert r.status_code == 201
    assert available(client)["Classic Burger"] is True


def test_unknown_menu_item_and_bad_quantity(client):
    assert order(client, (999, 1)).status_code == 404
    assert order(client, (1, 0)).status_code == 422   # rejected by validation


def test_last_portion_race_condition(client):
    # Leave exactly ONE patty, then have two customers order it at the same moment.
    assert order(client, (1, 5)).status_code == 201
    assert stock(client, "beef_patty") == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: order(client, (2, 1)).status_code, range(2)))
    assert sorted(results) == [201, 409]   # exactly one wins
    assert stock(client, "beef_patty") == 0  # and we never go negative


def test_inventory_reports_received_totals(client):
    row = next(r for r in client.get("/inventory").json() if r["ingredient"] == "beef_patty")
    assert row["total_received"] == 6
    assert row["last_unit_price_cents"] == 150
    order(client, (1, 1))
    client.post("/shipments", json={"ingredient": "beef_patty", "quantity": 4, "unit_price_cents": 175})
    row = next(r for r in client.get("/inventory").json() if r["ingredient"] == "beef_patty")
    assert row["quantity_on_hand"] == 9      # 6 - 1 + 4
    assert row["total_received"] == 10       # on hand can never exceed this
    assert row["last_unit_price_cents"] == 175


def test_recent_orders_feed(client):
    order(client, (1, 2))
    order(client, (6, 1), (8, 3))
    feed = client.get("/orders").json()
    assert [o["id"] for o in feed] == [2, 1]          # newest first
    assert feed[1]["items"] == [{"name": "Classic Burger", "quantity": 2}]
    assert "qr_token" not in feed[0]                  # not exposed in the feed


# ---------- Pipeline 2: drivers, QR pickup, drop-off ----------

def place(client):
    """Place a small order (fries) and return the JSON response."""
    return order(client, (6, 1)).json()


def scan(client, driver_id, token):
    return client.post("/pickup-scan", json={"driver_id": driver_id, "qr_token": token})


def dropoff(client, order_id, driver_id):
    return client.post(f"/orders/{order_id}/dropoff", json={"driver_id": driver_id})


def stats(client, driver_id):
    return client.get(f"/drivers/{driver_id}/stats").json()


def test_orders_are_spread_across_drivers(client):
    drivers = [place(client)["driver_id"] for _ in range(4)]
    assert drivers == ["d1", "d2", "d3", "d1"]
    assert place(client)["driver_name"] == "Sam"


def test_pickup_scan_success(client):
    o = place(client)
    r = scan(client, o["driver_id"], o["qr_token"])
    assert r.status_code == 200
    assert client.get(f"/orders/{o['order_id']}").json()["status"] == "picked_up"


def test_wrong_bag_is_rejected_and_changes_nothing(client):
    a, b = place(client), place(client)           # different drivers
    r = scan(client, a["driver_id"], b["qr_token"])   # A scans B's bag
    assert r.status_code == 403
    assert client.get(f"/orders/{b['order_id']}").json()["status"] == "confirmed"


def test_unknown_token_and_double_scan(client):
    o = place(client)
    assert scan(client, o["driver_id"], "not-a-real-token").status_code == 404
    assert scan(client, o["driver_id"], o["qr_token"]).status_code == 200
    assert scan(client, o["driver_id"], o["qr_token"]).status_code == 409


def test_dropoff_requires_pickup_and_right_driver(client):
    o = place(client)
    assert dropoff(client, o["order_id"], o["driver_id"]).status_code == 409  # not picked up
    scan(client, o["driver_id"], o["qr_token"])
    other = "d2" if o["driver_id"] != "d2" else "d3"
    assert dropoff(client, o["order_id"], other).status_code == 403
    assert dropoff(client, o["order_id"], o["driver_id"]).status_code == 200
    assert dropoff(client, o["order_id"], o["driver_id"]).status_code == 409  # already delivered


def test_only_fully_verified_deliveries_count_for_metrics(client):
    o = place(client)
    d = o["driver_id"]
    assert stats(client, d)["deliveries_completed"] == 0
    scan(client, d, o["qr_token"])                         # picked up only
    s = stats(client, d)
    assert s["deliveries_completed"] == 0 and s["in_progress"] == 1 and s["payout_cents"] == 0
    dropoff(client, o["order_id"], d)                      # both steps done
    s = stats(client, d)
    assert s["deliveries_completed"] == 1 and s["payout_cents"] == 400 and s["in_progress"] == 0


def test_driver_orders_hide_the_qr_token(client):
    o = place(client)
    mine = client.get(f"/drivers/{o['driver_id']}/orders").json()
    assert [x["id"] for x in mine] == [o["order_id"]]
    assert "qr_token" not in mine[0]
    assert client.get("/drivers/nobody/orders").status_code == 404