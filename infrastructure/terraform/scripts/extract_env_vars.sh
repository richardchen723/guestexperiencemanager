#!/bin/bash
# Extract environment variables from Terraform output
# This script formats Terraform outputs for easy copy-paste to .env file

set -e

cd "$(dirname "$0")/.."

echo "# ============================================================"
echo "# Environment Variables for .env file"
echo "# ============================================================"
echo "# Generated from Terraform output"
echo "#"
echo ""

# Get database URLs
echo "# Database URLs"
DB_URLS=$(terraform output -json database_urls 2>/dev/null)
if [ $? -eq 0 ]; then
    echo "DATABASE_URL=$(echo $DB_URLS | python3 -c "import sys, json; print(json.load(sys.stdin)['main'])" 2>/dev/null || echo '')"
    echo "USERS_DATABASE_URL=$(echo $DB_URLS | python3 -c "import sys, json; print(json.load(sys.stdin)['users'])" 2>/dev/null || echo '')"
    echo "CACHE_DATABASE_URL=$(echo $DB_URLS | python3 -c "import sys, json; print(json.load(sys.stdin)['cache'])" 2>/dev/null || echo '')"
else
    echo "# Run 'terraform apply' first to get database URLs"
fi
echo ""

# Get S3 info
echo "# AWS S3 Configuration"
S3_BUCKET=$(terraform output -raw s3_bucket_name 2>/dev/null || echo '')
S3_REGION=$(terraform output -raw s3_bucket_region 2>/dev/null || echo '')
AWS_KEY=$(terraform output -raw aws_access_key_id 2>/dev/null || echo '')
AWS_SECRET=$(terraform output -raw aws_secret_access_key 2>/dev/null || echo '')

if [ -n "$S3_BUCKET" ]; then
    echo "AWS_S3_BUCKET_NAME=$S3_BUCKET"
    echo "AWS_S3_REGION=$S3_REGION"
    echo "CONVERSATIONS_S3_PREFIX=conversations/"
    echo ""
    echo "AWS_ACCESS_KEY_ID=$AWS_KEY"
    echo "AWS_SECRET_ACCESS_KEY=$AWS_SECRET"
else
    echo "# Run 'terraform apply' first to get S3 configuration"
fi
echo ""
echo "# ============================================================"
echo "# Copy the above to your .env file"
echo "# ============================================================"

