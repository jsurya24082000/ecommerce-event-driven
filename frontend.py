"""
E-commerce Frontend UI
A modern Streamlit interface for the e-commerce backend.
"""

import streamlit as st
import requests
from datetime import datetime

# Configuration
API_BASE = "http://localhost:8000"

# Page config
st.set_page_config(
    page_title="ShopEase - E-commerce",
    page_icon="🛒",
    layout="wide"
)

# Custom CSS
st.markdown("""
<style>
    .product-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 15px;
        padding: 20px;
        margin: 10px 0;
        color: white;
    }
    .price-tag {
        font-size: 24px;
        font-weight: bold;
        color: #00ff88;
    }
    .order-success {
        background: #00c853;
        padding: 20px;
        border-radius: 10px;
        color: white;
    }
    .cart-item {
        background: #f5f5f5;
        padding: 10px;
        border-radius: 8px;
        margin: 5px 0;
    }
</style>
""", unsafe_allow_html=True)

# Session state initialization
if 'token' not in st.session_state:
    st.session_state.token = None
if 'user' not in st.session_state:
    st.session_state.user = None
if 'cart' not in st.session_state:
    st.session_state.cart = []


def api_request(method, endpoint, data=None, auth=False):
    """Make API request."""
    headers = {}
    if auth and st.session_state.token:
        headers['Authorization'] = f'Bearer {st.session_state.token}'
    
    try:
        if method == 'GET':
            resp = requests.get(f"{API_BASE}{endpoint}", headers=headers)
        elif method == 'POST':
            if 'login' in endpoint:
                resp = requests.post(f"{API_BASE}{endpoint}", data=data, headers=headers)
            else:
                resp = requests.post(f"{API_BASE}{endpoint}", json=data, headers=headers)
        
        return resp.json() if resp.status_code < 400 else None, resp.status_code
    except Exception as e:
        return None, 500


def login_page():
    """Login/Register page."""
    st.title("🛒 ShopEase")
    st.subheader("Welcome! Please login or register.")
    
    tab1, tab2 = st.tabs(["Login", "Register"])
    
    with tab1:
        with st.form("login_form"):
            email = st.text_input("Email", placeholder="john@example.com")
            password = st.text_input("Password", type="password")
            submit = st.form_submit_button("Login", use_container_width=True)
            
            if submit:
                data, status = api_request('POST', '/api/v1/users/login', {
                    'username': email,
                    'password': password
                })
                if data and 'access_token' in data:
                    st.session_state.token = data['access_token']
                    # Get user profile
                    user, _ = api_request('GET', '/api/v1/users/me', auth=True)
                    st.session_state.user = user
                    st.success("Login successful!")
                    st.rerun()
                else:
                    st.error("Invalid credentials")
    
    with tab2:
        with st.form("register_form"):
            name = st.text_input("Full Name")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submit = st.form_submit_button("Register", use_container_width=True)
            
            if submit:
                data, status = api_request('POST', '/api/v1/users/register', {
                    'email': email,
                    'password': password,
                    'name': name
                })
                if status == 201:
                    st.success("Registration successful! Please login.")
                else:
                    st.error("Registration failed. Email may already exist.")


def product_catalog():
    """Display product catalog."""
    st.header("🛍️ Products")
    
    # Fetch products
    products, _ = api_request('GET', '/api/v1/products')
    
    if not products:
        st.warning("Unable to load products. Is the API running?")
        return
    
    # Category filter
    categories = list(set(p['category'] for p in products))
    selected_category = st.selectbox("Filter by Category", ["All"] + categories)
    
    if selected_category != "All":
        products = [p for p in products if p['category'] == selected_category]
    
    # Display products in grid
    cols = st.columns(3)
    for i, product in enumerate(products):
        with cols[i % 3]:
            with st.container():
                st.markdown(f"### {product['name']}")
                st.markdown(f"**Category:** {product['category']}")
                st.markdown(f"<span class='price-tag'>${product['price']}</span>", unsafe_allow_html=True)
                st.caption(f"In Stock: {product['stock']}")
                
                col1, col2 = st.columns(2)
                with col1:
                    qty = st.number_input("Qty", min_value=1, max_value=product['stock'], value=1, key=f"qty_{product['id']}")
                with col2:
                    if st.button("Add to Cart", key=f"add_{product['id']}", use_container_width=True):
                        # Check if already in cart
                        existing = next((item for item in st.session_state.cart if item['product_id'] == product['id']), None)
                        if existing:
                            existing['quantity'] += qty
                        else:
                            st.session_state.cart.append({
                                'product_id': product['id'],
                                'product_name': product['name'],
                                'price': product['price'],
                                'quantity': qty
                            })
                        st.success(f"Added {qty}x {product['name']} to cart!")
                
                st.divider()


def shopping_cart():
    """Display shopping cart."""
    st.header("🛒 Shopping Cart")
    
    if not st.session_state.cart:
        st.info("Your cart is empty. Add some products!")
        return
    
    total = 0
    for i, item in enumerate(st.session_state.cart):
        col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
        with col1:
            st.write(f"**{item['product_name']}**")
        with col2:
            st.write(f"${item['price']}")
        with col3:
            st.write(f"x{item['quantity']}")
        with col4:
            subtotal = item['price'] * item['quantity']
            st.write(f"${subtotal:.2f}")
            total += subtotal
        
        if st.button("Remove", key=f"remove_{i}"):
            st.session_state.cart.pop(i)
            st.rerun()
    
    st.divider()
    st.markdown(f"### Total: ${total:.2f}")
    
    # Checkout
    st.subheader("Checkout")
    shipping_address = st.text_area("Shipping Address", placeholder="123 Main St, City, State ZIP")
    
    if st.button("Place Order", type="primary", use_container_width=True):
        if not shipping_address:
            st.error("Please enter a shipping address")
            return
        
        # Create order
        order_data = {
            'items': [{'product_id': item['product_id'], 'quantity': item['quantity']} for item in st.session_state.cart],
            'shipping_address': shipping_address
        }
        
        order, status = api_request('POST', '/api/v1/orders', order_data, auth=True)
        
        if status == 201:
            # Process payment
            payment, pay_status = api_request('POST', '/api/v1/payments', {
                'order_id': order['id'],
                'payment_method': 'credit_card'
            }, auth=True)
            
            if pay_status == 200:
                st.balloons()
                st.success(f"""
                ### ✅ Order Placed Successfully!
                
                **Order ID:** {order['id']}  
                **Total:** ${order['total_amount']}  
                **Payment ID:** {payment['payment_id']}
                
                Thank you for your purchase!
                """)
                st.session_state.cart = []
            else:
                st.error("Payment failed. Please try again.")
        else:
            st.error("Failed to create order. Please try again.")


def order_history():
    """Display order history."""
    st.header("📦 My Orders")
    
    orders, _ = api_request('GET', '/api/v1/orders', auth=True)
    
    if not orders:
        st.info("You haven't placed any orders yet.")
        return
    
    for order in orders:
        with st.expander(f"Order {order['id']} - {order['status'].upper()}"):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**Status:** {order['status']}")
                st.write(f"**Total:** ${order['total_amount']}")
            with col2:
                st.write(f"**Date:** {order['created_at'][:10]}")
            
            if order.get('items'):
                st.write("**Items:**")
                for item in order['items']:
                    st.write(f"- {item.get('product_name', 'Product')} x{item['quantity']}")


def main():
    """Main application."""
    # Sidebar
    with st.sidebar:
        st.title("🛒 ShopEase")
        
        if st.session_state.user:
            st.write(f"Welcome, **{st.session_state.user['name']}**!")
            st.caption(st.session_state.user['email'])
            
            if st.button("Logout", use_container_width=True):
                st.session_state.token = None
                st.session_state.user = None
                st.session_state.cart = []
                st.rerun()
            
            st.divider()
            
            # Cart summary
            cart_count = sum(item['quantity'] for item in st.session_state.cart)
            st.metric("Cart Items", cart_count)
            
            st.divider()
            
            # Navigation
            page = st.radio("Navigate", ["Products", "Cart", "Orders"])
        else:
            page = "Login"
        
        st.divider()
        st.caption("Event-Driven E-commerce Demo")
        st.caption("Built with FastAPI + Streamlit")
    
    # Main content
    if not st.session_state.user:
        login_page()
    elif page == "Products":
        product_catalog()
    elif page == "Cart":
        shopping_cart()
    elif page == "Orders":
        order_history()


if __name__ == "__main__":
    main()
