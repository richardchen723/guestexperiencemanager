#!/bin/bash
# Get Terraform outputs and format for .env file

cd "$(dirname "$0")/../infrastructure/terraform"

if [ ! -f "terraform.tfstate" ] && [ ! -f ".terraform/terraform.tfstate" ]; then
    echo "⚠ Terraform state not found. Have you run 'terraform apply'?"
    echo ""
    echo "If you set up infrastructure manually via AWS Console, you can:"
    echo "1. Get RDS endpoint from AWS RDS Console"
    echo "2. Get S3 bucket name from AWS S3 Console"
    echo "3. Get IAM access keys from AWS IAM Console"
    echo ""
    echo "Or run Terraform to create infrastructure:"
    echo "  cd infrastructure/terraform"
    echo "  terraform init"
    echo "  terraform apply"
    exit 1
fi

echo "# ============================================================"
echo "# Environment Variables from Terraform"
echo "# ============================================================"
echo ""

# Try to get outputs
RDS_ENDPOINT=$(terraform output -raw rds_endpoint 2>/dev/null)
S3_BUCKET=$(terraform output -raw s3_bucket_name 2>/dev/null)
S3_REGION=$(terraform output -raw s3_bucket_region 2>/dev/null)
AWS_KEY=$(terraform output -raw aws_access_key_id 2>/dev/null)
AWS_SECRET=$(terraform output -raw aws_secret_access_key 2>/dev/null)

if [ -z "$RDS_ENDPOINT" ]; then
    echo "⚠ Terraform outputs not available."
    echo "Run 'terraform apply' first, or provide connection info manually."
    exit 1
fi

# Get database password from tfvars
DB_PASSWORD=$(grep "db_password" terraform.tfvars 2>/dev/null | cut -d'"' -f2 | cut -d'"' -f1)
DB_USERNAME=$(grep "db_username" terraform.tfvars 2>/dev/null | cut -d'"' -f2 | cut -d'"' -f1 || echo "hostaway_admin")

if [ -z "$DB_PASSWORD" ]; then
    echo "⚠ Could not find db_password in terraform.tfvars"
    echo "Please provide database password manually"
    exit 1
fi

RDS_PORT=5432

echo "# Database URLs"
echo "DATABASE_URL=postgresql://${DB_USERNAME}:${DB_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/hostaway_main"
echo "USERS_DATABASE_URL=postgresql://${DB_USERNAME}:${DB_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/hostaway_users"
echo "CACHE_DATABASE_URL=postgresql://${DB_USERNAME}:${DB_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/hostaway_ai_cache"
echo ""
echo "# AWS S3 Configuration"
echo "AWS_ACCESS_KEY_ID=${AWS_KEY}"
echo "AWS_SECRET_ACCESS_KEY=${AWS_SECRET}"
echo "AWS_S3_BUCKET_NAME=${S3_BUCKET}"
echo "AWS_S3_REGION=${S3_REGION}"
echo "CONVERSATIONS_S3_PREFIX=conversations/"

