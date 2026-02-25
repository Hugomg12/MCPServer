-- =============================================================================
-- Database schema for the Agent Lab project.
--
-- Defines the tables used by the MCP backend to manage products, inventory
-- (stock), orders, and stock reservations.
-- =============================================================================

-- Enable the pgcrypto extension so we can use gen_random_uuid() for UUID generation
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- Products table
-- Stores the product catalog. Each product has a unique SKU (Stock Keeping Unit)
-- and a human-readable name.
-- -----------------------------------------------------------------------------
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    sku VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL
);

-- -----------------------------------------------------------------------------
-- Stock table
-- Holds the current stock quantity for each product.
-- Each product has exactly one stock row (enforced by the UNIQUE constraint
-- on product_id). The updated_at timestamp tracks the last stock change.
-- -----------------------------------------------------------------------------
CREATE TABLE stock (
    id SERIAL PRIMARY KEY,
    product_id INTEGER REFERENCES products(id),
    quantity INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(product_id)
);

-- -----------------------------------------------------------------------------
-- Stock movements table
-- An audit log of every stock change: additions, subtractions, reservations, etc.
-- Each row records which product changed, by how much (delta), and why.
-- -----------------------------------------------------------------------------
CREATE TABLE stock_movements (
    id SERIAL PRIMARY KEY,
    product_id INTEGER REFERENCES products(id),
    delta INTEGER NOT NULL,
    reason VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Orders table
-- Stores order headers with a UUID primary key and a status field that tracks
-- the order lifecycle: PENDING → RESERVED → PAID (or CANCELLED / FAILED).
-- -----------------------------------------------------------------------------
CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(50) DEFAULT 'PENDING',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Order items table
-- Stores the line items for each order. Currently each order has one item,
-- identified by its SKU and the requested quantity.
-- -----------------------------------------------------------------------------
CREATE TABLE order_items (
    id SERIAL PRIMARY KEY,
    order_id UUID REFERENCES orders(id),
    sku VARCHAR(255) NOT NULL,
    qty INTEGER NOT NULL
);

-- -----------------------------------------------------------------------------
-- Reservations table
-- Tracks stock that has been reserved for an order but not yet confirmed (paid).
-- When a reservation is released, active is set to FALSE and released_at is
-- populated. This allows the system to return reserved stock if an order is cancelled.
-- -----------------------------------------------------------------------------
CREATE TABLE reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders(id),
    sku VARCHAR(255) NOT NULL,
    qty INTEGER NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    released_at TIMESTAMPTZ
);