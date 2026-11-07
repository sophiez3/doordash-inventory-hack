"""
main.py - the API. Run with:  fastapi dev main.py
Then open http://127.0.0.1:8000/docs to try every endpoint with buttons.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import database as db
import insights
import llm
import manifests
import modifiers
import json


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # runs once when the server starts
    yield


app = FastAPI(title="Smart Kitchen API", lifespan=lifespan)

# Lets your React app (running on a different port) call this API.
# Without this, the browser blocks the requests (this is called CORS).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- request shapes (Pydantic validates these automatically) ----------

class OrderLine(BaseModel):
    menu_item_id: int
    quantity: int = Field(ge=1, le=20)
    notes: str | None = Field(default=None, max_length=200)  # "double cheese, hold the onions"


class OrderIn(BaseModel):
    items: list[OrderLine] = Field(min_length=1, max_length=10)


class ShipmentIn(BaseModel):
    ingredient: str
    quantity: int = Field(gt=0, le=100_000)
    unit_price_cents: int = Field(ge=0, le=1_000_000)


class BatchShipmentIn(BaseModel):
    items: list[ShipmentIn] = Field(min_length=1, max_length=50)


class ManifestIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class PickupScanIn(BaseModel):
    driver_id: str
    qr_token: str = Field(min_length=1, max_length=100)


class DropoffIn(BaseModel):
    driver_id: str


# What a driver earns per verified delivery (flat rate for the demo).
PAYOUT_PER_DELIVERY_CENTS = 400


# ---------- read endpoints ----------

@app.get("/menu")
def get_menu():
    """The storefront polls this. is_available=false means 'Sold Out'."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, price_cents, is_available FROM menu_items ORDER BY id"
        ).fetchall()
    return [{**dict(r), "is_available": bool(r["is_available"])} for r in rows]


@app.get("/inventory")
def get_inventory():
    """
    The kitchen dashboard polls this. Besides live stock (Database B), it also
    reports how much has EVER been received (summed from Database A) so the
    dashboard can draw "how full" bars, and the last price paid per unit.
    """
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT i.ingredient, i.unit, i.quantity_on_hand, i.reserve_threshold,
                   COALESCE((SELECT SUM(s.quantity) FROM supplier_intake s
                             WHERE s.ingredient = i.ingredient), 0) AS total_received,
                   (SELECT s.unit_price_cents FROM supplier_intake s
                    WHERE s.ingredient = i.ingredient
                    ORDER BY s.id DESC LIMIT 1) AS last_unit_price_cents
            FROM inventory i
            ORDER BY i.ingredient
            """
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/orders")
def list_recent_orders(limit: int = 10):
    """Newest orders first, with dish names. Feeds the dashboard's live order feed."""
    limit = max(1, min(limit, 50))
    with db.get_conn() as conn:
        orders = conn.execute(
            """
            SELECT o.id, o.status, o.total_cents, o.created_at, d.name AS driver_name
            FROM orders o LEFT JOIN drivers d ON d.id = o.driver_id
            ORDER BY o.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for o in orders:
            items = conn.execute(
                """
                SELECT m.name, oi.quantity, oi.notes, oi.modifications
                FROM order_items oi JOIN menu_items m ON m.id = oi.menu_item_id
                WHERE oi.order_id = ?
                """,
                (o["id"],),
            ).fetchall()
            result.append({**dict(o), "items": [_feed_item(i) for i in items]})
    return result


def _feed_item(row) -> dict:
    try:
        applied = json.loads(row["modifications"])["applied"] if row["modifications"] else []
    except (ValueError, KeyError, TypeError):
        applied = []
    return {
        "name": row["name"],
        "quantity": row["quantity"],
        "notes": row["notes"],
        "modifications": applied,
    }


@app.get("/ai/status")
def ai_status():
    """Lets the dashboard show whether the AI is connected."""
    return {"configured": llm.is_configured(), "model": llm.model_name()}


@app.get("/orders/{order_id}")
def get_order(order_id: int):
    with db.get_conn() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            raise HTTPException(404, "Order not found")
        items = conn.execute(
            "SELECT menu_item_id, quantity FROM order_items WHERE order_id = ?", (order_id,)
        ).fetchall()
    return {**dict(order), "items": [dict(i) for i in items]}


# ---------- write endpoints ----------

@app.post("/orders", status_code=201)
def create_order(order: OrderIn):
    """
    Place an order. The database work happens inside ONE transaction:
    if anything fails (e.g. not enough stock), nothing is changed.
    """
    # Step 0: understand any special instructions BEFORE the transaction starts.
    # (An AI call can take seconds, and a transaction locks the database for writes,
    # so we never make the kitchen wait while we wait for the AI.)
    interpretations = {}
    for index, line in enumerate(order.items):
        if line.notes and line.notes.strip():
            interpretations[index] = modifiers.interpret_notes(line.menu_item_id, line.notes)

    with db.transaction() as conn:
        # 1. Look up each dish and add up the ingredients the whole order needs.
        needed: dict[str, int] = {}
        total_cents = 0
        for index, line in enumerate(order.items):
            item = conn.execute(
                "SELECT id, name, price_cents, is_available FROM menu_items WHERE id = ?",
                (line.menu_item_id,),
            ).fetchone()
            if item is None:
                raise HTTPException(404, f"Menu item {line.menu_item_id} does not exist")
            if not item["is_available"]:
                raise HTTPException(409, f"{item['name']} is sold out")
            total_cents += item["price_cents"] * line.quantity
            per_serving = {
                r["ingredient"]: r["quantity_needed"]
                for r in conn.execute(
                    "SELECT ingredient, quantity_needed FROM recipe_ingredients WHERE menu_item_id = ?",
                    (line.menu_item_id,),
                )
            }
            # Apply the (already validated) changes: "double cheese, hold the onions".
            for change in interpretations.get(index, {}).get("applied", []):
                ing = change["ingredient"]
                per_serving[ing] = max(0, per_serving.get(ing, 0) + change["delta"])
            for ingredient, amount in per_serving.items():
                needed[ingredient] = needed.get(ingredient, 0) + amount * line.quantity

        # 2. Check there is enough of every ingredient (respecting the reserve).
        short = []
        for ingredient, amount in needed.items():
            stock = conn.execute(
                "SELECT quantity_on_hand, reserve_threshold FROM inventory WHERE ingredient = ?",
                (ingredient,),
            ).fetchone()
            if stock["quantity_on_hand"] - stock["reserve_threshold"] < amount:
                short.append(ingredient)
        if short:
            raise HTTPException(
                409, f"Not enough stock for this order (short on: {', '.join(short)})"
            )

        # 3. Deduct the ingredients.
        for ingredient, amount in needed.items():
            conn.execute(
                "UPDATE inventory SET quantity_on_hand = quantity_on_hand - ? WHERE ingredient = ?",
                (amount, ingredient),
            )

        # 4. Assign the driver with the fewest active deliveries, then save the order.
        driver = conn.execute(
            """
            SELECT d.id, d.name
            FROM drivers d
            LEFT JOIN orders o ON o.driver_id = d.id AND o.status IN ('confirmed', 'picked_up')
            GROUP BY d.id
            ORDER BY COUNT(o.id), d.id
            LIMIT 1
            """
        ).fetchone()
        driver_id = driver["id"] if driver else None
        driver_name = driver["name"] if driver else None

        qr_token = db.new_qr_token()
        cursor = conn.execute(
            "INSERT INTO orders (qr_token, total_cents, driver_id) VALUES (?, ?, ?)",
            (qr_token, total_cents, driver_id),
        )
        order_id = cursor.lastrowid
        special_instructions = []
        for index, line in enumerate(order.items):
            result = interpretations.get(index)
            conn.execute(
                "INSERT INTO order_items (order_id, menu_item_id, quantity, notes, modifications) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    order_id,
                    line.menu_item_id,
                    line.quantity,
                    line.notes.strip() if line.notes and line.notes.strip() else None,
                    json.dumps(result) if result else None,
                ),
            )
            if result:
                name = conn.execute(
                    "SELECT name FROM menu_items WHERE id = ?", (line.menu_item_id,)
                ).fetchone()["name"]
                special_instructions.append({
                    "menu_item_id": line.menu_item_id,
                    "name": name,
                    "notes": line.notes.strip(),
                    "applied": result["applied"],
                    "unhandled": result["unhandled"],
                    "source": result["source"],
                })

        # 5. Re-check which dishes are still available (the automatic 86-ing).
        db.refresh_availability(conn)

    return {
        "order_id": order_id,
        "status": "confirmed",
        "total_cents": total_cents,
        "qr_token": qr_token,  # shown as a QR code on the receipt / bag label
        "driver_id": driver_id,
        "driver_name": driver_name,
        "special_instructions": special_instructions,
    }


@app.post("/shipments", status_code=201)
def add_shipment(shipment: ShipmentIn):
    """Restock an ingredient. Sold-out dishes come back automatically."""
    try:
        with db.transaction() as conn:
            db.receive_shipment(
                conn, shipment.ingredient, shipment.quantity, shipment.unit_price_cents
            )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


# ---------- Pipeline 2: driver handoff & delivery verification ----------

def _require_driver(conn, driver_id: str):
    driver = conn.execute("SELECT id, name FROM drivers WHERE id = ?", (driver_id,)).fetchone()
    if driver is None:
        raise HTTPException(404, "Unknown driver")
    return driver


@app.get("/drivers")
def list_drivers():
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT id, name FROM drivers ORDER BY id").fetchall()]


@app.get("/drivers/{driver_id}/orders")
def driver_orders(driver_id: str):
    """
    The driver's active deliveries. Notice it does NOT include qr_token:
    the driver must physically scan the bag, they can't just read the code.
    """
    with db.get_conn() as conn:
        _require_driver(conn, driver_id)
        orders = conn.execute(
            """
            SELECT id, status, total_cents, created_at FROM orders
            WHERE driver_id = ? AND status IN ('confirmed', 'picked_up')
            ORDER BY id
            """,
            (driver_id,),
        ).fetchall()
        result = []
        for o in orders:
            items = conn.execute(
                """
                SELECT m.name, oi.quantity
                FROM order_items oi JOIN menu_items m ON m.id = oi.menu_item_id
                WHERE oi.order_id = ?
                """,
                (o["id"],),
            ).fetchall()
            result.append({**dict(o), "items": [dict(i) for i in items]})
    return result


@app.get("/drivers/{driver_id}/stats")
def driver_stats(driver_id: str):
    """Only orders that finished BOTH steps have status 'delivered', so only they count."""
    with db.get_conn() as conn:
        driver = _require_driver(conn, driver_id)
        done = conn.execute(
            """
            SELECT COUNT(*) AS completed,
                   COALESCE(AVG(CAST(strftime('%s', delivered_at) AS INTEGER)
                              - CAST(strftime('%s', picked_up_at) AS INTEGER)), 0) AS avg_seconds
            FROM orders
            WHERE driver_id = ? AND status = 'delivered'
              AND picked_up_at IS NOT NULL AND delivered_at IS NOT NULL
            """,
            (driver_id,),
        ).fetchone()
        in_progress = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE driver_id = ? AND status IN ('confirmed', 'picked_up')",
            (driver_id,),
        ).fetchone()[0]
    return {
        "driver_id": driver["id"],
        "driver_name": driver["name"],
        "deliveries_completed": done["completed"],
        "payout_cents": done["completed"] * PAYOUT_PER_DELIVERY_CENTS,
        "avg_delivery_seconds": round(done["avg_seconds"]),
        "in_progress": in_progress,
    }


@app.post("/pickup-scan")
def pickup_scan(scan: PickupScanIn):
    """
    Step 1: the driver scans the QR on the bag before leaving the store.
    The scanner only knows the token, so the order is found by token.
    """
    token = scan.qr_token.strip()
    with db.transaction() as conn:
        _require_driver(conn, scan.driver_id)
        order = conn.execute(
            "SELECT id, status, driver_id FROM orders WHERE qr_token = ?", (token,)
        ).fetchone()
        if order is None:
            raise HTTPException(404, "Unknown QR code. This bag is not in our system.")
        if order["driver_id"] != scan.driver_id:
            raise HTTPException(403, "Wrong bag! This order is assigned to a different driver.")
        if order["status"] == "picked_up":
            raise HTTPException(409, f"Order #{order['id']} was already picked up.")
        if order["status"] == "delivered":
            raise HTTPException(409, f"Order #{order['id']} was already delivered.")
        conn.execute(
            "UPDATE orders SET status = 'picked_up', picked_up_at = CURRENT_TIMESTAMP WHERE id = ?",
            (order["id"],),
        )
    return {"order_id": order["id"], "status": "picked_up"}


@app.post("/orders/{order_id}/dropoff")
def dropoff(order_id: int, body: DropoffIn):
    """Step 2: the driver confirms the hand-off. Only possible after step 1."""
    with db.transaction() as conn:
        _require_driver(conn, body.driver_id)
        order = conn.execute(
            "SELECT id, status, driver_id FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if order is None:
            raise HTTPException(404, "Order not found")
        if order["driver_id"] != body.driver_id:
            raise HTTPException(403, "This order is assigned to a different driver.")
        if order["status"] == "confirmed":
            raise HTTPException(409, "Scan the bag at pickup before confirming drop-off.")
        if order["status"] == "delivered":
            raise HTTPException(409, "This order was already delivered.")
        conn.execute(
            "UPDATE orders SET status = 'delivered', delivered_at = CURRENT_TIMESTAMP WHERE id = ?",
            (order_id,),
        )
    return {"order_id": order_id, "status": "delivered"}


@app.post("/shipments/batch", status_code=201)
def add_shipments_batch(batch: BatchShipmentIn):
    """Save several shipment lines at once. All of them succeed, or none do."""
    try:
        with db.transaction() as conn:
            for s in batch.items:
                db.receive_shipment(conn, s.ingredient, s.quantity, s.unit_price_cents)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "lines": len(batch.items)}


@app.post("/manifests/parse")
def parse_manifest(body: ManifestIn):
    """
    AI invoice parser. Returns a DRAFT for a human to review; it saves nothing.
    The reviewed rows are then saved through POST /shipments/batch.
    """
    try:
        return manifests.parse_manifest(body.text)
    except llm.LLMError as e:
        raise HTTPException(
            503, f"AI parsing is unavailable right now ({e}). You can enter the shipment manually."
        )


# ---------- Step 7: owner insights ----------

@app.get("/insights")
def get_insights():
    """All numbers, computed by our own code (no AI). Cheap enough to poll."""
    return insights.compute()


@app.post("/insights/summary")
def insights_summary():
    """An AI-written briefing built from those numbers (falls back to plain text)."""
    return insights.owner_summary()


@app.post("/dev/reset")
def reset_demo():
    """Wipe and re-seed everything. Use before each demo run."""
    db.reset_db()
    return {"ok": True}