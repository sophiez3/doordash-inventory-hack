"""
database.py - everything that touches the database lives here.

Tables (matching your architecture):
  A) supplier_intake      - log of every shipment received
  B) inventory            - live ingredient levels
  C) menu_items + recipe_ingredients - the menu and each dish's "bill of materials"
Plus: orders and order_items.
"""
import os
import sqlite3
import uuid
from contextlib import contextmanager


def db_path() -> str:
    # Read at call time so tests can point this at a temporary file.
    return os.environ.get("DB_PATH", "restaurant.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS supplier_intake (          -- Database A
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ingredient       TEXT    NOT NULL,
    quantity         INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    received_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inventory (                -- Database B
    ingredient        TEXT PRIMARY KEY,
    unit              TEXT    NOT NULL,
    quantity_on_hand  INTEGER NOT NULL CHECK (quantity_on_hand >= 0),
    reserve_threshold INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS menu_items (               -- Database C
    id           INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL,
    price_cents  INTEGER NOT NULL,
    is_available INTEGER NOT NULL DEFAULT 1           -- 1 = true, 0 = false (SQLite has no boolean)
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (       -- Database C ("bill of materials")
    menu_item_id    INTEGER NOT NULL REFERENCES menu_items(id),
    ingredient      TEXT    NOT NULL REFERENCES inventory(ingredient),
    quantity_needed INTEGER NOT NULL,
    PRIMARY KEY (menu_item_id, ingredient)
);

CREATE TABLE IF NOT EXISTS drivers (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    status       TEXT NOT NULL DEFAULT 'confirmed',   -- confirmed -> picked_up -> delivered
    qr_token     TEXT NOT NULL UNIQUE,                -- random, NOT the order id
    driver_id    TEXT,
    total_cents  INTEGER NOT NULL,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    picked_up_at TEXT,
    delivered_at TEXT
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id      INTEGER NOT NULL REFERENCES orders(id),
    menu_item_id  INTEGER NOT NULL REFERENCES menu_items(id),
    quantity      INTEGER NOT NULL,
    notes         TEXT,      -- the customer's words, exactly as typed
    modifications TEXT       -- JSON: what we understood and applied
);
"""

# (ingredient, unit, starting quantity, unit cost in cents, reserve threshold)
# Quantities are deliberately small so you can sell items out during a demo.
SEED_INGREDIENTS = [
    ("beef_patty",     "patty",   6, 150, 0),
    ("burger_bun",     "bun",    10,  40, 0),
    ("cheese_slice",   "slice",  12,  25, 0),
    ("lettuce",        "leaf",   10,  10, 0),
    ("tomato",         "slice",  10,  15, 0),
    ("onion",          "ring",    8,  10, 0),
    ("chicken_breast", "piece",   5, 180, 0),
    ("tortilla",       "wrap",    8,  30, 0),
    ("potato",         "portion", 12,  35, 0),
    ("cola",           "can",    10,  50, 0),
]

# (id, name, price in cents, {ingredient: quantity needed})
SEED_MENU = [
    (1, "Classic Burger",      950, {"beef_patty": 1, "burger_bun": 1, "lettuce": 1, "tomato": 1, "onion": 1}),
    (2, "Cheeseburger",       1050, {"beef_patty": 1, "burger_bun": 1, "cheese_slice": 1, "onion": 1}),
    (3, "Double Cheeseburger", 1300, {"beef_patty": 2, "burger_bun": 1, "cheese_slice": 2}),
    (4, "Chicken Sandwich",   1000, {"chicken_breast": 1, "burger_bun": 1, "lettuce": 1}),
    (5, "Chicken Wrap",        900, {"chicken_breast": 1, "tortilla": 1, "lettuce": 1, "tomato": 1}),
    (6, "Fries",               400, {"potato": 1}),
    (7, "Garden Salad",        800, {"lettuce": 2, "tomato": 1, "onion": 1}),
    (8, "Cola",                250, {"cola": 1}),
]


# (id, name) - a few demo drivers so you can show "wrong bag" scenarios
SEED_DRIVERS = [("d1", "Alex"), ("d2", "Sam"), ("d3", "Priya")]


# ---------- connections ----------

def _connect() -> sqlite3.Connection:
    # isolation_level=None means WE control transactions explicitly.
    conn = sqlite3.connect(db_path(), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["name"]
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn():
    """For read-only queries."""
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction():
    """
    For anything that changes data. Everything inside the `with` block either
    fully succeeds (COMMIT) or is fully undone (ROLLBACK) if an error is raised.

    BEGIN IMMEDIATE locks the database for writing right away, so two orders
    can never run their "check stock, then deduct" steps at the same time.
    That is what prevents the race condition on the last burger.
    """
    conn = _connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ---------- business rules ----------

def refresh_availability(conn: sqlite3.Connection) -> None:
    """
    The auto "86-ing" rule: a dish is available only if, for EVERY ingredient
    in its recipe, (on hand - reserve) >= the amount the dish needs.
    Call this after anything that changes inventory.
    """
    conn.execute(
        """
        UPDATE menu_items SET is_available = CASE
            WHEN EXISTS (
                SELECT 1
                FROM recipe_ingredients r
                JOIN inventory i ON i.ingredient = r.ingredient
                WHERE r.menu_item_id = menu_items.id
                  AND i.quantity_on_hand - i.reserve_threshold < r.quantity_needed
            ) THEN 0 ELSE 1 END
        """
    )


def receive_shipment(conn: sqlite3.Connection, ingredient: str,
                     quantity: int, unit_price_cents: int) -> None:
    """Pipeline 1, step 1: log the delivery (A) and add it to live stock (B)."""
    exists = conn.execute(
        "SELECT 1 FROM inventory WHERE ingredient = ?", (ingredient,)
    ).fetchone()
    if exists is None:
        raise ValueError(f"Unknown ingredient: {ingredient}")
    conn.execute(
        "INSERT INTO supplier_intake (ingredient, quantity, unit_price_cents) VALUES (?, ?, ?)",
        (ingredient, quantity, unit_price_cents),
    )
    conn.execute(
        "UPDATE inventory SET quantity_on_hand = quantity_on_hand + ? WHERE ingredient = ?",
        (quantity, ingredient),
    )
    refresh_availability(conn)


def new_qr_token() -> str:
    return str(uuid.uuid4())


# ---------- setup ----------

def _seed(conn: sqlite3.Connection) -> None:
    for ingredient, unit, qty, cost, reserve in SEED_INGREDIENTS:
        # Start at 0, then "receive" the starting stock as a shipment, so
        # Database A genuinely initializes Database B.
        conn.execute(
            "INSERT INTO inventory (ingredient, unit, quantity_on_hand, reserve_threshold) "
            "VALUES (?, ?, 0, ?)",
            (ingredient, unit, reserve),
        )
        receive_shipment(conn, ingredient, qty, cost)
    for item_id, name, price, recipe in SEED_MENU:
        conn.execute(
            "INSERT INTO menu_items (id, name, price_cents) VALUES (?, ?, ?)",
            (item_id, name, price),
        )
        for ingredient, needed in recipe.items():
            conn.execute(
                "INSERT INTO recipe_ingredients VALUES (?, ?, ?)",
                (item_id, ingredient, needed),
            )
    refresh_availability(conn)


def init_db() -> None:
    """Create tables and seed them if the database is empty."""
    with transaction() as conn:
        # Run the schema one statement at a time (executescript would end our transaction).
        for statement in SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        # Upgrade older database files: add columns that didn't exist yet.
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(order_items)")}
        for column in ("notes", "modifications"):
            if column not in existing:
                conn.execute(f"ALTER TABLE order_items ADD COLUMN {column} TEXT")
        if conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0] == 0:
            _seed(conn)
        if conn.execute("SELECT COUNT(*) FROM drivers").fetchone()[0] == 0:
            conn.executemany("INSERT INTO drivers (id, name) VALUES (?, ?)", SEED_DRIVERS)


def reset_db() -> None:
    """Wipe everything and re-seed. Handy before each demo run."""
    with transaction() as conn:
        for table in ("order_items", "orders", "recipe_ingredients",
                      "menu_items", "inventory", "supplier_intake", "drivers"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    init_db()