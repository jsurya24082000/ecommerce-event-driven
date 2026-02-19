"""
E-commerce Order Flow Demo
Demonstrates the complete order lifecycle.
"""

import requests

BASE = 'http://localhost:8000'

print('=' * 50)
print('E-COMMERCE ORDER FLOW DEMO')
print('=' * 50)

# 1. Register user
print('\n1. REGISTER USER')
resp = requests.post(f'{BASE}/api/v1/users/register', json={
    'email': 'john@example.com',
    'password': 'password123',
    'name': 'John Doe'
})
print(f'   Status: {resp.status_code}')
print(f'   Response: {resp.json()}')

# 2. Login
print('\n2. LOGIN')
resp = requests.post(f'{BASE}/api/v1/users/login', data={
    'username': 'john@example.com',
    'password': 'password123'
})
token = resp.json()['access_token']
print(f'   Token: {token[:20]}...')
headers = {'Authorization': f'Bearer {token}'}

# 3. List products
print('\n3. BROWSE PRODUCTS')
resp = requests.get(f'{BASE}/api/v1/products')
products = resp.json()
for p in products[:3]:
    print(f"   - {p['name']}: ${p['price']} (stock: {p['stock']})")

# 4. Create order
print('\n4. CREATE ORDER')
resp = requests.post(f'{BASE}/api/v1/orders', json={
    'items': [
        {'product_id': 'prod-001', 'quantity': 2},
        {'product_id': 'prod-003', 'quantity': 1}
    ],
    'shipping_address': '123 Main St, New York, NY 10001'
}, headers=headers)
order = resp.json()
print(f"   Order ID: {order['id']}")
print(f"   Total: ${order['total_amount']}")
print(f"   Status: {order['status']}")
print(f"   Items:")
for item in order['items']:
    print(f"      - {item['product_name']} x{item['quantity']} = ${item['subtotal']}")

# 5. Process payment
print('\n5. PROCESS PAYMENT')
resp = requests.post(f'{BASE}/api/v1/payments', json={
    'order_id': order['id'],
    'payment_method': 'credit_card'
}, headers=headers)
print(f'   Status: {resp.status_code}')
print(f'   Response: {resp.json()}')

# 6. Check order status
print('\n6. CHECK ORDER STATUS')
resp = requests.get(f"{BASE}/api/v1/orders/{order['id']}", headers=headers)
updated_order = resp.json()
print(f"   Order Status: {updated_order['status']}")

# 7. Check updated stock
print('\n7. CHECK UPDATED STOCK')
resp = requests.get(f'{BASE}/api/v1/products/prod-001')
product = resp.json()
print(f"   {product['name']}: {product['stock']} remaining (was 100)")

print('\n' + '=' * 50)
print('ORDER FLOW COMPLETE!')
print('=' * 50)
