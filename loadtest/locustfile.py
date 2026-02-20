"""
Load Testing Suite for E-commerce Event-Driven Backend.

Targets:
- 100 RPS → 1000 RPS sustained throughput
- p95 latency < 200ms
- Error rate < 1%
- Spike tests
- Failure injection tests

Usage:
    locust -f locustfile.py --host=http://localhost:8000
    
    # Headless mode for CI/CD
    locust -f locustfile.py --host=http://localhost:8000 \
           --headless -u 100 -r 10 --run-time 5m
"""

import random
import string
import uuid
import json
from locust import HttpUser, task, between, events
from locust.runners import MasterRunner


def random_email():
    """Generate random email."""
    return f"user_{uuid.uuid4().hex[:8]}@test.com"


def random_string(length=8):
    """Generate random string."""
    return ''.join(random.choices(string.ascii_lowercase, k=length))


class EcommerceUser(HttpUser):
    """Simulated e-commerce user behavior."""
    
    wait_time = between(0.1, 0.5)  # Fast requests for load testing
    
    def on_start(self):
        """Setup: Register and login user."""
        self.user_id = None
        self.token = None
        self.order_ids = []
        
        # Register user
        email = random_email()
        password = "TestPass123!"
        
        response = self.client.post("/api/users/register", json={
            "email": email,
            "password": password,
            "name": f"Test User {random_string()}"
        })
        
        if response.status_code == 201:
            data = response.json()
            self.user_id = data.get("user_id")
        
        # Login
        response = self.client.post("/api/auth/login", json={
            "email": email,
            "password": password
        })
        
        if response.status_code == 200:
            data = response.json()
            self.token = data.get("access_token")
    
    @property
    def auth_headers(self):
        """Get authorization headers."""
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}
    
    @task(10)
    def browse_products(self):
        """Browse product catalog (high frequency)."""
        self.client.get("/api/products", headers=self.auth_headers)
    
    @task(5)
    def view_product(self):
        """View single product."""
        product_id = random.randint(1, 100)
        self.client.get(
            f"/api/products/{product_id}",
            headers=self.auth_headers,
            name="/api/products/[id]"
        )
    
    @task(3)
    def check_inventory(self):
        """Check inventory availability."""
        sku_id = f"SKU-{random.randint(1, 50):04d}"
        self.client.get(
            f"/api/inventory/{sku_id}",
            headers=self.auth_headers,
            name="/api/inventory/[sku]"
        )
    
    @task(2)
    def create_order(self):
        """Create a new order."""
        if not self.token:
            return
        
        order_data = {
            "items": [
                {
                    "sku_id": f"SKU-{random.randint(1, 50):04d}",
                    "quantity": random.randint(1, 3),
                    "price": round(random.uniform(10, 100), 2)
                }
                for _ in range(random.randint(1, 3))
            ],
            "shipping_address": {
                "street": "123 Test St",
                "city": "Test City",
                "zip": "12345"
            }
        }
        
        with self.client.post(
            "/api/orders",
            json=order_data,
            headers=self.auth_headers,
            catch_response=True
        ) as response:
            if response.status_code == 201:
                data = response.json()
                order_id = data.get("order_id")
                if order_id:
                    self.order_ids.append(order_id)
                response.success()
            elif response.status_code == 400:
                # Insufficient stock is expected
                response.success()
            else:
                response.failure(f"Order creation failed: {response.status_code}")
    
    @task(1)
    def process_payment(self):
        """Process payment for an order."""
        if not self.order_ids or not self.token:
            return
        
        order_id = random.choice(self.order_ids)
        
        payment_data = {
            "order_id": order_id,
            "payment_method": "credit_card",
            "card_token": f"tok_{uuid.uuid4().hex[:16]}"
        }
        
        with self.client.post(
            "/api/payments",
            json=payment_data,
            headers=self.auth_headers,
            catch_response=True
        ) as response:
            if response.status_code in [200, 201]:
                response.success()
            elif response.status_code == 400:
                # Already paid or invalid state
                response.success()
            else:
                response.failure(f"Payment failed: {response.status_code}")
    
    @task(2)
    def get_order_history(self):
        """Get user's order history."""
        if not self.token:
            return
        
        self.client.get("/api/orders", headers=self.auth_headers)
    
    @task(1)
    def get_order_status(self):
        """Check order status."""
        if not self.order_ids or not self.token:
            return
        
        order_id = random.choice(self.order_ids)
        self.client.get(
            f"/api/orders/{order_id}",
            headers=self.auth_headers,
            name="/api/orders/[id]"
        )


class HighThroughputUser(HttpUser):
    """User for high-throughput testing (100+ RPS)."""
    
    wait_time = between(0.01, 0.05)  # Very fast
    
    @task(10)
    def health_check(self):
        """Health check endpoint."""
        self.client.get("/health")
    
    @task(5)
    def browse_products(self):
        """Browse products."""
        self.client.get("/api/products")
    
    @task(3)
    def check_inventory(self):
        """Check inventory."""
        sku_id = f"SKU-{random.randint(1, 50):04d}"
        self.client.get(
            f"/api/inventory/{sku_id}",
            name="/api/inventory/[sku]"
        )


class SpikeTestUser(HttpUser):
    """User for spike testing."""
    
    wait_time = between(0, 0.01)  # Burst traffic
    
    @task
    def create_order_burst(self):
        """Burst order creation."""
        order_data = {
            "items": [
                {"sku_id": f"SKU-{random.randint(1, 10):04d}", "quantity": 1, "price": 50.00}
            ]
        }
        self.client.post("/api/orders", json=order_data)


# =============================================================================
# CUSTOM METRICS REPORTING
# =============================================================================

@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, **kwargs):
    """Track custom metrics per request."""
    pass


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate test report on completion."""
    stats = environment.stats
    
    print("\n" + "=" * 70)
    print("LOAD TEST RESULTS")
    print("=" * 70)
    
    print(f"\nTotal Requests: {stats.total.num_requests}")
    print(f"Failed Requests: {stats.total.num_failures}")
    print(f"Error Rate: {(stats.total.num_failures / max(stats.total.num_requests, 1)) * 100:.2f}%")
    
    print(f"\nRequests/sec: {stats.total.total_rps:.2f}")
    print(f"Avg Response Time: {stats.total.avg_response_time:.2f}ms")
    print(f"p50 Response Time: {stats.total.get_response_time_percentile(0.50):.2f}ms")
    print(f"p95 Response Time: {stats.total.get_response_time_percentile(0.95):.2f}ms")
    print(f"p99 Response Time: {stats.total.get_response_time_percentile(0.99):.2f}ms")
    
    print("\n" + "=" * 70)
    print("PER-ENDPOINT BREAKDOWN")
    print("=" * 70)
    
    for name, entry in sorted(stats.entries.items()):
        if entry.num_requests > 0:
            print(f"\n{name}:")
            print(f"  Requests: {entry.num_requests}")
            print(f"  Failures: {entry.num_failures}")
            print(f"  Avg: {entry.avg_response_time:.2f}ms")
            print(f"  p95: {entry.get_response_time_percentile(0.95):.2f}ms")
