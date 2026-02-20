# Runbook: Kafka Consumer Lag High

## Severity: P2

## Symptoms
- Kafka consumer lag > 10,000 messages
- Order processing delays
- Payment confirmations delayed
- Alerts from Prometheus/Grafana

## Dashboard
- Grafana: Kafka Consumer Lag Dashboard
- Prometheus query: `ecommerce_kafka_consumer_lag{consumer_group="order-service-group"}`

## Diagnosis Steps

### 1. Check consumer lag per partition
```bash
kafka-consumer-groups --bootstrap-server kafka-1:9092 \
  --describe --group order-service-group
```

### 2. Check consumer instance health
```bash
# Check if all consumer instances are running
docker ps | grep -E "(order|inventory|payment)-service"

# Check consumer logs
docker logs order-service-1 --tail 100
```

### 3. Check for slow message processing
```bash
# Look for slow processing in logs
docker logs order-service-1 2>&1 | grep -i "processing time"
```

### 4. Check Kafka broker health
```bash
kafka-broker-api-versions --bootstrap-server kafka-1:9092
```

## Resolution Steps

### If consumers are down:
```bash
# Restart consumer instances
docker-compose -f docker-compose.prod.yml restart order-service-1 order-service-2 order-service-3
```

### If processing is slow:
1. Check database query performance
2. Check Redis connection
3. Scale up consumer instances:
```bash
docker-compose -f docker-compose.prod.yml up -d --scale order-service=5
```

### If Kafka broker issues:
1. Check broker logs: `docker logs ecommerce-kafka-1`
2. Check disk space on broker
3. Consider rebalancing partitions

## Prevention
- Set up alerting for lag > 5,000
- Auto-scaling based on lag metrics
- Regular capacity planning reviews

## Escalation
If not resolved in 30 minutes, escalate to service owner.
