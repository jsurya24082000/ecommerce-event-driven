-- =============================================================================
-- E-commerce Database Schema with Scaling Optimizations
-- =============================================================================
-- Features:
-- - Service-owned tables (schema separation)
-- - Optimized indexes for common queries
-- - Outbox pattern table
-- - Inventory reservation tracking
-- =============================================================================

-- Users Schema (owned by user-service)
CREATE SCHEMA IF NOT EXISTS users;

CREATE TABLE IF NOT EXISTS users.accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_users_email ON users.accounts(email);

-- Orders Schema (owned by order-service)
CREATE SCHEMA IF NOT EXISTS orders;

CREATE TABLE IF NOT EXISTS orders.orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    total_amount DECIMAL(10, 2) NOT NULL,
    shipping_address JSONB,
    correlation_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders.order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders.orders(id),
    sku_id VARCHAR(50) NOT NULL,
    quantity INT NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for order queries
CREATE INDEX idx_orders_user_id_created ON orders.orders(user_id, created_at DESC);
CREATE INDEX idx_orders_status ON orders.orders(status);
CREATE INDEX idx_order_items_order_id ON orders.order_items(order_id);
CREATE INDEX idx_order_items_sku ON orders.order_items(sku_id);

-- Inventory Schema (owned by inventory-service)
CREATE SCHEMA IF NOT EXISTS inventory;

CREATE TABLE IF NOT EXISTS inventory.products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku_id VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price DECIMAL(10, 2) NOT NULL,
    category VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS inventory.stock (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku_id VARCHAR(50) UNIQUE NOT NULL REFERENCES inventory.products(sku_id),
    available_quantity INT NOT NULL DEFAULT 0,
    reserved_quantity INT NOT NULL DEFAULT 0,
    sold_quantity INT NOT NULL DEFAULT 0,
    low_stock_threshold INT DEFAULT 10,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    version INT DEFAULT 1  -- Optimistic locking
);

CREATE TABLE IF NOT EXISTS inventory.reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reservation_id VARCHAR(100) NOT NULL,
    order_id UUID NOT NULL,
    sku_id VARCHAR(50) NOT NULL,
    quantity INT NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    confirmed_at TIMESTAMP WITH TIME ZONE,
    released_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for inventory queries
CREATE INDEX idx_inventory_sku ON inventory.stock(sku_id);
CREATE INDEX idx_products_category ON inventory.products(category);
CREATE INDEX idx_reservations_status_expires ON inventory.reservations(status, expires_at);
CREATE INDEX idx_reservations_order ON inventory.reservations(order_id);

-- Payments Schema (owned by payment-service)
CREATE SCHEMA IF NOT EXISTS payments;

CREATE TABLE IF NOT EXISTS payments.transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL,
    amount DECIMAL(10, 2) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    payment_method VARCHAR(50),
    external_transaction_id VARCHAR(255),
    error_message TEXT,
    correlation_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for payment queries
CREATE INDEX idx_payments_order ON payments.transactions(order_id);
CREATE INDEX idx_payments_status ON payments.transactions(status);

-- =============================================================================
-- OUTBOX PATTERN TABLE (for reliable event publishing)
-- =============================================================================

CREATE TABLE IF NOT EXISTS outbox_events (
    id UUID PRIMARY KEY,
    aggregate_type VARCHAR(50) NOT NULL,
    aggregate_id VARCHAR(100) NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    partition_key VARCHAR(100) NOT NULL,
    topic VARCHAR(100) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    published_at TIMESTAMP WITH TIME ZONE,
    retry_count INT DEFAULT 0,
    error_message TEXT
);

CREATE INDEX idx_outbox_status_created ON outbox_events(status, created_at);
CREATE INDEX idx_outbox_aggregate ON outbox_events(aggregate_type, aggregate_id);

-- =============================================================================
-- IDEMPOTENCY TABLE (for exactly-once processing)
-- =============================================================================

CREATE TABLE IF NOT EXISTS processed_events (
    event_id UUID PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Auto-cleanup old processed events (keep 7 days)
CREATE INDEX idx_processed_events_time ON processed_events(processed_at);

-- =============================================================================
-- SAMPLE DATA
-- =============================================================================

-- Insert sample products
INSERT INTO inventory.products (sku_id, name, description, price, category) VALUES
    ('SKU-0001', 'Wireless Mouse', 'Ergonomic wireless mouse', 29.99, 'Electronics'),
    ('SKU-0002', 'Mechanical Keyboard', 'RGB mechanical keyboard', 89.99, 'Electronics'),
    ('SKU-0003', 'USB-C Hub', '7-port USB-C hub', 49.99, 'Electronics'),
    ('SKU-0004', 'Monitor Stand', 'Adjustable monitor stand', 39.99, 'Accessories'),
    ('SKU-0005', 'Webcam HD', '1080p HD webcam', 59.99, 'Electronics')
ON CONFLICT (sku_id) DO NOTHING;

-- Insert sample stock
INSERT INTO inventory.stock (sku_id, available_quantity, reserved_quantity, sold_quantity) VALUES
    ('SKU-0001', 100, 0, 50),
    ('SKU-0002', 75, 0, 25),
    ('SKU-0003', 200, 0, 100),
    ('SKU-0004', 150, 0, 30),
    ('SKU-0005', 80, 0, 40)
ON CONFLICT (sku_id) DO NOTHING;

-- =============================================================================
-- FUNCTIONS FOR ATOMIC OPERATIONS
-- =============================================================================

-- Atomic stock reservation
CREATE OR REPLACE FUNCTION inventory.reserve_stock(
    p_sku_id VARCHAR(50),
    p_quantity INT,
    p_reservation_id VARCHAR(100),
    p_order_id UUID,
    p_ttl_minutes INT DEFAULT 10
) RETURNS BOOLEAN AS $$
DECLARE
    v_affected INT;
BEGIN
    -- Atomic update with optimistic locking
    UPDATE inventory.stock
    SET 
        available_quantity = available_quantity - p_quantity,
        reserved_quantity = reserved_quantity + p_quantity,
        updated_at = NOW(),
        version = version + 1
    WHERE sku_id = p_sku_id
    AND available_quantity >= p_quantity;
    
    GET DIAGNOSTICS v_affected = ROW_COUNT;
    
    IF v_affected > 0 THEN
        -- Record reservation
        INSERT INTO inventory.reservations 
            (reservation_id, order_id, sku_id, quantity, status, expires_at)
        VALUES 
            (p_reservation_id, p_order_id, p_sku_id, p_quantity, 'pending', 
             NOW() + (p_ttl_minutes || ' minutes')::INTERVAL);
        RETURN TRUE;
    ELSE
        RETURN FALSE;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Confirm reservation (after payment)
CREATE OR REPLACE FUNCTION inventory.confirm_reservation(
    p_reservation_id VARCHAR(100)
) RETURNS BOOLEAN AS $$
DECLARE
    v_reservation RECORD;
BEGIN
    SELECT * INTO v_reservation
    FROM inventory.reservations
    WHERE reservation_id = p_reservation_id
    AND status = 'pending'
    FOR UPDATE;
    
    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;
    
    -- Move from reserved to sold
    UPDATE inventory.stock
    SET 
        reserved_quantity = reserved_quantity - v_reservation.quantity,
        sold_quantity = sold_quantity + v_reservation.quantity,
        updated_at = NOW()
    WHERE sku_id = v_reservation.sku_id;
    
    -- Update reservation status
    UPDATE inventory.reservations
    SET status = 'confirmed', confirmed_at = NOW()
    WHERE reservation_id = p_reservation_id;
    
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- Release reservation (payment failed or expired)
CREATE OR REPLACE FUNCTION inventory.release_reservation(
    p_reservation_id VARCHAR(100),
    p_reason VARCHAR(20) DEFAULT 'released'
) RETURNS BOOLEAN AS $$
DECLARE
    v_reservation RECORD;
BEGIN
    SELECT * INTO v_reservation
    FROM inventory.reservations
    WHERE reservation_id = p_reservation_id
    AND status = 'pending'
    FOR UPDATE;
    
    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;
    
    -- Return reserved to available
    UPDATE inventory.stock
    SET 
        reserved_quantity = reserved_quantity - v_reservation.quantity,
        available_quantity = available_quantity + v_reservation.quantity,
        updated_at = NOW()
    WHERE sku_id = v_reservation.sku_id;
    
    -- Update reservation status
    UPDATE inventory.reservations
    SET status = p_reason, released_at = NOW()
    WHERE reservation_id = p_reservation_id;
    
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- Expire stale reservations (called by background worker)
CREATE OR REPLACE FUNCTION inventory.expire_stale_reservations()
RETURNS INT AS $$
DECLARE
    v_count INT := 0;
    v_reservation RECORD;
BEGIN
    FOR v_reservation IN 
        SELECT reservation_id 
        FROM inventory.reservations
        WHERE status = 'pending'
        AND expires_at < NOW()
        FOR UPDATE SKIP LOCKED
    LOOP
        PERFORM inventory.release_reservation(v_reservation.reservation_id, 'expired');
        v_count := v_count + 1;
    END LOOP;
    
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;
