# Event-Driven E-commerce Backend

A production-ready microservices-based e-commerce platform demonstrating distributed systems patterns, event-driven architecture, and scalable design.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   API       │     │   User      │     │   Order     │     │  Inventory  │
│   Gateway   │────▶│   Service   │     │   Service   │     │   Service   │
│   (nginx)   │     │   :8001     │     │   :8002     │     │   :8003     │
└─────────────┘     └──────┬──────┘     └──────┬──────┘     └──────┬──────┘
                           │                   │                   │
                           │         ┌─────────┴─────────┐         │
                           │         │                   │         │
                           ▼         ▼                   ▼         ▼
                    ┌─────────────────────────────────────────────────┐
                    │                  KAFKA                          │
                    │  Topics: orders, payments, inventory, users     │
                    └─────────────────────────────────────────────────┘
                           │                   │                   │
                           ▼                   ▼                   ▼
                    ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
                    │  Payment    │     │   Redis     │     │  PostgreSQL │
                    │  Service    │     │   Cache     │     │   Database  │
                    │   :8004     │     │   :6379     │     │   :5432     │
                    └─────────────┘     └─────────────┘     └─────────────┘
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

## Consistency Patterns

1. **Saga Pattern** - Distributed transactions across services
2. **Outbox Pattern** - Reliable event publishing
3. **Idempotency** - Safe retry handling
4. **Eventual Consistency** - Async updates via Kafka

## Scaling Strategies

- **Horizontal Scaling**: Each service can scale independently
- **Database Sharding**: User-based partitioning
- **Caching**: Redis for hot data (products, sessions)
- **Kafka Partitions**: Parallel event processing

## Monitoring

- **Health Checks**: `/health` endpoint on each service
- **Metrics**: Prometheus-compatible `/metrics`
- **Tracing**: OpenTelemetry integration ready

## License

MIT License
