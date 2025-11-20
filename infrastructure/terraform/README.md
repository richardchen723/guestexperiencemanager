# Terraform Infrastructure for Hostaway Messages

This Terraform configuration creates AWS RDS PostgreSQL and S3 resources optimized for a small application (up to 20 users) with minimal cost.

## Cost Estimate

**Monthly costs (approximate):**
- RDS db.t3.micro: ~$15/month (or free tier if eligible)
- S3 Storage (20GB): ~$0.50/month
- S3 Requests: ~$0.10/month
- **Total: ~$15-20/month** (or less with free tier)

## Prerequisites

1. **AWS CLI configured:**
   ```bash
   aws configure
   ```

2. **Terraform installed:**
   ```bash
   # macOS
   brew install terraform
   
   # Or download from: https://www.terraform.io/downloads
   ```

3. **PostgreSQL client (for database creation):**
   ```bash
   # macOS
   brew install postgresql
   ```

## Quick Start

### 1. Initialize Terraform

```bash
cd infrastructure/terraform
terraform init
```

### 2. Configure Variables

```bash
# Copy example file
cp terraform.tfvars.example terraform.tfvars

# Edit terraform.tfvars and set:
# - db_password (use a strong password!)
# - aws_region (optional, defaults to us-east-1)
```

Or set password via environment variable:
```bash
export TF_VAR_db_password="your-secure-password-here"
```

### 3. Review Plan

```bash
terraform plan
```

This will show what resources will be created. Review carefully!

### 4. Apply Configuration

```bash
terraform apply
```

Type `yes` when prompted. This will:
- Create RDS PostgreSQL instance (~10-15 minutes)
- Create S3 bucket
- Create IAM user with S3 access
- Generate access keys

### 5. Get Connection Information

```bash
# Get all connection info (sensitive)
terraform output -json connection_info > connection_info.json

# Or view individual outputs
terraform output database_urls
terraform output s3_bucket_name
terraform output aws_access_key_id
```

### 6. Add to .env File

Copy the connection strings from the output to your `.env` file:

```bash
# From terraform output
DATABASE_URL=postgresql://...
USERS_DATABASE_URL=postgresql://...
CACHE_DATABASE_URL=postgresql://...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET_NAME=...
AWS_S3_REGION=us-east-1
```

### 7. Verify Setup

```bash
cd ../..  # Back to project root
python3 scripts/test_setup.py
```

## Database Creation

The Terraform configuration includes a provisioner to create the 3 databases, but it requires `psql` to be installed. If the provisioner fails, you can create databases manually:

```bash
# Get RDS endpoint
terraform output rds_endpoint

# Connect and create databases
psql -h <rds-endpoint> -U hostaway_admin -d postgres
# Then run:
CREATE DATABASE hostaway_main;
CREATE DATABASE hostaway_users;
CREATE DATABASE hostaway_ai_cache;
\q
```

## Cost Optimization Features

- **RDS**: Uses `db.t3.micro` (free tier eligible)
- **Storage**: Minimal 20GB with auto-scaling
- **S3**: Standard storage, no versioning by default
- **Monitoring**: Enhanced monitoring disabled
- **Backups**: 7-day retention (minimum)

## Updating Infrastructure

```bash
# Make changes to .tf files
# Review changes
terraform plan

# Apply changes
terraform apply
```

## Destroying Infrastructure

**⚠️ WARNING: This will delete all resources and data!**

```bash
terraform destroy
```

## Troubleshooting

### RDS Creation Fails

- Check AWS account limits (RDS instance limit)
- Verify VPC and subnet configuration
- Check security group rules

### Database Creation Fails

- Ensure `psql` is installed
- Check RDS security group allows your IP
- Verify database password is correct

### S3 Access Denied

- Check IAM user policy
- Verify access keys are correct
- Check bucket name matches

## Variables Reference

See `variables.tf` for all available variables. Key variables:

- `db_instance_class`: RDS instance size (default: `db.t3.micro`)
- `db_allocated_storage`: Initial storage in GB (default: 20)
- `db_max_allocated_storage`: Max auto-scaling storage (default: 100)
- `enable_s3_versioning`: Enable S3 versioning (default: false)

## Security Notes

1. **Never commit `terraform.tfvars`** - it contains sensitive data
2. **Rotate database password** regularly
3. **Restrict RDS security group** to Vercel IPs in production
4. **Use IAM roles** instead of access keys when possible (for EC2/Lambda)
5. **Enable deletion protection** in production (`deletion_protection = true`)

