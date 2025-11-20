#!/bin/bash
# Setup script for Terraform infrastructure

set -e

echo "============================================================"
echo "Hostaway Messages - Infrastructure Setup"
echo "============================================================"
echo ""

# Check prerequisites
echo "Checking prerequisites..."

if ! command -v terraform &> /dev/null; then
    echo "✗ Terraform not found. Install from: https://www.terraform.io/downloads"
    exit 1
fi
echo "✓ Terraform installed"

if ! command -v aws &> /dev/null; then
    echo "✗ AWS CLI not found. Install from: https://aws.amazon.com/cli/"
    exit 1
fi
echo "✓ AWS CLI installed"

# Check AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo "✗ AWS credentials not configured. Run: aws configure"
    exit 1
fi
echo "✓ AWS credentials configured"

# Check if terraform.tfvars exists
if [ ! -f "terraform.tfvars" ]; then
    echo ""
    echo "Creating terraform.tfvars from example..."
    cp terraform.tfvars.example terraform.tfvars
    echo "⚠ Please edit terraform.tfvars and set db_password!"
    echo ""
    read -p "Press Enter after editing terraform.tfvars..."
fi

# Generate password if not set
if ! grep -q "db_password.*=" terraform.tfvars 2>/dev/null || grep -q "your-secure-password-here" terraform.tfvars 2>/dev/null; then
    echo ""
    echo "Generating secure database password..."
    DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null || openssl rand -hex 16)
    echo "Generated password: $DB_PASSWORD"
    echo ""
    echo "⚠ Save this password securely!"
    echo ""
    
    # Update terraform.tfvars
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s/db_password = .*/db_password = \"$DB_PASSWORD\"/" terraform.tfvars
    else
        # Linux
        sed -i "s/db_password = .*/db_password = \"$DB_PASSWORD\"/" terraform.tfvars
    fi
    echo "✓ Password saved to terraform.tfvars"
fi

# Initialize Terraform
echo ""
echo "Initializing Terraform..."
terraform init

# Plan
echo ""
echo "Planning infrastructure changes..."
terraform plan

# Confirm
echo ""
read -p "Do you want to create these resources? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

# Apply
echo ""
echo "Creating infrastructure (this may take 10-15 minutes)..."
terraform apply -auto-approve

# Get outputs
echo ""
echo "============================================================"
echo "Infrastructure Created Successfully!"
echo "============================================================"
echo ""
echo "Connection information:"
terraform output -json connection_info > connection_info.json
echo "✓ Saved to connection_info.json"
echo ""

# Display connection info
echo "Database URLs:"
terraform output database_urls
echo ""
echo "S3 Bucket:"
terraform output s3_bucket_name
echo ""
echo "AWS Access Key ID:"
terraform output aws_access_key_id
echo ""

echo "============================================================"
echo "Next Steps:"
echo "============================================================"
echo "1. Copy connection info from connection_info.json to .env file"
echo "2. Run: python3 scripts/test_setup.py"
echo "3. Deploy to Vercel"
echo ""

