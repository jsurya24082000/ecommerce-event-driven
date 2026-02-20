# AWS Deployment Script for E-commerce Event-Driven Backend (PowerShell)

$ErrorActionPreference = "Stop"

$AWS_REGION = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
$AWS_ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)
$PROJECT_NAME = "ecommerce-backend"

Write-Host "=============================================="
Write-Host "E-commerce Backend AWS Deployment"
Write-Host "=============================================="
Write-Host "AWS Account: $AWS_ACCOUNT_ID"
Write-Host "Region: $AWS_REGION"
Write-Host ""

# Step 1: Initialize Terraform
Write-Host "Step 1: Initializing Terraform..."
Set-Location infrastructure
terraform init

# Step 2: Plan infrastructure
Write-Host ""
Write-Host "Step 2: Planning infrastructure..."
terraform plan -out=tfplan

# Step 3: Apply infrastructure
Write-Host ""
Write-Host "Step 3: Applying infrastructure (this will take ~10-15 minutes)..."
terraform apply tfplan

# Get outputs
$ALB_DNS = terraform output -raw alb_dns_name
$ECR_REPOS = terraform output -json ecr_repositories | ConvertFrom-Json

Set-Location ..

# Step 4: Login to ECR
Write-Host ""
Write-Host "Step 4: Logging into ECR..."
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

# Step 5: Build and push Docker images
Write-Host ""
Write-Host "Step 5: Building and pushing Docker images..."

$services = @("user-service", "order-service", "inventory-service", "payment-service")

foreach ($service in $services) {
    Write-Host "  Building $service..."
    docker build -t "$PROJECT_NAME/${service}:latest" "./services/$service"
    docker tag "$PROJECT_NAME/${service}:latest" "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$PROJECT_NAME/${service}:latest"
    docker push "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$PROJECT_NAME/${service}:latest"
}

# Step 6: Update ECS services
Write-Host ""
Write-Host "Step 6: Updating ECS services..."
foreach ($service in $services) {
    aws ecs update-service `
        --cluster "$PROJECT_NAME-cluster" `
        --service $service `
        --force-new-deployment `
        --region $AWS_REGION
}

# Step 7: Wait for services to stabilize
Write-Host ""
Write-Host "Step 7: Waiting for services to stabilize..."
foreach ($service in $services) {
    Write-Host "  Waiting for $service..."
    aws ecs wait services-stable `
        --cluster "$PROJECT_NAME-cluster" `
        --services $service `
        --region $AWS_REGION
}

Write-Host ""
Write-Host "=============================================="
Write-Host "DEPLOYMENT COMPLETE!"
Write-Host "=============================================="
Write-Host ""
Write-Host "API Gateway URL: http://$ALB_DNS"
Write-Host ""
Write-Host "Service Endpoints:"
Write-Host "  - Users:     http://$ALB_DNS/api/users/"
Write-Host "  - Orders:    http://$ALB_DNS/api/orders/"
Write-Host "  - Inventory: http://$ALB_DNS/api/inventory/"
Write-Host "  - Payments:  http://$ALB_DNS/api/payments/"
Write-Host ""
Write-Host "Health Check:"
Write-Host "  curl http://$ALB_DNS/health"
