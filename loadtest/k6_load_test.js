/**
 * K6 Load Testing Suite for E-commerce Event-Driven Backend
 * 
 * Targets:
 * - 100 RPS → 1000 RPS sustained throughput
 * - p95 latency < 200ms
 * - Error rate < 1%
 * 
 * Usage:
 *   k6 run k6_load_test.js
 *   k6 run --vus 100 --duration 5m k6_load_test.js
 */

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import { randomString, randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// =============================================================================
// CUSTOM METRICS
// =============================================================================

const orderCreationTime = new Trend('order_creation_time');
const paymentProcessingTime = new Trend('payment_processing_time');
const inventoryCheckTime = new Trend('inventory_check_time');
const orderSuccessRate = new Rate('order_success_rate');
const paymentSuccessRate = new Rate('payment_success_rate');
const errorCount = new Counter('errors');

// =============================================================================
// TEST CONFIGURATION
// =============================================================================

export const options = {
    scenarios: {
        // Ramp-up test: 0 → 100 → 500 → 1000 RPS
        ramp_up: {
            executor: 'ramping-arrival-rate',
            startRate: 10,
            timeUnit: '1s',
            preAllocatedVUs: 100,
            maxVUs: 500,
            stages: [
                { duration: '1m', target: 100 },   // Ramp to 100 RPS
                { duration: '3m', target: 100 },   // Hold at 100 RPS
                { duration: '1m', target: 500 },   // Ramp to 500 RPS
                { duration: '3m', target: 500 },   // Hold at 500 RPS
                { duration: '1m', target: 1000 },  // Ramp to 1000 RPS
                { duration: '2m', target: 1000 },  // Hold at 1000 RPS
                { duration: '1m', target: 0 },     // Ramp down
            ],
        },
        
        // Spike test
        spike: {
            executor: 'ramping-arrival-rate',
            startRate: 50,
            timeUnit: '1s',
            preAllocatedVUs: 200,
            maxVUs: 1000,
            stages: [
                { duration: '30s', target: 50 },
                { duration: '10s', target: 1000 },  // Spike!
                { duration: '1m', target: 1000 },
                { duration: '10s', target: 50 },
                { duration: '30s', target: 50 },
            ],
            startTime: '15m',  // Start after ramp_up
        },
        
        // Soak test (long duration)
        soak: {
            executor: 'constant-arrival-rate',
            rate: 200,
            timeUnit: '1s',
            duration: '30m',
            preAllocatedVUs: 100,
            maxVUs: 300,
            startTime: '20m',
        },
    },
    
    thresholds: {
        http_req_duration: ['p(95)<200', 'p(99)<500'],
        http_req_failed: ['rate<0.01'],
        order_success_rate: ['rate>0.95'],
        payment_success_rate: ['rate>0.95'],
        order_creation_time: ['p(95)<500'],
        payment_processing_time: ['p(95)<1000'],
    },
};

// =============================================================================
// CONFIGURATION
// =============================================================================

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

// =============================================================================
// HELPER FUNCTIONS
// =============================================================================

function generateUser() {
    return {
        email: `user_${randomString(8)}@test.com`,
        password: 'TestPass123!',
        name: `Test User ${randomString(5)}`,
    };
}

function generateOrder() {
    const numItems = randomIntBetween(1, 3);
    const items = [];
    
    for (let i = 0; i < numItems; i++) {
        items.push({
            sku_id: `SKU-${String(randomIntBetween(1, 50)).padStart(4, '0')}`,
            quantity: randomIntBetween(1, 3),
            price: (Math.random() * 90 + 10).toFixed(2),
        });
    }
    
    return {
        items: items,
        shipping_address: {
            street: '123 Test St',
            city: 'Test City',
            zip: '12345',
        },
    };
}

// =============================================================================
// TEST SCENARIOS
// =============================================================================

export default function() {
    let token = null;
    let userId = null;
    let orderId = null;
    
    group('User Registration & Login', function() {
        const user = generateUser();
        
        // Register
        let res = http.post(`${BASE_URL}/api/users/register`, JSON.stringify(user), {
            headers: { 'Content-Type': 'application/json' },
        });
        
        check(res, {
            'registration successful': (r) => r.status === 201,
        });
        
        if (res.status === 201) {
            userId = res.json('user_id');
        }
        
        // Login
        res = http.post(`${BASE_URL}/api/auth/login`, JSON.stringify({
            email: user.email,
            password: user.password,
        }), {
            headers: { 'Content-Type': 'application/json' },
        });
        
        check(res, {
            'login successful': (r) => r.status === 200,
        });
        
        if (res.status === 200) {
            token = res.json('access_token');
        }
    });
    
    const authHeaders = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
    };
    
    group('Browse Products', function() {
        const res = http.get(`${BASE_URL}/api/products`, { headers: authHeaders });
        
        check(res, {
            'products loaded': (r) => r.status === 200,
            'response time OK': (r) => r.timings.duration < 200,
        });
    });
    
    group('Check Inventory', function() {
        const skuId = `SKU-${String(randomIntBetween(1, 50)).padStart(4, '0')}`;
        const start = Date.now();
        
        const res = http.get(`${BASE_URL}/api/inventory/${skuId}`, { headers: authHeaders });
        
        inventoryCheckTime.add(Date.now() - start);
        
        check(res, {
            'inventory check successful': (r) => r.status === 200,
        });
    });
    
    group('Create Order', function() {
        if (!token) return;
        
        const order = generateOrder();
        const start = Date.now();
        
        const res = http.post(`${BASE_URL}/api/orders`, JSON.stringify(order), {
            headers: authHeaders,
        });
        
        orderCreationTime.add(Date.now() - start);
        
        const success = res.status === 201;
        orderSuccessRate.add(success);
        
        check(res, {
            'order created': (r) => r.status === 201 || r.status === 400,
        });
        
        if (res.status === 201) {
            orderId = res.json('order_id');
        } else {
            errorCount.add(1);
        }
    });
    
    group('Process Payment', function() {
        if (!token || !orderId) return;
        
        const payment = {
            order_id: orderId,
            payment_method: 'credit_card',
            card_token: `tok_${randomString(16)}`,
        };
        
        const start = Date.now();
        
        const res = http.post(`${BASE_URL}/api/payments`, JSON.stringify(payment), {
            headers: authHeaders,
        });
        
        paymentProcessingTime.add(Date.now() - start);
        
        const success = res.status === 200 || res.status === 201;
        paymentSuccessRate.add(success);
        
        check(res, {
            'payment processed': (r) => r.status === 200 || r.status === 201 || r.status === 400,
        });
        
        if (!success) {
            errorCount.add(1);
        }
    });
    
    group('Get Order Status', function() {
        if (!token || !orderId) return;
        
        const res = http.get(`${BASE_URL}/api/orders/${orderId}`, { headers: authHeaders });
        
        check(res, {
            'order status retrieved': (r) => r.status === 200,
        });
    });
    
    sleep(randomIntBetween(1, 3) / 10);  // 0.1-0.3s between iterations
}

// =============================================================================
// LIFECYCLE HOOKS
// =============================================================================

export function handleSummary(data) {
    console.log('\n========================================');
    console.log('LOAD TEST RESULTS SUMMARY');
    console.log('========================================\n');
    
    const metrics = data.metrics;
    
    console.log('HTTP Requests:');
    console.log(`  Total: ${metrics.http_reqs.values.count}`);
    console.log(`  Rate: ${metrics.http_reqs.values.rate.toFixed(2)}/s`);
    
    console.log('\nLatency (http_req_duration):');
    console.log(`  Avg: ${metrics.http_req_duration.values.avg.toFixed(2)}ms`);
    console.log(`  p50: ${metrics.http_req_duration.values['p(50)'].toFixed(2)}ms`);
    console.log(`  p95: ${metrics.http_req_duration.values['p(95)'].toFixed(2)}ms`);
    console.log(`  p99: ${metrics.http_req_duration.values['p(99)'].toFixed(2)}ms`);
    
    console.log('\nError Rate:');
    console.log(`  Failed: ${(metrics.http_req_failed.values.rate * 100).toFixed(2)}%`);
    
    console.log('\nBusiness Metrics:');
    if (metrics.order_success_rate) {
        console.log(`  Order Success Rate: ${(metrics.order_success_rate.values.rate * 100).toFixed(2)}%`);
    }
    if (metrics.payment_success_rate) {
        console.log(`  Payment Success Rate: ${(metrics.payment_success_rate.values.rate * 100).toFixed(2)}%`);
    }
    
    console.log('\n========================================\n');
    
    return {
        'loadtest_results.json': JSON.stringify(data, null, 2),
    };
}
