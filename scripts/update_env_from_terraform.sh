#!/bin/bash
# Extract Terraform outputs and update .env file

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT/infrastructure/terraform"

echo "Waiting for Terraform to complete..."
echo "This may take 10-15 minutes for RDS to be created..."

# Wait for terraform apply to complete
while pgrep -f "terraform apply" > /dev/null; do
    echo "Still creating infrastructure... (this takes ~10-15 minutes)"
    sleep 30
done

echo ""
echo "Checking Terraform outputs..."

# Get outputs
RDS_ENDPOINT=$(terraform output -raw rds_endpoint 2>/dev/null || echo "")
S3_BUCKET=$(terraform output -raw s3_bucket_name 2>/dev/null || echo "")
S3_REGION=$(terraform output -raw s3_bucket_region 2>/dev/null || echo "")
AWS_KEY=$(terraform output -raw aws_access_key_id 2>/dev/null || echo "")
AWS_SECRET=$(terraform output -raw aws_secret_access_key 2>/dev/null || echo "")

if [ -z "$RDS_ENDPOINT" ]; then
    echo "⚠ Terraform outputs not available yet."
    echo "RDS may still be creating. Wait a few more minutes and try again."
    exit 1
fi

# Get password from tfvars
DB_PASSWORD=$(grep "db_password" terraform.tfvars | cut -d'"' -f2)
DB_USERNAME="hostaway_admin"
RDS_PORT=5432

echo "✓ Infrastructure ready!"
echo ""
echo "Extracting connection information..."

# Generate connection strings
cd "$PROJECT_ROOT"

ENV_VARS="# Database URLs (from Terraform)
DATABASE_URL=postgresql://${DB_USERNAME}:${DB_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/hostaway_main
USERS_DATABASE_URL=postgresql://${DB_USERNAME}:${DB_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/hostaway_users
CACHE_DATABASE_URL=postgresql://${DB_USERNAME}:${DB_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/hostaway_ai_cache

# AWS S3 Configuration (from Terraform)
AWS_ACCESS_KEY_ID=${AWS_KEY}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET}
AWS_S3_BUCKET_NAME=${S3_BUCKET}
AWS_S3_REGION=${S3_REGION}
CONVERSATIONS_S3_PREFIX=conversations/
"

# Update .env file
echo "Updating .env file..."

# Remove existing database and S3 vars
if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' '/^DATABASE_URL=/d' .env
    sed -i '' '/^USERS_DATABASE_URL=/d' .env
    sed -i '' '/^CACHE_DATABASE_URL=/d' .env
    sed -i '' '/^AWS_ACCESS_KEY_ID=/d' .env
    sed -i '' '/^AWS_SECRET_ACCESS_KEY=/d' .env
    sed -i '' '/^AWS_S3_BUCKET_NAME=/d' .env
    sed -i '' '/^AWS_S3_REGION=/d' .env
    sed -i '' '/^CONVERSATIONS_S3_PREFIX=/d' .env
else
    sed -i '/^DATABASE_URL=/d' .env
    sed -i '/^USERS_DATABASE_URL=/d' .env
    sed -i '/^CACHE_DATABASE_URL=/d' .env
    sed -i '/^AWS_ACCESS_KEY_ID=/d' .env
    sed -i '/^AWS_SECRET_ACCESS_KEY=/d' .env
    sed -i '/^AWS_S3_BUCKET_NAME=/d' .env
    sed -i '/^AWS_S3_REGION=/d' .env
    sed -i '/^CONVERSATIONS_S3_PREFIX=/d' .env
fi

# Add new vars
echo "$ENV_VARS" >> .env

# Add SECRET_KEY if missing
if ! grep -q "^SECRET_KEY=" .env; then
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)
    echo "SECRET_KEY=${SECRET_KEY}" >> .env
    echo "  ✓ SECRET_KEY (generated)"
fi

echo ""
echo "============================================================"
echo "✓ .env file updated with connection information!"
echo "============================================================"
echo ""
echo "Next steps:"
echo "1. Verify setup: python3 scripts/test_setup.py"
echo "2. Create databases (if not auto-created):"
echo "   cd infrastructure/terraform"
echo "   ./scripts/create_databases.sh ${RDS_ENDPOINT} ${DB_USERNAME} ${DB_PASSWORD}"
echo "3. Continue with Vercel deployment"

