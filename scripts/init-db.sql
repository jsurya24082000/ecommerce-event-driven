-- Initialize E-commerce Database Schema

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(36) PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Products table
CREATE TABLE IF NOT EXISTS products (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price DECIMAL(10, 2) NOT NULL,
    stock_quantity INTEGER DEFAULT 0,
    reserved_quantity INTEGER DEFAULT 0,
    category VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Orders table
CREATE TABLE IF NOT EXISTS orders (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id),
    status VARCHAR(50) DEFAULT 'pending',
    total_amount DECIMAL(10, 2) NOT NULL,
    shipping_address VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Order items table
CREATE TABLE IF NOT EXISTS order_items (
    id VARCHAR(36) PRIMARY KEY,
    order_id VARCHAR(36) NOT NULL REFERENCES orders(id),
    product_id VARCHAR(36) NOT NULL,
    product_name VARCHAR(255),
    quantity INTEGER NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL
);

-- Payments table
CREATE TABLE IF NOT EXISTS payments (
    id VARCHAR(36) PRIMARY KEY,
    order_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    amount DECIMAL(10, 2) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    payment_method VARCHAR(50) DEFAULT 'credit_card',
    transaction_id VARCHAR(100),
    error_message VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id);
CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);

-- Sample products
INSERT INTO products (id, name, description, price, stock_quantity, category) VALUES
    ('prod-001', 'Wireless Headphones', 'Premium noise-canceling wireless headphones', 149.99, 100, 'Electronics'),
    ('prod-002', 'Smart Watch', 'Fitness tracking smartwatch with heart rate monitor', 299.99, 50, 'Electronics'),
    ('prod-003', 'Laptop Stand', 'Ergonomic aluminum laptop stand', 49.99, 200, 'Accessories'),
    ('prod-004', 'USB-C Hub', '7-in-1 USB-C hub with HDMI and SD card reader', 79.99, 150, 'Accessories'),
    ('prod-005', 'Mechanical Keyboard', 'RGB mechanical gaming keyboard', 129.99, 75, 'Electronics'),
    ('prod-006', 'Wireless Mouse', 'Ergonomic wireless mouse with adjustable DPI', 39.99, 300, 'Accessories'),
    ('prod-007', 'Monitor Light Bar', 'LED monitor light bar for reduced eye strain', 59.99, 120, 'Accessories'),
    ('prod-008', 'Webcam HD', '1080p HD webcam with built-in microphone', 89.99, 80, 'Electronics'),
    ('prod-009', 'Desk Mat', 'Large extended desk mat for keyboard and mouse', 29.99, 250, 'Accessories'),
    ('prod-010', 'Portable SSD', '1TB portable SSD with USB 3.2', 119.99, 60, 'Storage')
ON CONFLICT (id) DO NOTHING;
