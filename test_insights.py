"""Tests for Step 7 (owner insights). The AI is always simulated."""
import pytest
from fastapi.testclient import TestClient

import llm


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import main

    def ai_is_down(*args, **kwargs):
        raise llm.LLMError("tests never call the real AI")

    monkeypatch.setattr(llm, "call_structured", ai_is_down)
    with TestClient(main.app) as c:
        yield c


def order(client, *lines):
    r = client.post("/orders", json={"items": [{"menu_item_id": i, "quantity": q} for i, q in lines]})
    assert r.status_code == 201
    return r


def dish(data, name):
    return next(d for d in data["dishes"] if d["name"] == name)


def test_empty_restaurant(client):
    data = client.get("/insights").json()
    assert data["totals"]["orders"] == 0
    assert data["totals"]["gross_margin_pct"] is None
    assert data["reorder"] == [] and data["sold_out"] == []
    classic = dish(data, "Classic Burger")
    # patty 150 + bun 40 + lettuce 10 + tomato 15 + onion 10
    assert classic["cost_cents"] == 225
    assert classic["margin_cents"] == 950 - 225
    assert classic["margin_pct"] == 76.3


def test_sales_and_profit_numbers(client):
    order(client, (1, 2))   # 2 classic burgers = $19.00
    t = client.get("/insights").json()["totals"]
    assert t["orders"] == 1 and t["units_sold"] == 2
    assert t["revenue_cents"] == 1900
    assert t["cogs_cents"] == 450            # 2 x 225
    assert t["gross_profit_cents"] == 1450
    assert t["gross_margin_pct"] == 76.3
    assert t["avg_order_cents"] == 1900


def test_cost_uses_weighted_average_of_supplier_prices(client):
    # 6 patties @ $1.50 plus 4 more @ $2.00 -> average 170 cents.
    client.post("/shipments", json={"ingredient": "beef_patty", "quantity": 4, "unit_price_cents": 200})
    classic = dish(client.get("/insights").json(), "Classic Burger")
    assert classic["cost_cents"] == 170 + 40 + 10 + 15 + 10


def test_reorder_suggestions_and_blocked_dishes(client):
    order(client, (3, 3))    # uses all 6 patties
    data = client.get("/insights").json()
    assert [r["ingredient"] for r in data["reorder"]] == ["beef_patty"]   # buns/cheese still fine
    r = data["reorder"][0]
    assert r["status"] == "out" and r["suggested_qty"] == 6 and r["estimated_cost_cents"] == 900
    blocked = {s["name"]: s["blocked_by"] for s in data["sold_out"]}
    assert blocked == {
        "Classic Burger": ["beef_patty"],
        "Cheeseburger": ["beef_patty"],
        "Double Cheeseburger": ["beef_patty"],
    }
    assert data["dishes"][0]["name"] == "Double Cheeseburger"   # best seller first


def test_restocking_clears_the_alerts(client):
    order(client, (3, 3))
    client.post("/shipments", json={"ingredient": "beef_patty", "quantity": 10, "unit_price_cents": 150})
    data = client.get("/insights").json()
    assert data["reorder"] == [] and data["sold_out"] == []


# ---------- the briefing ----------

def test_briefing_falls_back_when_ai_is_down(client):
    order(client, (3, 3))
    r = client.post("/insights/summary").json()
    assert r["source"] == "fallback"
    assert "1 order" in r["summary"] and "$39.00" in r["summary"]
    assert any("beef patty" in a for a in r["actions"])


def test_briefing_with_no_orders(client):
    r = client.post("/insights/summary").json()
    assert r["source"] == "fallback" and "No orders yet" in r["summary"]


def test_ai_briefing_gets_formatted_facts_and_is_trimmed(client, monkeypatch):
    seen = {}

    def fake(system, user, name, schema):
        seen["user"] = user
        return {"summary": "  Business is good.  " + "x" * 2000,
                "actions": ["Reorder patties", "", 5, "b" * 500, "c", "d"]}

    monkeypatch.setattr(llm, "call_structured", fake)
    order(client, (3, 3))
    r = client.post("/insights/summary").json()
    assert r["source"] == "ai"
    assert len(r["summary"]) <= 700
    assert len(r["actions"]) == 3 and all(len(a) <= 200 for a in r["actions"])
    assert '"revenue": "$39.00"' in seen["user"]     # AI receives finished, formatted numbers


@pytest.mark.parametrize("bad", [
    ["not", "a", "dict"],
    {"summary": "", "actions": []},
    {"nope": 1},
])
def test_bad_ai_briefing_falls_back(client, monkeypatch, bad):
    monkeypatch.setattr(llm, "call_structured", lambda *a, **k: bad)
    assert client.post("/insights/summary").json()["source"] == "fallback"