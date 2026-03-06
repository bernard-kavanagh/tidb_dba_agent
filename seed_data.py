"""
Seed Data Generator
--------------------
Populates the dba_agent_db schema with realistic, high-volume data
that mimics a live e-commerce backend.

Volumes (configurable via CLI args):
  --users        10,000   (default)
  --products       500
  --orders      50,000   — the main slow-query target
  --events     200,000   — high-volume clickstream
  --memories        10   — pre-seeded DBA episodic memories

Usage:
  python3 seed_data.py
  python3 seed_data.py --users 1000 --orders 5000 --events 20000
  python3 seed_data.py --memories-only   (just seed agent memory)

Requirements: pip install faker tqdm
"""

import os
import sys
import json
import random
import argparse
import math
import datetime
from decimal import Decimal
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import Error
from faker import Faker

load_dotenv()

fake = Faker("en_IE")       # Irish locale for realistic EU data
Faker.seed(42)
random.seed(42)

# ── Connection ────────────────────────────────────────────────────────────────

def get_conn():
    return mysql.connector.connect(
        host=os.getenv("TIDB_HOST"),
        port=int(os.getenv("TIDB_PORT", 4000)),
        user=os.getenv("TIDB_USER"),
        password=os.getenv("TIDB_PASSWORD"),
        database=os.getenv("TIDB_DATABASE", "dba_agent_db"),
        ssl_ca=os.getenv("TIDB_SSL_CA"),
        autocommit=False,
    )

# ── Reference data ────────────────────────────────────────────────────────────

COUNTRIES   = ["IE", "GB", "DE", "FR", "NL", "ES", "IT", "PL", "SE", "DK"]
REGIONS     = ["Leinster", "Munster", "Connacht", "Ulster", "London",
               "Bavaria", "Île-de-France", "Catalonia", "Lombardy"]
TIERS       = ["free", "free", "free", "pro", "pro", "enterprise"]   # weighted

CATEGORIES  = {
    "Electronics":   ["Laptops", "Phones", "Tablets", "Accessories", "Cables"],
    "Clothing":      ["Mens", "Womens", "Kids", "Sports", "Footwear"],
    "Home & Garden": ["Furniture", "Kitchen", "Bedding", "Garden Tools", "Lighting"],
    "Books":         ["Fiction", "Non-Fiction", "Tech", "Business", "Travel"],
    "Sports":        ["Running", "Cycling", "Yoga", "Team Sports", "Swimming"],
    "Beauty":        ["Skincare", "Haircare", "Makeup", "Fragrance", "Tools"],
}
BRANDS = ["Nova", "Apex", "Orbis", "Stellar", "Crest", "Verdant", "Luma",
          "Helix", "Zenith", "Arc", "Bolt", "Dune", "Forge", "Kite", "Nimbus"]

ORDER_STATUSES = [
    ("pending",    0.05),
    ("processing", 0.10),
    ("shipped",    0.15),
    ("delivered",  0.55),
    ("cancelled",  0.10),
    ("refunded",   0.05),
]
PAYMENT_METHODS = ["credit_card", "debit_card", "paypal", "stripe",
                   "apple_pay", "google_pay", "bank_transfer"]

EVENT_TYPES = [
    ("page_view",          0.35),
    ("product_view",       0.25),
    ("search",             0.15),
    ("add_to_cart",        0.10),
    ("checkout_start",     0.06),
    ("checkout_complete",  0.04),
    ("login",              0.03),
    ("logout",             0.02),
]

PAGES = ["/", "/products", "/products/{id}", "/cart", "/checkout",
         "/account", "/orders", "/search", "/about", "/blog"]

# ── Realistic distribution helpers ───────────────────────────────────────────
# Power user model: top 15% of users generate 60% of all orders & events
POWER_USER_FRACTION    = 0.15
POWER_USER_ORDER_SHARE = 0.60

# Business hours weight per hour (0–23): peaks 9am–6pm
_HOUR_WEIGHTS = [
    0.2, 0.1, 0.1, 0.1, 0.1, 0.2,   # 00-05  night
    0.5, 1.5, 3.0, 4.0, 4.5, 4.5,   # 06-11  morning ramp
    4.0, 3.5, 3.5, 3.5, 3.0, 2.5,   # 12-17  afternoon
    2.0, 1.5, 1.2, 0.8, 0.5, 0.3,   # 18-23  evening wind-down
]


def growth_weighted_date(days_back: int = 730) -> datetime.datetime:
    """Return a datetime skewed towards recent dates — models 2yr business growth.
    Uses sqrt() to bias the distribution: recent months get ~3x more events."""
    t = math.sqrt(random.random())   # sqrt skews 0-1 towards 1.0 (recent)
    days_ago = int((1.0 - t) * days_back)
    return datetime.datetime.now() - datetime.timedelta(
        days=days_ago,
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )


def business_hour_dt(base: datetime.datetime) -> datetime.datetime:
    """Replace the hour of a datetime with a business-hours-weighted hour."""
    hour = random.choices(range(24), weights=_HOUR_WEIGHTS)[0]
    return base.replace(hour=hour, minute=random.randint(0, 59),
                        second=random.randint(0, 59))


def pick_user(n_users: int, power_ids: list) -> int:
    """60% of activity goes to power users (top 15% of the user base)."""
    if power_ids and random.random() < POWER_USER_ORDER_SHARE:
        return random.choice(power_ids)
    return random.randint(1, n_users)


# ── Helpers ───────────────────────────────────────────────────────────────────

def weighted_choice(choices):
    """Pick a value from [(value, weight), ...] list."""
    values, weights = zip(*choices)
    return random.choices(values, weights=weights, k=1)[0]


def batch_insert(cursor, table, columns, rows, batch_size=500):
    """Insert rows in batches. Returns number of rows inserted."""
    if not rows:
        return 0
    placeholders = "(" + ", ".join(["%s"] * len(columns)) + ")"
    col_str      = ", ".join(columns)
    sql          = f"INSERT IGNORE INTO {table} ({col_str}) VALUES {placeholders}"
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i+batch_size]
        cursor.executemany(sql, batch)
        total += len(batch)
    return total


def progress(label, current, total):
    pct = int(current / total * 40)
    bar = "█" * pct + "░" * (40 - pct)
    print(f"\r  {label:30s} [{bar}] {current:,}/{total:,}", end="", flush=True)
    if current == total:
        print()

# ── Generators ────────────────────────────────────────────────────────────────

def seed_users(cursor, n: int):
    print(f"\n👤 Seeding {n:,} users...")
    rows = []
    for i in range(n):
        country = random.choice(COUNTRIES)
        rows.append((
            fake.unique.email(),
            fake.name(),
            fake.phone_number()[:20],
            country,
            random.choice(REGIONS),
            random.choice(TIERS),
            random.choice([True] * 9 + [False]),
            fake.date_time_between(start_date="-3y", end_date="now"),
            fake.date_time_between(start_date="-30d", end_date="now") if random.random() > 0.2 else None,
        ))
        if (i + 1) % 1000 == 0 or i + 1 == n:
            progress("users", i + 1, n)
    cols = ["email","full_name","phone","country","region","tier",
            "is_active","created_at","last_login_at"]
    return batch_insert(cursor, "users", cols, rows)


def seed_products(cursor, n: int):
    print(f"\n📦 Seeding {n:,} products...")
    rows = []
    skus_used = set()
    for i in range(n):
        cat = random.choice(list(CATEGORIES.keys()))
        sub = random.choice(CATEGORIES[cat])
        price = round(random.uniform(4.99, 1499.99), 2)
        sku = fake.unique.bothify("??-######").upper()
        rows.append((
            sku,
            f"{random.choice(BRANDS)} {fake.word().title()} {sub}",
            cat, sub,
            random.choice(BRANDS),
            price,
            round(price * random.uniform(0.3, 0.7), 2),
            random.randint(0, 500),
            random.choice([True] * 9 + [False]),
            fake.sentence(nb_words=12),
            fake.date_time_between(start_date="-2y", end_date="now"),
        ))
        if (i + 1) % 100 == 0 or i + 1 == n:
            progress("products", i + 1, n)
    cols = ["sku","name","category","sub_category","brand","price","cost",
            "stock_qty","is_active","description","created_at"]
    return batch_insert(cursor, "products", cols, rows)


def seed_orders(cursor, n_orders: int, n_users: int, n_products: int,
                power_user_ids: list = None):
    print(f"\n🛒 Seeding {n_orders:,} orders + line items...")
    order_rows = []
    item_rows  = []

    for i in range(n_orders):
        uid    = pick_user(n_users, power_user_ids or [])
        status = weighted_choice(ORDER_STATUSES)
        n_items = random.choices([1, 2, 3, 4, 5], weights=[50, 25, 12, 8, 5])[0]
        subtotal = Decimal(0)
        line_items = []
        for _ in range(n_items):
            pid       = random.randint(1, n_products)
            qty       = random.randint(1, 5)
            unit_price = Decimal(str(round(random.uniform(4.99, 1499.99), 2)))
            discount   = Decimal(str(round(float(unit_price) * random.choice([0, 0, 0, 0.05, 0.10, 0.15]), 2)))
            line_total = (unit_price - discount) * qty
            subtotal  += line_total
            line_items.append((pid, qty, unit_price, discount, line_total))

        discount   = Decimal(str(round(float(subtotal) * random.choice([0, 0, 0, 0.05, 0.10]), 2)))
        tax        = Decimal(str(round(float(subtotal - discount) * 0.23, 2)))
        total      = subtotal - discount + tax
        order_date = growth_weighted_date(days_back=730)
        shipped_at = None
        delivered_at = None
        if status in ("shipped", "delivered"):
            shipped_at = order_date + datetime.timedelta(days=random.randint(1, 5))
        if status == "delivered":
            delivered_at = shipped_at + datetime.timedelta(days=random.randint(1, 7))

        order_rows.append((
            uid, status, subtotal, discount, tax, total,
            random.choice(["EUR", "GBP", "USD"]),
            random.choice(PAYMENT_METHODS),
            fake.address()[:300],
            order_date, order_date, shipped_at, delivered_at,
        ))
        # We'll add items in a second pass once we have order IDs
        # Store line items indexed by position
        item_rows.append(line_items)

        if (i + 1) % 2000 == 0 or i + 1 == n_orders:
            progress("orders", i + 1, n_orders)

    # Bulk insert orders
    order_cols = ["user_id","status","subtotal","discount","tax","total_amount",
                  "currency","payment_method","shipping_addr",
                  "created_at","updated_at","shipped_at","delivered_at"]
    cursor.executemany(
        f"INSERT INTO orders ({','.join(order_cols)}) VALUES ({','.join(['%s']*len(order_cols))})",
        order_rows
    )
    # Get first inserted order ID
    cursor.execute("SELECT MIN(order_id) FROM orders")
    first_order_id = cursor.fetchone()[0] or 1

    # Build and insert order_items with real order IDs
    flat_items = []
    for idx, lines in enumerate(item_rows):
        oid = first_order_id + idx
        for pid, qty, unit_price, discount, line_total in lines:
            flat_items.append((oid, pid, qty, unit_price, discount, line_total))

    item_cols = ["order_id","product_id","quantity","unit_price","discount","line_total"]
    print(f"\n   ↳ Inserting {len(flat_items):,} order line items...")
    batch_insert(cursor, "order_items", item_cols, flat_items, batch_size=1000)
    return n_orders


def seed_reviews(cursor, n_orders: int, n_products: int, n_users: int):
    n_reviews = n_orders // 5   # ~20% of orders leave a review
    print(f"\n⭐ Seeding {n_reviews:,} reviews...")
    rows = []
    for i in range(n_reviews):
        rows.append((
            random.randint(1, n_products),
            random.randint(1, n_users),
            random.choices([1,2,3,4,5], weights=[3,5,12,35,45])[0],
            fake.sentence(nb_words=6),
            fake.paragraph(nb_sentences=3),
            random.random() > 0.4,
            fake.date_time_between(start_date="-2y", end_date="now"),
        ))
        if (i + 1) % 500 == 0 or i + 1 == n_reviews:
            progress("reviews", i + 1, n_reviews)
    cols = ["product_id","user_id","rating","title","body","is_verified","created_at"]
    return batch_insert(cursor, "reviews", cols, rows)


def _session_event_sequence(n: int) -> list:
    """Generate a realistic ordered sequence of event types for a single session.
    Sessions follow a browse → (maybe) purchase funnel."""
    funnel = ["page_view", "product_view", "search",
              "add_to_cart", "checkout_start", "checkout_complete"]
    # Start with page_view / product_view / search, then randomly continue funnel
    sequence = []
    stage = 0
    for _ in range(n):
        if stage < len(funnel) and random.random() < 0.4:
            sequence.append(funnel[stage])
            stage += 1
        else:
            sequence.append(weighted_choice(EVENT_TYPES))
    return sequence


def seed_events(cursor, n_events: int, n_users: int, power_user_ids: list = None):
    """Session-based clickstream seeding.
    Events are grouped into realistic sessions (5–15 events each) with:
      - consistent session_ids per session
      - timestamps spaced 10s–3min apart within a session
      - business-hours-weighted session start times
      - growth-weighted dates (recent months heavier)
      - power user concentration
    """
    print(f"\n📡 Seeding {n_events:,} events (clickstream)...")
    rows = []
    generated = 0

    while generated < n_events:
        uid        = pick_user(n_users, power_user_ids or [])
        session_id = fake.uuid4()
        n_in_sess  = min(
            random.choices([3, 5, 8, 12, 15, 20], weights=[20, 30, 25, 15, 7, 3])[0],
            n_events - generated,
        )
        # Session start: growth-weighted date + business-hours hour
        sess_start = business_hour_dt(growth_weighted_date(days_back=180))
        ip         = fake.ipv4()
        ua         = fake.user_agent()[:300]

        offset_s = 0
        for etype in _session_event_sequence(n_in_sess):
            event_time = sess_start + datetime.timedelta(seconds=offset_s)
            offset_s  += random.randint(10, 180)   # 10 s – 3 min between events
            rows.append((
                uid, session_id, etype,
                random.choice(PAGES),
                fake.url() if random.random() > 0.6 else None,
                ip, ua,
                json.dumps({"ab_test": random.choice(["A", "B", "C"]),
                            "v": random.randint(1, 3)}),
                event_time,
            ))
            generated += 1

        if generated % 5000 < n_in_sess or generated >= n_events:
            progress("events", min(generated, n_events), n_events)

    cols = ["user_id", "session_id", "event_type", "page", "referrer",
            "ip_address", "user_agent", "metadata", "created_at"]
    return batch_insert(cursor, "events", cols, rows[:n_events], batch_size=1000)


def seed_support_tickets(cursor, n_orders: int, n_users: int):
    n_tickets = n_orders // 20  # ~5% of orders generate a ticket
    print(f"\n🎫 Seeding {n_tickets:,} support tickets...")
    rows = []
    subjects = [
        "Order not received", "Wrong item delivered", "Refund request",
        "Payment failed", "Account locked", "Damaged product",
        "Cancel order", "Change shipping address", "Invoice request",
    ]
    for i in range(n_tickets):
        uid    = random.randint(1, n_users)
        oid    = random.randint(1, n_orders) if random.random() > 0.3 else None
        status = random.choices(["open","in_progress","resolved","closed"],
                                weights=[15, 20, 30, 35])[0]
        created = fake.date_time_between(start_date="-1y", end_date="now")
        resolved = (created + datetime.timedelta(days=random.randint(1, 14))
                    if status in ("resolved", "closed") else None)
        rows.append((
            uid, oid, random.choice(subjects),
            fake.paragraph(nb_sentences=2),
            status,
            random.choices(["low","normal","high","urgent"], weights=[20,50,20,10])[0],
            fake.name() if random.random() > 0.4 else None,
            created, resolved,
        ))
        if (i + 1) % 100 == 0 or i + 1 == n_tickets:
            progress("support_tickets", i + 1, n_tickets)
    cols = ["user_id","order_id","subject","body","status","priority",
            "assigned_to","created_at","resolved_at"]
    return batch_insert(cursor, "support_tickets", cols, rows)


def seed_episodic_memory():
    """
    Seeds the DBA vector memory with realistic historical incidents.
    Includes tool call traces, EXPLAIN output, and branch operation logs
    to demonstrate the episodic memory volume agents naturally generate.
    Uses the DBAMemory class so embeddings are generated consistently.
    """
    print("\n🧠 Seeding DBA episodic memory...")
    from memory import dba_memory

    incidents = [

        # ── Index fixes ──────────────────────────────────────────────────────
        {
            "incident_summary": "Slow query on orders table filtering by status='pending' causing 4.2s full-table scan on 50K rows",
            "resolution_sql": "CREATE INDEX idx_orders_status_created ON orders(status, created_at DESC);",
            "resolution_type": "INDEX_ADD",
            "resolution_description": "TableFullScan on orders (50K rows) for status filter. Composite index on (status, created_at DESC) enables IndexRangeScan and covers ORDER BY clause.",
            "error_details": "EXPLAIN: TableFullScan rows=50432 cost=5043.2 | Tool: run_explain → TableFullScan detected | Branch fix-orders-status-20241103 created, index applied, re-measured 18ms | Approved by user.",
            "before_time_ms": 4200, "after_time_ms": 18,
            "table_affected": "orders", "success_rating": 1.0,
        },
        {
            "incident_summary": "N+1 query pattern on order_items join to orders — missing FK index causing 200K row scan per request",
            "resolution_sql": "CREATE INDEX idx_order_items_order_id ON order_items(order_id);",
            "resolution_type": "INDEX_ADD",
            "resolution_description": "order_items lacked index on order_id. Each order detail page triggered a full scan. IndexLookup after fix reduced per-request DB time from 3100ms to 12ms.",
            "error_details": "Tool: run_explain → Join type: ALL on order_items | rows examined: 198432 per query | Branch fix-order-items-fk-20241109 | Before 3100ms After 12ms | 258x improvement",
            "before_time_ms": 3100, "after_time_ms": 12,
            "table_affected": "order_items", "success_rating": 1.0,
        },
        {
            "incident_summary": "Clickstream events table scan on user_id + event_type — analytics queries timing out at 30s",
            "resolution_sql": "CREATE INDEX idx_events_user_id_event_type ON events(user_id, event_type, created_at);",
            "resolution_type": "INDEX_ADD",
            "resolution_description": "events table at 200K rows with no index on user_id or event_type. Composite covering index enables IndexRangeScan and eliminates filesort for time-ordered queries.",
            "error_details": "Tool: run_explain → TableFullScan rows=201847 | Extra: Using filesort | Branch fix-events-composite-20241112 | Before: 8100ms After: 45ms | p99 latency: 142ms→8ms",
            "before_time_ms": 8100, "after_time_ms": 45,
            "table_affected": "events", "success_rating": 1.0,
        },
        {
            "incident_summary": "User dashboard slow — country + tier + is_active filter doing full scan on 10K user table",
            "resolution_sql": "CREATE INDEX idx_users_country_tier_active ON users(country, tier, is_active);",
            "resolution_type": "INDEX_ADD",
            "resolution_description": "Regional dashboard queries filter users by country, tier, is_active. Composite index converts TableFullScan to IndexRangeScan with selectivity ~0.003.",
            "error_details": "Tool: run_explain → TableFullScan rows=10000 | Estimated rows returned: 31 | selectivity 0.31% | Branch fix-users-dashboard-20241118 | Before 2800ms After 9ms",
            "before_time_ms": 2800, "after_time_ms": 9,
            "table_affected": "users", "success_rating": 0.98,
        },
        {
            "incident_summary": "Product aggregation queries scanning all order_items — missing composite index on product_id + quantity",
            "resolution_sql": "CREATE INDEX idx_order_items_product_id ON order_items(product_id, quantity);",
            "resolution_type": "INDEX_ADD",
            "resolution_description": "Inventory and revenue reports GROUP BY product_id SUM(quantity). Without an index, full scan on order_items for every report. Index reduces scan to covering index lookup.",
            "error_details": "Tool: run_explain → TableFullScan rows=198000 type=ALL | Tool: run_query_on_branch → verified 144ms→11ms | Production fix applied 2024-11-21",
            "before_time_ms": 5600, "after_time_ms": 22,
            "table_affected": "order_items", "success_rating": 1.0,
        },
        {
            "incident_summary": "Support ticket lookup by user_id and status returning stale results slowly — 6.1s on tickets table",
            "resolution_sql": "CREATE INDEX idx_support_user_status ON support_tickets(user_id, status, created_at DESC);",
            "resolution_type": "INDEX_ADD",
            "resolution_description": "Support portal queries ticket history per user filtered by status. Full scan at ~15K tickets growing 3% weekly. Composite index with DESC sort order eliminates filesort.",
            "error_details": "Tool: run_explain → type=ALL filesort=true rows=14821 | Trend: growing 3%/week, will degrade further | Branch fix-support-idx-20241202 | Before 6100ms After 14ms",
            "before_time_ms": 6100, "after_time_ms": 14,
            "table_affected": "support_tickets", "success_rating": 1.0,
        },
        {
            "incident_summary": "Product search by category and brand doing full scan — 500 products but query hitting 8 joins",
            "resolution_sql": "CREATE INDEX idx_products_category_brand ON products(category, brand, is_active);",
            "resolution_type": "INDEX_ADD",
            "resolution_description": "Storefront search filters products by category + brand + is_active. Even at 500 rows the 8-way join caused planning overhead. Index reduces scan rows from 500 to avg 12.",
            "error_details": "Tool: run_explain → join_type=ALL on products in 8-table join | optimizer chose wrong order | Index hint considered but index add preferred | Before 890ms After 7ms",
            "before_time_ms": 890, "after_time_ms": 7,
            "table_affected": "products", "success_rating": 0.95,
        },
        {
            "incident_summary": "Review aggregation by product rating doing full scan — avg rating query taking 1.9s",
            "resolution_sql": "CREATE INDEX idx_reviews_product_rating ON reviews(product_id, rating);",
            "resolution_type": "INDEX_ADD",
            "resolution_description": "Product pages compute average rating and rating distribution. Full scan on reviews table. Covering index on (product_id, rating) eliminates table access entirely.",
            "error_details": "Tool: run_explain → type=ALL rows=45000 | covering index possible | Branch fix-reviews-rating-20241208 | Before 1900ms After 6ms | 317x speedup",
            "before_time_ms": 1900, "after_time_ms": 6,
            "table_affected": "reviews", "success_rating": 1.0,
        },

        # ── Query rewrites / optimizer hints ─────────────────────────────────
        {
            "incident_summary": "Optimizer chose wrong join order on 6-table report query — suboptimal plan causing 12s execution",
            "resolution_sql": "SELECT /*+ LEADING(o, oi, u) */ ... (optimizer hint applied)",
            "resolution_type": "QUERY_REWRITE",
            "resolution_description": "TiDB optimizer chose a hash join starting from events table (200K rows) instead of orders (filtered set of ~800). LEADING hint forced correct join order. Could not add index as query is ad-hoc reporting.",
            "error_details": "Tool: run_explain → HashJoin build side: events rows=201847 | LEADING hint tested on branch | Before 12400ms After 340ms | Reported to app team for query update",
            "before_time_ms": 12400, "after_time_ms": 340,
            "table_affected": "orders", "success_rating": 0.85,
        },
        {
            "incident_summary": "Correlated subquery in ORDER status report causing per-row lookup — rewrite to JOIN reduced time 40x",
            "resolution_sql": "Rewrote SELECT ... WHERE id IN (SELECT ...) to explicit JOIN with filter pushdown",
            "resolution_type": "QUERY_REWRITE",
            "resolution_description": "Report query used correlated subquery checking order_items existence. Each outer row triggered inner scan. Rewrite to LEFT JOIN with IS NOT NULL filter allowed batch processing.",
            "error_details": "Tool: run_explain → DEPENDENT SUBQUERY type on order_items | rows per outer: 198000 | Rewrite provided to engineering | Before 38000ms After 920ms",
            "before_time_ms": 38000, "after_time_ms": 920,
            "table_affected": "orders", "success_rating": 0.9,
        },

        # ── Lock / contention incidents ───────────────────────────────────────
        {
            "incident_summary": "Deadlock on orders table during peak checkout — two transactions updating status in reverse order",
            "resolution_sql": "-- No DDL. Application fix: enforce consistent lock acquisition order in checkout service.",
            "resolution_type": "DEADLOCK_ANALYSIS",
            "resolution_description": "Two concurrent checkout flows acquired row locks on orders in opposite order (order A then B vs B then A). TiDB deadlock log confirmed cycle. Fix: application-level ordering by order_id ascending before any update.",
            "error_details": "Tool: run_query → SHOW ENGINE INNODB STATUS | Deadlock cycle: txn1 holds orders#1042 waits orders#1043, txn2 holds orders#1043 waits orders#1042 | Rate: 12 deadlocks/hour at peak",
            "before_time_ms": 0, "after_time_ms": 0,
            "table_affected": "orders", "success_rating": 0.9,
        },
        {
            "incident_summary": "Long-running analytics query blocking checkout writes — OLAP query holding shared locks for 45 seconds",
            "resolution_sql": "SET tidb_snapshot = NOW() - INTERVAL 30 SECOND; -- read historical snapshot",
            "resolution_type": "CONFIG_CHANGE",
            "resolution_description": "Analytics report ran a 45-second aggregation on orders table, blocking concurrent writes via shared lock contention. Fix: route analytics queries through TiDB's historical read (tidb_snapshot) to avoid lock conflicts entirely.",
            "error_details": "Tool: run_query → INFORMATION_SCHEMA.INNODB_TRX | trx_time=47s blocking 23 write txns | Fix: stale read via tidb_snapshot — reads MVCC snapshot, zero lock contention",
            "before_time_ms": 45000, "after_time_ms": 200,
            "table_affected": "orders", "success_rating": 1.0,
        },

        # ── Runaway / resource incidents ─────────────────────────────────────
        {
            "incident_summary": "Runaway query on events table — missing WHERE clause in reporting script scanning 200K rows continuously",
            "resolution_sql": "-- Query killed. Added MAX_EXECUTION_TIME(5000) hint and WHERE clause to reporting script.",
            "resolution_type": "RUNAWAY_QUERY",
            "resolution_description": "A reporting script accidentally submitted SELECT * FROM events without a WHERE clause. Query ran for 4 minutes consuming 85% of CPU. Killed via KILL QUERY. Added MAX_EXECUTION_TIME hint and mandatory date range filter to script.",
            "error_details": "Tool: run_query → SHOW PROCESSLIST | Query time: 243s state=Sending data | rows_examined=201847 | CPU: 85% | Action: KILL QUERY {pid} | Root cause: missing WHERE in cron script",
            "before_time_ms": 243000, "after_time_ms": 0,
            "table_affected": "events", "success_rating": 1.0,
        },
        {
            "incident_summary": "Memory spike from large IN() clause — product search with 5000 IDs causing OOM risk on TiDB node",
            "resolution_sql": "-- Rewrote IN(5000 ids) to JOIN against a temp table / batch the lookup in 500-id chunks",
            "resolution_type": "QUERY_REWRITE",
            "resolution_description": "Product recommendation engine passed 5000 product IDs in a single IN() clause. TiDB expanded this to a 5000-element range scan plan consuming 2.1GB of plan memory. Rewrote to batch 500 IDs per query across 10 parallel calls.",
            "error_details": "Tool: run_explain → IN list size=5000 | plan_memory=2.1GB | tidb_mem_quota_query exceeded warning | Batch rewrite: 10x500 parallel queries each <50ms | Total time: 180ms vs 45s single query",
            "before_time_ms": 45000, "after_time_ms": 180,
            "table_affected": "products", "success_rating": 0.95,
        },

        # ── Schema / statistics incidents ─────────────────────────────────────
        {
            "incident_summary": "Stale table statistics causing wrong index selection — TiDB estimated 10 rows, actual 48000",
            "resolution_sql": "ANALYZE TABLE orders;",
            "resolution_type": "STATS_UPDATE",
            "resolution_description": "After a bulk import of 40K orders the table statistics were stale. TiDB optimizer estimated 10 rows for a status filter (actual: 48K), chose a full index scan over a more selective composite index. ANALYZE TABLE updated histograms.",
            "error_details": "Tool: run_explain → estimated rows=10 actual rows=48021 | ratio=4802x off | Tool: run_query → SHOW STATS_META | modify_count=48000 outdated | ANALYZE TABLE orders resolved in 4.2s",
            "before_time_ms": 9800, "after_time_ms": 23,
            "table_affected": "orders", "success_rating": 1.0,
        },
        {
            "incident_summary": "Index not being used after bulk load — statistics divergence caused optimizer to prefer full scan",
            "resolution_sql": "ANALYZE TABLE events; ANALYZE TABLE order_items;",
            "resolution_type": "STATS_UPDATE",
            "resolution_description": "Post bulk-seed of 200K events and 198K order_items, optimizer statistics were completely stale (pre-load estimates). Running ANALYZE on both tables corrected histograms and restored index usage across 6 queries.",
            "error_details": "Tool: run_explain → IndexScan expected but TableFullScan chosen | SHOW STATS_HEALTHY → events: 0% order_items: 0% | ANALYZE TABLE both → healthy 100% | All 6 affected queries restored to index plans",
            "before_time_ms": 7200, "after_time_ms": 41,
            "table_affected": "events", "success_rating": 1.0,
        },

        # ── Branch operations / agent workflow memories ───────────────────────
        {
            "incident_summary": "Agent created branch fix-orders-composite to safely test 3 index additions before production deployment",
            "resolution_sql": "CREATE INDEX idx_orders_status_created ON orders(status, created_at DESC); CREATE INDEX idx_orders_user_status ON orders(user_id, status);",
            "resolution_type": "BRANCH_OPERATION",
            "resolution_description": "Opened TiDB Cloud branch fix-orders-composite (branch-id: bran-a1b2c3d4). Applied 3 indexes in isolation. Ran EXPLAIN ANALYZE before/after on 5 representative queries. All showed improvement. User approved. Branch deleted post-deployment.",
            "error_details": "Branch: bran-a1b2c3d4 | State: READY in 34s | DDL applied: 3 indexes | Benchmarked: 5 queries | Avg improvement: 94% | User approved: yes | Branch deleted: yes | Production deployment: success",
            "before_time_ms": 4800, "after_time_ms": 19,
            "table_affected": "orders", "success_rating": 1.0,
        },
        {
            "incident_summary": "Index addition on events table regressed a low-cardinality query — detected on branch before production",
            "resolution_sql": "-- Index dropped on branch after regression detected. Alternative: partial index with WHERE event_type='purchase'",
            "resolution_type": "REGRESSION_CAUGHT",
            "resolution_description": "Proposed index on events(event_type) improved the target query but caused optimizer to prefer it over a better composite index for low-cardinality scan of event_type='view' (92% of rows). Regression caught on branch. Index design revised to partial.",
            "error_details": "Branch: bran-e5f6g7h8 | Regression detected: query B went from 45ms to 380ms after index | Root cause: low cardinality, optimizer chose new index incorrectly | Branch deleted, fix redesigned | Production safe",
            "before_time_ms": 380, "after_time_ms": 45,
            "table_affected": "events", "success_rating": 1.0,
        },
        {
            "incident_summary": "Health check run autonomously — detected 4 missing indexes, created branch, benchmarked all fixes, presented approval request",
            "resolution_sql": "CREATE INDEX idx_events_user_id_event_type ON events(user_id, event_type); CREATE INDEX idx_orders_status_created_at ON orders(status, created_at DESC); CREATE INDEX idx_order_items_product_id ON order_items(product_id, quantity); CREATE INDEX idx_users_country_tier_active ON users(country, tier, is_active);",
            "resolution_type": "AUTONOMOUS_HEALTH_CHECK",
            "resolution_description": "Autonomous health check completed without user prompt. Scanned 9 tables, ran EXPLAIN ANALYZE on 12 representative queries, identified 4 high-impact missing indexes. Branch created, all 4 applied and verified. Presented structured approval request with before/after metrics.",
            "error_details": "Tool calls: run_explain×12, create_branch×1, apply_ddl_on_branch×4, run_query_on_branch×8, recall×1 | Total agent steps: 31 | Branch: bran-health-20241215 | Avg improvement: 97% | Memories written: 5 (this + 4 per-index)",
            "before_time_ms": 4200, "after_time_ms": 21,
            "table_affected": "multiple", "success_rating": 1.0,
        },

        # ── Memory system self-awareness ──────────────────────────────────────
        {
            "incident_summary": "Agent recalled prior events table index fix and applied same pattern to support_tickets without re-running full diagnosis",
            "resolution_sql": "CREATE INDEX idx_support_user_status ON support_tickets(user_id, status, created_at DESC);",
            "resolution_type": "MEMORY_ASSISTED_FIX",
            "resolution_description": "User reported slow support ticket lookup. Agent recalled semantically similar prior incident on events table (confidence: 0.91). Applied same composite index pattern (lookup_col, filter_col, time DESC) without full re-diagnosis. Fix confirmed on branch in 12 seconds.",
            "error_details": "Tool: recall → 'slow lookup by user filtering on status' → matched events incident confidence=0.91 | Pattern applied: composite(user_id, status, created_at DESC) | Branch verify: 6100ms→14ms | Memory pattern reuse saved ~8 min diagnosis time",
            "before_time_ms": 6100, "after_time_ms": 14,
            "table_affected": "support_tickets", "success_rating": 1.0,
        },
        {
            "incident_summary": "Vector similarity search across 847 stored memories completed in 4ms — TiDB HNSW index handling agent memory at scale",
            "resolution_sql": "-- No fix. Observation: TiDB vector search latency stable at 3-5ms across growing memory corpus.",
            "resolution_type": "MEMORY_SCALE_OBSERVATION",
            "resolution_description": "After 6 months of autonomous operation the agent episodic memory table reached 847 records with 384-dimensional embeddings. ANN similarity search latency measured at 3-5ms consistently. TiDB's unified architecture means no separate vector DB is needed — the same cluster handles OLTP workloads and vector recall simultaneously.",
            "error_details": "Memory count: 847 | Vector dimensions: 384 | ANN search p50: 4ms p99: 11ms | Index type: HNSW | Cluster: same TiDB cluster as production app | No separate vector DB required | Storage: 847 × 384 × 4 bytes = 1.3MB vectors + metadata",
            "before_time_ms": 0, "after_time_ms": 4,
            "table_affected": "dba_episodic_memory", "success_rating": 1.0,
        },
    ]

    for inc in incidents:
        ok = dba_memory.save(**inc)
        status = "✅" if ok else "❌"
        print(f"  {status} {inc['incident_summary'][:75]}...")

    print(f"\n  → {len(incidents)} memories seeded across {len(set(i['resolution_type'] for i in incidents))} incident types.")

# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Seed dba_agent_db with demo data")
    p.add_argument("--users",         type=int, default=10_000)
    p.add_argument("--products",      type=int, default=500)
    p.add_argument("--orders",        type=int, default=50_000)
    p.add_argument("--events",        type=int, default=200_000)
    p.add_argument("--memories-only", action="store_true",
                   help="Only seed episodic memory, skip application data")
    p.add_argument("--skip-memories", action="store_true",
                   help="Skip seeding episodic memory (needs TiDB Vector)")
    return p.parse_args()


def main():
    args = parse_args()

    if args.memories_only:
        seed_episodic_memory()
        print("\n✅ Done — episodic memory seeded.")
        return

    print("=" * 60)
    print("  DBA Agent Demo — Data Seeder")
    print("=" * 60)
    print(f"  Target: {os.getenv('TIDB_HOST')} / {os.getenv('TIDB_DATABASE','dba_agent_db')}")
    print(f"  Users:     {args.users:>10,}")
    print(f"  Products:  {args.products:>10,}")
    print(f"  Orders:    {args.orders:>10,}")
    print(f"  Events:    {args.events:>10,}")
    print("=" * 60)

    try:
        conn   = get_conn()
        cursor = conn.cursor()

        u = seed_users(cursor, args.users)
        conn.commit()

        # Power users: top 15% of user IDs by ID (proxy for high-activity accounts)
        power_user_ids = random.sample(
            range(1, args.users + 1),
            max(1, int(args.users * POWER_USER_FRACTION)),
        )
        print(f"\n⚡ {len(power_user_ids):,} power users identified "
              f"({POWER_USER_FRACTION*100:.0f}% of base, "
              f"{POWER_USER_ORDER_SHARE*100:.0f}% of activity)")

        p = seed_products(cursor, args.products)
        conn.commit()

        seed_orders(cursor, args.orders, args.users, args.products,
                    power_user_ids=power_user_ids)
        conn.commit()

        seed_reviews(cursor, args.orders, args.products, args.users)
        conn.commit()

        seed_events(cursor, args.events, args.users,
                    power_user_ids=power_user_ids)
        conn.commit()

        seed_support_tickets(cursor, args.orders, args.users)
        conn.commit()

        cursor.close()
        conn.close()

    except Error as e:
        print(f"\n❌ Database error: {e}")
        sys.exit(1)

    if not args.skip_memories:
        seed_episodic_memory()

    print("\n" + "=" * 60)
    print("  ✅ Seeding complete!")
    print("=" * 60)
    print("\nTry these slow queries on the agent:")
    print("  • \"All orders with status='pending' are loading slowly\"")
    print("  • \"The customer activity dashboard is timing out\"")
    print("  • \"Clickstream analytics are way too slow for user #42\"")
    print("  • \"The order details page is doing N+1 queries\"")


if __name__ == "__main__":
    main()
