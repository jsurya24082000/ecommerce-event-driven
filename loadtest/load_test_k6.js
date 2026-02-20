/**
 * K6 Load Test for E-commerce Event-Driven Backend
 * 
 * Tests:
 * - 100 → 1000 RPS ramp
 * - p50/p95/p99 latency
 * - Error rate
 * - Order creation throughput
 * - Payment processing latency
 * 
 * Run: k6 run load_test_k6.js
 * Run with output: k6 run --out json=results.json load_test_k6.js
 */

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Counter, Trend, Rate, Gauge } from 'k6/metrics';
import { randomIntBetween, randomString } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// =============================================================================
// CUSTOM METRICS
// =============================================================================

// Latency metrics
const orderCreationLatency = new Trend('order_creation_latency_ms');
const paymentProcessingLatency = new Trend('payment_processing_latency_ms');
const inventoryCheckLatency = new Trend('inventory_check_latency_ms');
const e2eOrderLatency = new Trend('e2e_order_latency_ms');

// Error metrics
const orderErrors = new Counter('order_errors_total');
const paymentErrors = new Counter('payment_errors_total');
const inventoryErrors = new Counter('inventory_errors_total');

// Rate metrics
const orderSuccessRate = new Rate('order_success_rate');
const paymentSuccessRate = new Rate('payment_success_rate');

// Throughput
const ordersCreated = new Counter('orders_created_total');
const paymentsProcessed = new Counter('payments_processed_total');

// =============================================================================
// CONFIGURATION
// =============================================================================

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

// Test scenarios
export const options = {
    scenarios: {
        // Scenario 1: Ramp from 100 to 1000 RPS
        ramp_up: {
            executor: 'ramping-arrival-rate',
            startRate: 100,
            timeUnit: '1s',
            preAllocatedVUs: 200,
            maxVUs: 2000,
            stages: [
                { duration: '30s', target: 100 },   // Warm up at 100 RPS
                { duration: '1m', target: 250 },    // Ramp to 250 RPS
                { duration: '1m', target: 500 },    // Ramp to 500 RPS
                { duration: '1m', target: 750 },    // Ramp to 750 RPS
                { duration: '2m', target: 1000 },   // Peak at 1000 RPS
                { duration: '1m', target: 500 },    // Scale down
                { duration: '30s', target: 100 },   // Cool down
            ],
        },
        
        // Scenario 2: Sustained load
        sustained: {
            executor: 'constant-arrival-rate',
            rate: 500,
            timeUnit: '1s',
            duration: '5m',
            preAllocatedVUs: 100,
            maxVUs: 500,
            startTime: '8m', // Start after ramp_up
        },
        
        // Scenario 3: Spike test
        spike: {
            executor: 'ramping-arrival-rate',
            startRate: 100,
            timeUnit: '1s',
            preAllocatedVUs: 500,
            maxVUs: 3000,
            stages: [
                { duration: '10s', target: 100 },
                { duration: '5s', target: 2000 },   // Sudden spike
                { duration: '30s', target: 2000 },  // Hold spike
                { duration: '10s', target: 100 },   // Drop
            ],
            startTime: '14m', // Start after sustained
        },
    },
    
    // Thresholds - SLA requirements
    thresholds: {
        // HTTP latency thresholds
        'http_req_duration': ['p(50)<100', 'p(95)<500', 'p(99)<1000'],
        
        // Custom metric thresholds
        'order_creation_latency_ms': ['p(50)<150', 'p(95)<500', 'p(99)<1000'],
        'payment_processing_latency_ms': ['p(50)<200', 'p(95)<750', 'p(99)<1500'],
        'e2e_order_latency_ms': ['p(50)<500', 'p(95)<2000', 'p(99)<5000'],
        
        // Error rate thresholds
        'order_success_rate': ['rate>0.99'],      // 99% success rate
        'payment_success_rate': ['rate>0.995'],   // 99.5% success rate
        'http_req_failed': ['rate<0.01'],         // <1% error rate
    },
};

// =============================================================================
// TEST DATA GENERATORS
// =============================================================================

function generateUser() {
    return {
        email: `user_${randomString(8)}@test.com`,
        name: `Test User ${randomIntBetween(1, 10000)}`,
        password: 'testpassword123',
    };
}

function generateOrder(userId) {
    const items = [];
    const numItems = randomIntBetween(1, 5);
    
    for (let i = 0; i < numItems; i++) {
        items.push({
            sku_id: `SKU-${randomIntBetween(1, 100)}`,
            quantity: randomIntBetween(1, 3),
            price_cents: randomIntBetween(1000, 50000),
        });
    }
    
    return {
        user_id: userId,
        items: items,
        shipping_address: {
            street: `${randomIntBetween(1, 9999)} Test Street`,
            city: 'Test City',
            state: 'TS',
            zip: `${randomIntBetween(10000, 99999)}`,
        },
    };
}

function generatePayment(orderId, amount) {
    return {
        order_id: orderId,
        amount_cents: amount,
        payment_method: ['credit_card', 'debit_card', 'paypal'][randomIntBetween(0, 2)],
        card_token: `tok_${randomString(16)}`,
    };
}

// =============================================================================
// API CALLS
// =============================================================================

function createUser() {
    const payload = JSON.stringify(generateUser());
    const params = {
        headers: { 'Content-Type': 'application/json' },
        tags: { name: 'CreateUser' },
    };
    
    const res = http.post(`${BASE_URL}/api/users`, payload, params);
    
    check(res, {
        'user created': (r) => r.status === 201 || r.status === 200,
    });
    
    if (res.status === 201 || res.status === 200) {
        return JSON.parse(res.body).user_id || JSON.parse(res.body).id;
    }
    return null;
}

function createOrder(userId) {
    const startTime = Date.now();
    const payload = JSON.stringify(generateOrder(userId));
    const params = {
        headers: { 'Content-Type': 'application/json' },
        tags: { name: 'CreateOrder' },
    };
    
    const res = http.post(`${BASE_URL}/api/orders`, payload, params);
    const latency = Date.now() - startTime;
    
    orderCreationLatency.add(latency);
    
    const success = res.status === 201 || res.status === 200;
    orderSuccessRate.add(success);
    
    if (success) {
        ordersCreated.add(1);
        const body = JSON.parse(res.body);
        return {
            order_id: body.order_id || body.id,
            total_amount: body.total_amount || body.total_cents || 10000,
        };
    } else {
        orderErrors.add(1);
        return null;
    }
}

function processPayment(orderId, amount) {
    const startTime = Date.now();
    const payload = JSON.stringify(generatePayment(orderId, amount));
    const params = {
        headers: { 'Content-Type': 'application/json' },
        tags: { name: 'ProcessPayment' },
    };
    
    const res = http.post(`${BASE_URL}/api/payments`, payload, params);
    const latency = Date.now() - startTime;
    
    paymentProcessingLatency.add(latency);
    
    const success = res.status === 201 || res.status === 200;
    paymentSuccessRate.add(success);
    
    if (success) {
        paymentsProcessed.add(1);
        return JSON.parse(res.body);
    } else {
        paymentErrors.add(1);
        return null;
    }
}

function checkInventory(skuId) {
    const startTime = Date.now();
    const params = {
        tags: { name: 'CheckInventory' },
    };
    
    const res = http.get(`${BASE_URL}/api/inventory/${skuId}`, params);
    const latency = Date.now() - startTime;
    
    inventoryCheckLatency.add(latency);
    
    if (res.status !== 200) {
        inventoryErrors.add(1);
    }
    
    return res.status === 200;
}

function getOrderStatus(orderId) {
    const params = {
        tags: { name: 'GetOrderStatus' },
    };
    
    const res = http.get(`${BASE_URL}/api/orders/${orderId}`, params);
    
    if (res.status === 200) {
        return JSON.parse(res.body).status;
    }
    return null;
}

function healthCheck() {
    const res = http.get(`${BASE_URL}/health`);
    return res.status === 200;
}

// =============================================================================
// TEST SCENARIOS
// =============================================================================

export default function() {
    group('Full Order Flow', function() {
        const e2eStart = Date.now();
        
        // Step 1: Create user (or use existing)
        const userId = `user_${randomIntBetween(1, 1000)}`;
        
        // Step 2: Check inventory
        const skuId = `SKU-${randomIntBetween(1, 100)}`;
        checkInventory(skuId);
        
        // Step 3: Create order
        const order = createOrder(userId);
        
        if (order) {
            // Step 4: Process payment
            const payment = processPayment(order.order_id, order.total_amount);
            
            if (payment) {
                // Step 5: Verify order status (poll)
                let status = null;
                let attempts = 0;
                const maxAttempts = 5;
                
                while (attempts < maxAttempts && status !== 'confirmed') {
                    sleep(0.5);
                    status = getOrderStatus(order.order_id);
                    attempts++;
                }
                
                // Record E2E latency
                const e2eLatency = Date.now() - e2eStart;
                e2eOrderLatency.add(e2eLatency);
            }
        }
    });
    
    // Small sleep to prevent overwhelming
    sleep(0.1);
}

// =============================================================================
// SETUP AND TEARDOWN
// =============================================================================

export function setup() {
    // Verify service is healthy
    const healthy = healthCheck();
    if (!healthy) {
        console.error('Service is not healthy!');
    }
    
    return { startTime: Date.now() };
}

export function teardown(data) {
    const duration = (Date.now() - data.startTime) / 1000;
    console.log(`Test completed in ${duration.toFixed(2)} seconds`);
}

// =============================================================================
// CUSTOM SUMMARY
// =============================================================================

export function handleSummary(data) {
    const summary = {
        timestamp: new Date().toISOString(),
        duration_seconds: data.state.testRunDurationMs / 1000,
        
        // Request metrics
        total_requests: data.metrics.http_reqs ? data.metrics.http_reqs.values.count : 0,
        rps: data.metrics.http_reqs ? data.metrics.http_reqs.values.rate : 0,
        
        // Latency metrics
        http_latency: {
            p50: data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(50)'] : 0,
            p95: data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(95)'] : 0,
            p99: data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(99)'] : 0,
            avg: data.metrics.http_req_duration ? data.metrics.http_req_duration.values.avg : 0,
        },
        
        order_latency: {
            p50: data.metrics.order_creation_latency_ms ? data.metrics.order_creation_latency_ms.values['p(50)'] : 0,
            p95: data.metrics.order_creation_latency_ms ? data.metrics.order_creation_latency_ms.values['p(95)'] : 0,
            p99: data.metrics.order_creation_latency_ms ? data.metrics.order_creation_latency_ms.values['p(99)'] : 0,
        },
        
        e2e_latency: {
            p50: data.metrics.e2e_order_latency_ms ? data.metrics.e2e_order_latency_ms.values['p(50)'] : 0,
            p95: data.metrics.e2e_order_latency_ms ? data.metrics.e2e_order_latency_ms.values['p(95)'] : 0,
            p99: data.metrics.e2e_order_latency_ms ? data.metrics.e2e_order_latency_ms.values['p(99)'] : 0,
        },
        
        // Error metrics
        error_rate: data.metrics.http_req_failed ? data.metrics.http_req_failed.values.rate : 0,
        order_success_rate: data.metrics.order_success_rate ? data.metrics.order_success_rate.values.rate : 0,
        
        // Throughput
        orders_created: data.metrics.orders_created_total ? data.metrics.orders_created_total.values.count : 0,
        payments_processed: data.metrics.payments_processed_total ? data.metrics.payments_processed_total.values.count : 0,
        
        // Threshold results
        thresholds_passed: Object.values(data.root_group.checks || {}).every(c => c.passes > 0),
    };
    
    return {
        'stdout': textSummary(data, { indent: ' ', enableColors: true }),
        'results/load_test_summary.json': JSON.stringify(summary, null, 2),
    };
}

function textSummary(data, options) {
    let output = '\n';
    output += '='.repeat(60) + '\n';
    output += '  E-COMMERCE LOAD TEST RESULTS\n';
    output += '='.repeat(60) + '\n\n';
    
    output += `Duration: ${(data.state.testRunDurationMs / 1000).toFixed(2)}s\n`;
    output += `Total Requests: ${data.metrics.http_reqs ? data.metrics.http_reqs.values.count : 0}\n`;
    output += `RPS (avg): ${data.metrics.http_reqs ? data.metrics.http_reqs.values.rate.toFixed(2) : 0}\n\n`;
    
    output += 'HTTP Latency:\n';
    if (data.metrics.http_req_duration) {
        output += `  p50: ${data.metrics.http_req_duration.values['p(50)'].toFixed(2)}ms\n`;
        output += `  p95: ${data.metrics.http_req_duration.values['p(95)'].toFixed(2)}ms\n`;
        output += `  p99: ${data.metrics.http_req_duration.values['p(99)'].toFixed(2)}ms\n`;
    }
    
    output += '\nError Rate: ';
    output += `${((data.metrics.http_req_failed ? data.metrics.http_req_failed.values.rate : 0) * 100).toFixed(2)}%\n`;
    
    output += '\n' + '='.repeat(60) + '\n';
    
    return output;
}
