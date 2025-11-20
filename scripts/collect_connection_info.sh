#!/bin/bash
# Interactive script to collect AWS connection information
# Use this if you created infrastructure manually via AWS Console

set -e

echo "============================================================"
echo "Collecting AWS Connection Information"
echo "============================================================"
echo ""
echo "This script will help you collect connection information"
echo "from your AWS resources."
echo ""

# Collect RDS information
echo "--- RDS PostgreSQL ---"
read -p "RDS Endpoint (e.g., hostaway-db.xxxxx.us-east-1.rds.amazonaws.com): " RDS_ENDPOINT
read -p "Database Username [hostaway_admin]: " DB_USERNAME
DB_USERNAME=${DB_USERNAME:-hostaway_admin}
read -sp "Database Password: " DB_PASSWORD
echo ""
read -p "Database Port [5432]: " RDS_PORT
RDS_PORT=${RDS_PORT:-5432}

# Collect S3 information
echo ""
echo "--- S3 Bucket ---"
read -p "S3 Bucket Name: " S3_BUCKET
read -p "S3 Region [us-east-1]: " S3_REGION
S3_REGION=${S3_REGION:-us-east-1}

# Collect IAM information
echo ""
echo "--- IAM Access Keys ---"
read -p "AWS Access Key ID: " AWS_KEY
read -sp "AWS Secret Access Key: " AWS_SECRET
echo ""

# Generate connection strings
echo ""
echo "============================================================"
echo "Connection Information"
echo "============================================================"
echo ""
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
echo ""

# Save to file
OUTPUT_FILE="connection_info.txt"
cat > "$OUTPUT_FILE" <<EOF
# Database URLs
DATABASE_URL=postgresql://${DB_USERNAME}:${DB_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/hostaway_main
USERS_DATABASE_URL=postgresql://${DB_USERNAME}:${DB_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/hostaway_users
CACHE_DATABASE_URL=postgresql://${DB_USERNAME}:${DB_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/hostaway_ai_cache

# AWS S3 Configuration
AWS_ACCESS_KEY_ID=${AWS_KEY}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET}
AWS_S3_BUCKET_NAME=${S3_BUCKET}
AWS_S3_REGION=${S3_REGION}
CONVERSATIONS_S3_PREFIX=conversations/
EOF

echo "✓ Saved to $OUTPUT_FILE"
echo ""
echo "Next steps:"
echo "1. Copy the above to your .env file"
echo "2. Or run: cat $OUTPUT_FILE >> .env"
echo "3. Run: python3 scripts/test_setup.py"

