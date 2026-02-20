# Production Deployment Guide

## AWS Infrastructure Overview

This guide deploys the E-commerce Event-Driven Backend to AWS using:

| Component | AWS Service | Specs | Est. Cost/Month |
|-----------|-------------|-------|-----------------|
| **Compute** | ECS Fargate | 4 services × 0.25 vCPU, 512MB | ~$30 |
| **Database** | RDS PostgreSQL | db.t3.micro, 20GB | ~$15 |
| **Cache** | ElastiCache Redis | cache.t3.micro | ~$12 |
| **Load Balancer** | ALB | 1 ALB | ~$20 |
| **Networking** | VPC, NAT Gateway | 1 NAT | ~$35 |
| **Monitoring** | CloudWatch | Logs + Metrics | ~$5 |
| **Total** | | | **~$117/month** |

## Prerequisites

1. **AWS CLI** configured with credentials
2. **Terraform** >= 1.0 installed
3. **Docker** installed (for building images)

## Quick Start

### 1. Configure Variables

```bash
cd infrastructure
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
```

### 2. Deploy Infrastructure

```powershell
# Windows PowerShell
.\scripts\deploy-aws.ps1
```

```bash
# Linux/Mac
chmod +x scripts/deploy-aws.sh
./scripts/deploy-aws.sh
```

### 3. Manual Deployment Steps

If you prefer manual deployment:

```bash
# Initialize Terraform
cd infrastructure
terraform init

# Plan and review
terraform plan -var="db_password=YourSecurePassword123!"

# Apply infrastructure (~10-15 minutes)
terraform apply -var="db_password=YourSecurePassword123!"

# Get outputs
terraform output
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         INTERNET                                 │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Application Load Balancer                     │
│                    (ecommerce-backend-alb)                       │
│                                                                  │
│   /api/users/*  /api/orders/*  /api/inventory/*  /api/payments/* │
└─────────────────────────────┬───────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ECS Fargate Cluster                         │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────┐│
│  │ user-service │ │order-service │ │inventory-svc │ │payment-  ││
│  │   :8001      │ │   :8002      │ │   :8003      │ │svc :8004 ││
│  │ 0.25vCPU     │ │ 0.25vCPU     │ │ 0.25vCPU     │ │0.25vCPU  ││
│  │ 512MB        │ │ 512MB        │ │ 512MB        │ │512MB     ││
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────┘│
└─────────────────────────────┬───────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────────┐
│  RDS PostgreSQL │ │ ElastiCache     │ │ CloudWatch              │
│  db.t3.micro    │ │ Redis           │ │ - Logs                  │
│  20GB gp3       │ │ cache.t3.micro  │ │ - Metrics               │
│                 │ │                 │ │ - Alarms                │
└─────────────────┘ └─────────────────┘ └─────────────────────────┘
```

## Endpoints

After deployment, your services will be available at:

| Service | Endpoint |
|---------|----------|
| API Gateway | `http://<ALB_DNS>/` |
| User Service | `http://<ALB_DNS>/api/users/` |
| Order Service | `http://<ALB_DNS>/api/orders/` |
| Inventory Service | `http://<ALB_DNS>/api/inventory/` |
| Payment Service | `http://<ALB_DNS>/api/payments/` |

## Health Checks

```bash
# Check ALB health
curl http://<ALB_DNS>/health

# Check individual services
curl http://<ALB_DNS>/api/users/health
curl http://<ALB_DNS>/api/orders/health
curl http://<ALB_DNS>/api/inventory/health
curl http://<ALB_DNS>/api/payments/health
```

## Monitoring

### CloudWatch Dashboard

Access the pre-configured dashboard:
```
https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=ecommerce-backend-dashboard
```

### Metrics Tracked

- **ALB**: Request count, response time, 5xx errors
- **ECS**: CPU utilization, memory utilization per service
- **RDS**: CPU, connections, storage
- **Redis**: Cache hits/misses, memory usage

### Alerts

Configured CloudWatch alarms:
- High CPU (>80%) on any ECS service
- High 5xx error rate on ALB
- High CPU on RDS

## Scaling

### Auto Scaling Configuration

Each ECS service has auto-scaling configured:
- **Min**: 1 task
- **Max**: 4 tasks
- **Target**: 70% CPU utilization
- **Scale out cooldown**: 60 seconds
- **Scale in cooldown**: 300 seconds

### Manual Scaling

```bash
# Scale a specific service
aws ecs update-service \
  --cluster ecommerce-backend-cluster \
  --service user-service \
  --desired-count 3
```

## Cleanup

To destroy all resources:

```bash
cd infrastructure
terraform destroy
```

**Warning**: This will delete all data including the RDS database.

## Security Best Practices

1. **Secrets Management**: Use AWS Secrets Manager for sensitive values
2. **Network**: Services run in private subnets, only ALB is public
3. **IAM**: Least privilege roles for ECS tasks
4. **Encryption**: Enable RDS encryption at rest
5. **WAF**: Consider adding AWS WAF to ALB for production

## Troubleshooting

### View ECS Logs

```bash
aws logs tail /ecs/ecommerce-backend --follow
```

### Check Service Status

```bash
aws ecs describe-services \
  --cluster ecommerce-backend-cluster \
  --services user-service order-service inventory-service payment-service
```

### Force New Deployment

```bash
aws ecs update-service \
  --cluster ecommerce-backend-cluster \
  --service user-service \
  --force-new-deployment
```
