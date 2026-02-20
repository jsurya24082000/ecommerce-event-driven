# Operational Runbooks

Runbooks for on-call engineers to handle common incidents.

## Runbook Index

| Incident | Runbook | Severity |
|----------|---------|----------|
| Kafka lag high | [kafka-lag-high.md](kafka-lag-high.md) | P2 |
| Payment service down | [payment-service-down.md](payment-service-down.md) | P1 |
| Inventory reservation leak | [inventory-reservation-leak.md](inventory-reservation-leak.md) | P2 |
| DB CPU spikes | [db-cpu-spikes.md](db-cpu-spikes.md) | P2 |
| API Gateway 5xx errors | [api-gateway-errors.md](api-gateway-errors.md) | P1 |
| Redis connection issues | [redis-issues.md](redis-issues.md) | P2 |

## Severity Definitions

- **P1 (Critical)**: Customer-facing outage, revenue impact
- **P2 (High)**: Degraded performance, potential data issues
- **P3 (Medium)**: Non-critical service issues
- **P4 (Low)**: Minor issues, no immediate impact

## Escalation Path

1. On-call engineer (first 15 minutes)
2. Service owner (if not resolved in 15 minutes)
3. Engineering manager (P1 incidents > 30 minutes)
4. VP Engineering (P1 incidents > 1 hour)
