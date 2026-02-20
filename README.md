# Event-Driven E-commerce Backend

A **production-ready, Amazon-style microservices platform** demonstrating distributed systems patterns, event-driven architecture, and horizontal scaling.

> **Interview line**: "All state is in Postgres/Redis/Kafka; services are stateless so we can scale by adding instances."

## Architecture (Scaled)

```
                         ┌─────────────────────────────────────┐
                         │         NGINX LOAD BALANCER         │
                         │         (Rate Limiting, Routing)    │
                         └──────────────────┬──────────────────┘
                                            │
        ┌───────────────┬───────────────────┼───────────────────┬───────────────┐
        ▼               ▼                   ▼                   ▼               ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│ User Service  │ │ Order Service │ │ Order Service │ │ Order Service │ │ Inventory     │
│ Replica 1     │ │ Replica 1     │ │ Replica 2     │ │ Replica 3     │ │ Replica 1-3   │
└───────┬───────┘ └───────┬───────┘ └───────┬───────┘ └───────┬───────┘ └───────┬───────┘
        │                 │                 │                 │                 │
        └─────────────────┴─────────────────┼─────────────────┴─────────────────┘
                                            ▼
                    ┌───────────────────────────────────────────────────────┐
                    │              KAFKA CLUSTER (3 Brokers)                │
                    │  Topics: orders(6p), inventory(6p), payments(6p)      │
                    │  Partition Keys: orderId, skuId for ordering          │
                    └───────────────────────────────────────────────────────┘
                                            │
        ┌───────────────────────────────────┼───────────────────────────────────┐
        ▼                                   ▼                                   ▼
┌───────────────┐                   ┌───────────────┐                   ┌───────────────┐
│  PostgreSQL   │                   │     Redis     │                   │  Prometheus   │
│  Primary +    │                   │   (Caching,   │                   │  + Grafana    │
│  Read Replica │                   │  Reservations)│                   │  (Monitoring) │
└───────────────┘                   └───────────────┘                   └───────────────┘
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| **User Service** | 8001 | Authentication, user management, JWT tokens |
| **Order Service** | 8002 | Order creation, status tracking, order history |
| **Inventory Service** | 8003 | Stock management, reservations, availability |
| **Payment Service** | 8004 | Payment processing (mock), refunds |
| **API Gateway** | 8000 | Request routing, rate limiting, auth validation |

## Event Flow

```
1. User places order → Order Service
2. Order Service publishes "order.created" → Kafka
3. Inventory Service consumes → reserves stock → publishes "inventory.reserved"
4. Payment Service consumes → processes payment → publishes "payment.completed"
5. Order Service consumes → updates order status → publishes "order.confirmed"
6. Notification Service consumes → sends email/SMS
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Language** | Python 3.11+ |
| **Framework** | FastAPI |
| **Database** | PostgreSQL |
| **Cache** | Redis |
| **Message Broker** | Apache Kafka |
| **Containerization** | Docker, Docker Compose |
| **API Docs** | OpenAPI/Swagger |

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.11+

### Run with Docker Compose

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Run Locally (Development)

```bash
# Install dependencies
pip install -r requirements.txt

# Start infrastructure (Kafka, Redis, PostgreSQL)
docker-compose up -d kafka redis postgres

# Run each service
cd services/user-service && uvicorn main:app --port 8001
cd services/order-service && uvicorn main:app --port 8002
cd services/inventory-service && uvicorn main:app --port 8003
cd services/payment-service && uvicorn main:app --port 8004
```

## API Endpoints

### User Service (8001)
```
POST /api/v1/users/register     - Register new user
POST /api/v1/users/login        - Login, get JWT token
GET  /api/v1/users/me           - Get current user profile
PUT  /api/v1/users/me           - Update profile
```

### Order Service (8002)
```
POST /api/v1/orders             - Create new order
GET  /api/v1/orders             - List user's orders
GET  /api/v1/orders/{id}        - Get order details
PUT  /api/v1/orders/{id}/cancel - Cancel order
```

### Inventory Service (8003)
```
GET  /api/v1/products           - List products
GET  /api/v1/products/{id}      - Get product details
POST /api/v1/products           - Add product (admin)
PUT  /api/v1/products/{id}/stock - Update stock
```

### Payment Service (8004)
```
POST /api/v1/payments           - Process payment
GET  /api/v1/payments/{id}      - Get payment status
POST /api/v1/payments/{id}/refund - Refund payment
```

## Kafka Topics

| Topic | Publisher | Consumers | Purpose |
|-------|-----------|-----------|---------|
| `orders` | Order Service | Inventory, Payment, Notification | Order lifecycle events |
| `inventory` | Inventory Service | Order Service | Stock updates |
| `payments` | Payment Service | Order Service | Payment status |
| `users` | User Service | All services | User events |

## Database Schema

### Users Table
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE,
    password_hash VARCHAR(255),
    name VARCHAR(100),
    created_at TIMESTAMP
);
```

### Orders Table
```sql
CREATE TABLE orders (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    status VARCHAR(50),
    total_amount DECIMAL(10,2),
    created_at TIMESTAMP
);
```

### Products Table
```sql
CREATE TABLE products (
    id UUID PRIMARY KEY,
    name VARCHAR(255),
    price DECIMAL(10,2),
    stock_quantity INT,
    reserved_quantity INT
);
```

## Amazon-Style Scaling Patterns

### 1. Stateless Services + Horizontal Scaling
```bash
# Scale to 5 order service instances
docker-compose -f docker-compose.prod.yml up -d --scale order-service=5
```
> **Interview line**: "All state is in Postgres/Redis/Kafka; services are stateless so we can scale by adding instances."

### 2. Kafka Partitioning Strategy
| Topic | Partition Key | Partitions | Purpose |
|-------|---------------|------------|---------|
| `orders` | `orderId` | 6 | Ordering guarantees per order |
| `inventory` | `skuId` | 6 | Avoid races on same SKU |
| `payments` | `orderId` | 6 | Correlate with orders |
| `dead-letter` | `eventId` | 3 | Failed message handling |

> **Interview line**: "We scale Kafka consumers by increasing partitions and consumer replicas; ordering preserved per key."

### 3. Inventory Consistency (Hardest Problem)
```sql
-- Atomic reservation with optimistic concurrency
UPDATE inventory 
SET available = available - :qty, reserved = reserved + :qty
WHERE sku_id = :sku AND available >= :qty;
```
- **Reservation + Expiry Model**: Reserve with 10-minute TTL
- **Auto-release**: If payment fails/times out
- **Idempotency**: Each reservation has unique ID

> **Interview line**: "Inventory is the consistency boundary; we use atomic updates + idempotent events to prevent oversell."

### 4. Reliability Patterns
| Pattern | Implementation |
|---------|----------------|
| **Idempotency** | Event IDs + processed event tracking |
| **Retry + DLQ** | Exponential backoff, dead-letter after N retries |
| **Outbox Pattern** | DB + outbox in same transaction, background publisher |
| **Consumer Groups** | Each service = consumer group for load balancing |

> **Interview line**: "Outbox pattern guarantees we never lose events between DB and Kafka."

### 5. Database Scaling
- **Service-owned schemas**: Each service owns its tables
- **Read replicas**: For heavy reads (order history, catalog)
- **Optimized indexes**: `orders(user_id, created_at)`, `inventory(sku_id)`
- **Redis caching**: Hot data with TTL

### 6. Observability
| Metric | Target |
|--------|--------|
| p95 latency per endpoint | < 200ms |
| Kafka lag per consumer | < 1000 |
| Order success rate | > 99% |
| Inventory oversell incidents | 0 |

> **Interview line**: "We built dashboards, alerts, and runbooks and treated this like an on-call service."

## Load Testing

```bash
# Run with Locust
locust -f loadtest/locustfile.py --host=http://localhost:8000

# Run with k6
k6 run loadtest/k6_load_test.js
```

### Performance Targets
| Metric | Target | Achieved |
|--------|--------|----------|
| Throughput | 100-1000 RPS | ✅ |
| p95 Latency | < 200ms | ✅ |
| Error Rate | < 1% | ✅ |
| Kafka Lag | < 1000 | ✅ |

## Production Deployment

```bash
# Full production stack with scaling
docker-compose -f docker-compose.prod.yml up -d

# Includes:
# - 3 Kafka brokers
# - 3 Order service replicas
# - 3 Inventory service replicas
# - 3 Payment service replicas
# - 2 User service replicas
# - Nginx load balancer
# - Prometheus + Grafana monitoring
```

## Runbooks

See `docs/runbooks/` for operational runbooks:
- Kafka lag high
- Payment service down
- Inventory reservation leak
- DB CPU spikes

## License

MIT License
