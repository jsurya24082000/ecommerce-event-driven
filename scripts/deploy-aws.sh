#!/bin/bash
# AWS Deployment Script for E-commerce Event-Driven Backend

set -e

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
PROJECT_NAME="ecommerce-backend"

echo "=============================================="
echo "E-commerce Backend AWS Deployment"
echo "=============================================="
echo "AWS Account: $AWS_ACCOUNT_ID"
echo "Region: $AWS_REGION"
echo ""

# Step 1: Initialize Terraform
echo "Step 1: Initializing Terraform..."
cd infrastructure
terraform init

# Step 2: Plan infrastructure
echo ""
echo "Step 2: Planning infrastructure..."
terraform plan -out=tfplan

# Step 3: Apply infrastructure
echo ""
echo "Step 3: Applying infrastructure..."
terraform apply tfplan

# Get outputs
ALB_DNS=$(terraform output -raw alb_dns_name)
ECR_USER=$(terraform output -json ecr_repositories | jq -r '."user-service"')
ECR_ORDER=$(terraform output -json ecr_repositories | jq -r '."order-service"')
ECR_INVENTORY=$(terraform output -json ecr_repositories | jq -r '."inventory-service"')
ECR_PAYMENT=$(terraform output -json ecr_repositories | jq -r '."payment-service"')

cd ..

# Step 4: Login to ECR
echo ""
echo "Step 4: Logging into ECR..."
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

# Step 5: Build and push Docker images
echo ""
echo "Step 5: Building and pushing Docker images..."

services=("user-service" "order-service" "inventory-service" "payment-service")
ports=(8001 8002 8003 8004)

for i in "${!services[@]}"; do
    service=${services[$i]}
    echo "  Building $service..."
    docker build -t $PROJECT_NAME/$service:latest ./services/$service
    docker tag $PROJECT_NAME/$service:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$PROJECT_NAME/$service:latest
    docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$PROJECT_NAME/$service:latest
done

# Step 6: Update ECS services
echo ""
echo "Step 6: Updating ECS services..."
for service in "${services[@]}"; do
    aws ecs update-service \
        --cluster $PROJECT_NAME-cluster \
        --service $service \
        --force-new-deployment \
        --region $AWS_REGION
done

# Step 7: Wait for services to stabilize
echo ""
echo "Step 7: Waiting for services to stabilize..."
for service in "${services[@]}"; do
    echo "  Waiting for $service..."
    aws ecs wait services-stable \
        --cluster $PROJECT_NAME-cluster \
        --services $service \
        --region $AWS_REGION
done

echo ""
echo "=============================================="
echo "DEPLOYMENT COMPLETE!"
echo "=============================================="
echo ""
echo "API Gateway URL: http://$ALB_DNS"
echo ""
echo "Service Endpoints:"
echo "  - Users:     http://$ALB_DNS/api/users/"
echo "  - Orders:    http://$ALB_DNS/api/orders/"
echo "  - Inventory: http://$ALB_DNS/api/inventory/"
echo "  - Payments:  http://$ALB_DNS/api/payments/"
echo ""
echo "Health Check:"
echo "  curl http://$ALB_DNS/health"
