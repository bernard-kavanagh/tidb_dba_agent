/*
   TiDB Schema — E-Commerce Application Backend
   -----------------------------------------------
   Mimics a real SaaS / e-commerce platform backend.
   Designed for the Safety-First DBA Agent demo.

   Intentional performance issues (no indexes on hot columns):
     • orders.status        — full-table scan on status filter
     • orders.customer_id   — FK with no covering index
     • order_items.order_id — FK with no index (N+1 pattern)
     • events.user_id       — high-cardinality filter, unindexed
     • events.event_type    — unindexed enum-like column
   The agent will detect these via EXPLAIN ANALYZE and propose fixes.

   Run against: dba_agent_db
*/

CREATE DATABASE IF NOT EXISTS dba_agent_db;
USE dba_agent_db;

-- ============================================================
-- 1. USERS  (customers / accounts)
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    user_id       INT AUTO_INCREMENT PRIMARY KEY,
    email         VARCHAR(150) NOT NULL UNIQUE,
    full_name     VARCHAR(120) NOT NULL,
    phone         VARCHAR(20),
    country       VARCHAR(60)  NOT NULL DEFAULT 'IE',
    region        VARCHAR(60),
    tier          ENUM('free','pro','enterprise') NOT NULL DEFAULT 'free',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at DATETIME
    -- ⚠️  No index on (tier), (country), (created_at) — intentional for demo
);

-- ============================================================
-- 2. PRODUCTS  (catalogue)
-- ============================================================

CREATE TABLE IF NOT EXISTS products (
    product_id   INT AUTO_INCREMENT PRIMARY KEY,
    sku          VARCHAR(60)  NOT NULL UNIQUE,
    name         VARCHAR(200) NOT NULL,
    category     VARCHAR(80)  NOT NULL,
    sub_category VARCHAR(80),
    brand        VARCHAR(80),
    price        DECIMAL(10,2) NOT NULL,
    cost         DECIMAL(10,2),
    stock_qty    INT NOT NULL DEFAULT 0,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    description  TEXT,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    -- ⚠️  No index on (category), (brand) — intentional
);

-- ============================================================
-- 3. ORDERS  (transactions)
-- ============================================================

CREATE TABLE IF NOT EXISTS orders (
    order_id       INT AUTO_INCREMENT PRIMARY KEY,
    user_id        INT          NOT NULL,
    status         VARCHAR(20)  NOT NULL DEFAULT 'pending',
                   -- values: pending | processing | shipped | delivered | cancelled | refunded
    subtotal       DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    discount       DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    tax            DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    total_amount   DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    currency       CHAR(3)      NOT NULL DEFAULT 'EUR',
    payment_method VARCHAR(30),
    shipping_addr  VARCHAR(300),
    notes          TEXT,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    shipped_at     DATETIME,
    delivered_at   DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
    -- ⚠️  No index on (status), (user_id), (created_at) — intentional for demo
    -- This makes queries like "all pending orders" do full-table scans
);

-- ============================================================
-- 4. ORDER ITEMS  (line items per order)
-- ============================================================

CREATE TABLE IF NOT EXISTS order_items (
    item_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id    INT         NOT NULL,
    product_id  INT         NOT NULL,
    quantity    INT         NOT NULL DEFAULT 1,
    unit_price  DECIMAL(10,2) NOT NULL,
    discount    DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    line_total  DECIMAL(10,2) NOT NULL,
    FOREIGN KEY (order_id)   REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
    -- ⚠️  No index on (order_id) — causes N+1 full scans when joining to orders
);

-- ============================================================
-- 5. REVIEWS  (product reviews)
-- ============================================================

CREATE TABLE IF NOT EXISTS reviews (
    review_id   INT AUTO_INCREMENT PRIMARY KEY,
    product_id  INT NOT NULL,
    user_id     INT NOT NULL,
    rating      TINYINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title       VARCHAR(200),
    body        TEXT,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(product_id),
    FOREIGN KEY (user_id)    REFERENCES users(user_id)
    -- ⚠️  No index on (product_id), (rating) — intentional
);

-- ============================================================
-- 6. EVENTS  (clickstream / audit log — high-volume)
-- ============================================================

CREATE TABLE IF NOT EXISTS events (
    event_id   BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT          NOT NULL,
    session_id VARCHAR(64)  NOT NULL,
    event_type VARCHAR(50)  NOT NULL,
               -- values: page_view | add_to_cart | checkout_start |
               --         checkout_complete | search | product_view | login | logout
    page       VARCHAR(200),
    referrer   VARCHAR(300),
    ip_address VARCHAR(45),
    user_agent VARCHAR(300),
    metadata   JSON,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
    -- ⚠️  No index on (user_id), (event_type), (created_at) — intentional
    -- This table will be the main source of slow-query demo pain
);

-- ============================================================
-- 7. SUPPORT TICKETS  (customer service)
-- ============================================================

CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_id   INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT         NOT NULL,
    order_id    INT,
    subject     VARCHAR(300) NOT NULL,
    body        TEXT,
    status      VARCHAR(20)  NOT NULL DEFAULT 'open',
               -- values: open | in_progress | resolved | closed
    priority    VARCHAR(10)  NOT NULL DEFAULT 'normal',
               -- values: low | normal | high | urgent
    assigned_to VARCHAR(80),
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME,
    FOREIGN KEY (user_id)  REFERENCES users(user_id),
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

-- ============================================================
-- 8. DBA EPISODIC MEMORY  (agent knowledge — vector store)
-- ============================================================
-- NOTE: This table is owned and auto-created by TiDBVectorStore
-- (langchain-tidb). Do NOT define it here — TiDBVectorStore will
-- create it with its required schema on first use:
--   id          VARCHAR(36)  PRIMARY KEY
--   embedding   VECTOR(384)
--   document    TEXT                  -- incident summary (used for ANN search)
--   meta        JSON                  -- all structured fields as JSON
--   create_time TIMESTAMP
-- Queries against this table must use JSON extraction:
--   meta->>'$.resolution_type', meta->>'$.before_time_ms', etc.

-- ============================================================
-- 9. INCIDENT LOG  (agent workflow trace)
-- ============================================================

CREATE TABLE IF NOT EXISTS incident_log (
    log_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
    incident_id VARCHAR(50)  NOT NULL,
    step_name   VARCHAR(50)  NOT NULL,
    step_detail TEXT,
    branch_name VARCHAR(100),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_incident (incident_id)
);
