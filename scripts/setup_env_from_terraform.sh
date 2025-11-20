#!/bin/bash
# Complete setup script: Get Terraform outputs and add to .env file

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "============================================================"
echo "Setting up environment variables from Terraform"
echo "============================================================"
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "Creating .env file..."
    touch .env
fi

# Get Terraform outputs
echo "Getting connection information from Terraform..."
ENV_VARS=$(./scripts/get_terraform_outputs.sh 2>&1)

if [ $? -ne 0 ]; then
    echo ""
    echo "⚠ Could not get Terraform outputs automatically."
    echo ""
    echo "Please provide connection information manually:"
    echo ""
    read -p "RDS Endpoint: " RDS_ENDPOINT
    read -p "Database Username [hostaway_admin]: " DB_USERNAME
    DB_USERNAME=${DB_USERNAME:-hostaway_admin}
    read -sp "Database Password: " DB_PASSWORD
    echo ""
    read -p "S3 Bucket Name: " S3_BUCKET
    read -p "S3 Region [us-east-1]: " S3_REGION
    S3_REGION=${S3_REGION:-us-east-1}
    read -p "AWS Access Key ID: " AWS_KEY
    read -sp "AWS Secret Access Key: " AWS_SECRET
    echo ""
    
    RDS_PORT=5432
    ENV_VARS="# Database URLs
DATABASE_URL=postgresql://${DB_USERNAME}:${DB_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/hostaway_main
USERS_DATABASE_URL=postgresql://${DB_USERNAME}:${DB_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/hostaway_users
CACHE_DATABASE_URL=postgresql://${DB_USERNAME}:${DB_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/hostaway_ai_cache

# AWS S3 Configuration
AWS_ACCESS_KEY_ID=${AWS_KEY}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET}
AWS_S3_BUCKET_NAME=${S3_BUCKET}
AWS_S3_REGION=${S3_REGION}
CONVERSATIONS_S3_PREFIX=conversations/"
fi

# Check for existing values and ask to update
echo ""
echo "Current .env file contents (sensitive values masked):"
grep -E "^[A-Z_]+=" .env 2>/dev/null | sed 's/=.*/=***/' || echo "  (empty or no variables found)"
echo ""

read -p "Add/update these variables in .env? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

# Add missing variables or update existing ones
echo ""
echo "Updating .env file..."

# Process each line from ENV_VARS
while IFS= read -r line; do
    if [[ $line =~ ^# ]] || [ -z "$line" ]; then
        # Comment or empty line - add as-is if not exists
        if ! grep -q "^${line}$" .env 2>/dev/null; then
            echo "$line" >> .env
        fi
    elif [[ $line =~ ^([A-Z_]+)=(.*)$ ]]; then
        VAR_NAME="${BASH_REMATCH[1]}"
        VAR_VALUE="${BASH_REMATCH[2]}"
        
        # Remove existing line if present
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "/^${VAR_NAME}=/d" .env
        else
            sed -i "/^${VAR_NAME}=/d" .env
        fi
        
        # Add new line
        echo "${VAR_NAME}=${VAR_VALUE}" >> .env
        echo "  ✓ ${VAR_NAME}"
    fi
done <<< "$ENV_VARS"

# Add other required variables if missing
echo ""
echo "Checking for other required variables..."

# SECRET_KEY
if ! grep -q "^SECRET_KEY=" .env; then
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)
    echo "SECRET_KEY=${SECRET_KEY}" >> .env
    echo "  ✓ SECRET_KEY (generated)"
fi

# Hostaway API (check if exists)
if ! grep -q "^HOSTAWAY_ACCOUNT_ID=" .env; then
    echo "  ⚠ HOSTAWAY_ACCOUNT_ID not set (add manually)"
fi

if ! grep -q "^HOSTAWAY_API_KEY=" .env; then
    echo "  ⚠ HOSTAWAY_API_KEY not set (add manually)"
fi

echo ""
echo "============================================================"
echo "✓ Environment variables updated!"
echo "============================================================"
echo ""
echo "Next steps:"
echo "1. Verify setup: python3 scripts/test_setup.py"
echo "2. Add Hostaway API credentials if not already set"
echo "3. Continue with Vercel deployment"
echo ""

