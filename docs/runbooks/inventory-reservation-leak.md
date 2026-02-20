# Runbook: Inventory Reservation Leak

## Severity: P2

## Symptoms
- Reserved inventory not being released
- Stock appears unavailable but no orders completed
- `reserved_quantity` growing without corresponding `sold_quantity`
- Customer complaints about "out of stock" for available items

## Dashboard
- Grafana: Inventory Metrics Dashboard
- Prometheus query: `ecommerce_inventory_reservations_total{status="pending"}`

## Diagnosis Steps

### 1. Check reservation expiry worker
```bash
# Check if expiry worker is running
docker logs inventory-service-1 2>&1 | grep -i "expiry worker"
```

### 2. Find stale reservations
```sql
-- Connect to Postgres
SELECT 
    reservation_id,
    order_id,
    sku_id,
    quantity,
    status,
    created_at,
    expires_at
FROM inventory_reservations
WHERE status = 'pending'
AND expires_at < NOW()
ORDER BY created_at ASC
LIMIT 100;
```

### 3. Check Redis reservation keys
```bash
redis-cli KEYS "reservation:*" | head -20
redis-cli TTL "reservation:some-id"
```

### 4. Check for failed payment events
```bash
# Check dead letter queue
kafka-console-consumer --bootstrap-server kafka-1:9092 \
  --topic dead-letter --from-beginning --max-messages 10
```

## Resolution Steps

### Manual release of stale reservations:
```sql
-- Release expired reservations
UPDATE inventory 
SET 
    available_quantity = available_quantity + r.quantity,
    reserved_quantity = reserved_quantity - r.quantity
FROM inventory_reservations r
WHERE inventory.sku_id = r.sku_id
AND r.status = 'pending'
AND r.expires_at < NOW();

-- Update reservation status
UPDATE inventory_reservations
SET status = 'expired', released_at = NOW()
WHERE status = 'pending'
AND expires_at < NOW();
```

### Restart expiry worker:
```bash
docker-compose -f docker-compose.prod.yml restart inventory-service-1 inventory-service-2 inventory-service-3
```

### Verify fix:
```sql
SELECT 
    sku_id,
    available_quantity,
    reserved_quantity,
    sold_quantity
FROM inventory
WHERE reserved_quantity > 0
ORDER BY reserved_quantity DESC;
```

## Prevention
- Monitor `reservation_pending_count` metric
- Alert if pending reservations > 1000
- Regular audit of reservation vs order completion rates

## Root Cause Analysis
After resolution, investigate:
1. Why did expiry worker fail?
2. Were there payment service failures?
3. Is reservation TTL appropriate?

## Escalation
If inventory discrepancy > 5%, escalate to service owner immediately.
