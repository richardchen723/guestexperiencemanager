#!/bin/bash
# Check if all prerequisites are installed

echo "Checking prerequisites..."
echo ""

MISSING=0

# Check Terraform
if command -v terraform &> /dev/null; then
    TERRAFORM_VERSION=$(terraform version -json 2>/dev/null | grep -o '"terraform_version":"[^"]*' | cut -d'"' -f4 || terraform version | head -1)
    echo "✓ Terraform: $TERRAFORM_VERSION"
else
    echo "✗ Terraform not found"
    echo "  Install: brew install terraform"
    echo "  Or: https://www.terraform.io/downloads"
    MISSING=1
fi

# Check AWS CLI
if command -v aws &> /dev/null; then
    AWS_VERSION=$(aws --version 2>&1 | head -1)
    echo "✓ AWS CLI: $AWS_VERSION"
else
    echo "✗ AWS CLI not found"
    echo "  Install: brew install awscli"
    echo "  Or: https://aws.amazon.com/cli/"
    MISSING=1
fi

# Check AWS credentials
if aws sts get-caller-identity &> /dev/null; then
    AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
    AWS_USER=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null | cut -d'/' -f2)
    echo "✓ AWS credentials configured"
    echo "  Account: $AWS_ACCOUNT"
    echo "  User: $AWS_USER"
else
    echo "✗ AWS credentials not configured"
    echo "  Run: aws configure"
    MISSING=1
fi

# Check PostgreSQL client
if command -v psql &> /dev/null; then
    PSQL_VERSION=$(psql --version | head -1)
    echo "✓ PostgreSQL client: $PSQL_VERSION"
else
    echo "⚠ PostgreSQL client (psql) not found"
    echo "  Install: brew install postgresql"
    echo "  (Optional - databases can be created manually)"
fi

# Check Python
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    echo "✓ Python: $PYTHON_VERSION"
else
    echo "✗ Python 3 not found"
    MISSING=1
fi

echo ""

if [ $MISSING -eq 0 ]; then
    echo "✓ All prerequisites met!"
    exit 0
else
    echo "✗ Some prerequisites are missing. Please install them first."
    exit 1
fi

