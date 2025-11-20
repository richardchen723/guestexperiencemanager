# Quick Start - Infrastructure Setup

This guide will help you set up AWS RDS and S3 using Terraform in about 15 minutes.

## Prerequisites Check

Run these commands to check if you have everything:

```bash
# Check Terraform
terraform version

# Check AWS CLI
aws --version

# Check AWS credentials
aws sts get-caller-identity
```

If any are missing, install them:

```bash
# Install Terraform (macOS)
brew install terraform

# Install AWS CLI (macOS)
brew install awscli

# Configure AWS credentials
aws configure
# Enter your AWS Access Key ID, Secret Access Key, region (e.g., us-east-1), and output format (json)
```

## Step 1: Configure AWS Credentials

If you haven't already:

```bash
aws configure
```

You'll need:
- AWS Access Key ID
- AWS Secret Access Key  
- Default region (e.g., `us-east-1`)
- Default output format (e.g., `json`)

Get your access keys from: https://console.aws.amazon.com/iam/ (Users → Security credentials → Create access key)

## Step 2: Run Automated Setup

The easiest way is to use the automated setup script:

```bash
cd infrastructure/terraform
./scripts/setup.sh
```

This script will:
1. Check prerequisites
2. Generate a secure database password
3. Initialize Terraform
4. Create all AWS resources
5. Output connection information

**Time: ~15 minutes** (most time is waiting for RDS to be created)

## Step 3: Alternative - Manual Setup

If you prefer manual control:

```bash
cd infrastructure/terraform

# 1. Copy and edit configuration
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars and set db_password

# 2. Initialize Terraform
terraform init

# 3. Review what will be created
terraform plan

# 4. Create resources
terraform apply
# Type 'yes' when prompted

# 5. Get connection information
terraform output -json connection_info > connection_info.json
```

## Step 4: Add to .env File

After Terraform completes, you'll get connection strings. Add them to your `.env` file:

**Option A: Use the extraction script**
```bash
cd infrastructure/terraform
./scripts/extract_env_vars.sh >> ../../.env
```

**Option B: Manual copy**
```bash
# View outputs
terraform output

# Copy the values to .env file:
# - DATABASE_URL
# - USERS_DATABASE_URL  
# - CACHE_DATABASE_URL
# - AWS_ACCESS_KEY_ID
# - AWS_SECRET_ACCESS_KEY
# - AWS_S3_BUCKET_NAME
# - AWS_S3_REGION
```

## Step 5: Verify Setup

```bash
cd ../..  # Back to project root
python3 scripts/test_setup.py
```

All checks should pass with ✓

## Step 6: Create Databases (if needed)

If the automated database creation didn't work, create them manually:

```bash
cd infrastructure/terraform

# Get RDS endpoint
terraform output rds_endpoint

# Create databases (replace with your values)
./scripts/create_databases.sh <rds-endpoint> hostaway_admin <password>
```

Or connect manually:
```bash
psql -h <rds-endpoint> -U hostaway_admin -d postgres
# Then run:
CREATE DATABASE hostaway_main;
CREATE DATABASE hostaway_users;
CREATE DATABASE hostaway_ai_cache;
\q
```

## What Gets Created

- **RDS PostgreSQL**: `db.t3.micro` instance (free tier eligible)
- **3 Databases**: hostaway_main, hostaway_users, hostaway_ai_cache
- **S3 Bucket**: For conversation file storage
- **IAM User**: With S3 access permissions
- **Security Groups**: Configured for RDS access

## Cost Estimate

- **RDS db.t3.micro**: $0-15/month (free tier: 750 hours/month for 12 months)
- **S3 Storage**: ~$0.50/month (for small usage)
- **Total**: ~$1-20/month depending on free tier eligibility

## Troubleshooting

### "Terraform not found"
```bash
brew install terraform
```

### "AWS credentials not configured"
```bash
aws configure
```

### "Error creating RDS"
- Check AWS account limits
- Verify you have RDS permissions
- Check if you're in the right region

### "Database creation failed"
- Ensure `psql` is installed: `brew install postgresql`
- Or create databases manually using the script

### "Access denied to S3"
- Check IAM user policy was created correctly
- Verify access keys are correct
- Check bucket name matches

## Next Steps

After infrastructure is set up:

1. ✅ Verify with `python3 scripts/test_setup.py`
2. ✅ Add environment variables to Vercel
3. ✅ Deploy to Vercel
4. ✅ Test application

## Cleanup (if needed)

To destroy all resources (⚠️ deletes everything):

```bash
cd infrastructure/terraform
terraform destroy
```
