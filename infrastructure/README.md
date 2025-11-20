# Infrastructure as Code

This directory contains Terraform configurations to automatically create and manage AWS resources for the Hostaway Messages application.

## Quick Start

### Option 1: Automated Setup (Recommended)

```bash
cd infrastructure/terraform
./scripts/setup.sh
```

This script will:
1. Check prerequisites
2. Generate secure database password
3. Initialize Terraform
4. Create all resources
5. Output connection information

### Option 2: Manual Setup

```bash
cd infrastructure/terraform

# 1. Initialize
terraform init

# 2. Configure variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars and set db_password

# 3. Plan
terraform plan

# 4. Apply
terraform apply

# 5. Get outputs
terraform output -json connection_info > connection_info.json
```

## What Gets Created

### RDS PostgreSQL
- **Instance**: `db.t3.micro` (free tier eligible, ~$15/month if not)
- **Storage**: 20GB initial, auto-scales to 100GB
- **Databases**: 3 databases (hostaway_main, hostaway_users, hostaway_ai_cache)
- **Backups**: 7-day retention
- **Public Access**: Enabled (required for Vercel)

### S3 Bucket
- **Bucket**: Unique name with random suffix
- **Encryption**: AES256 server-side encryption
- **Versioning**: Disabled by default (for cost savings)
- **Access**: Private (IAM only)

### IAM User
- **User**: `hostaway-messages-s3-user`
- **Policy**: S3 read/write access to conversations bucket
- **Access Keys**: Generated automatically

## Cost Breakdown

**Monthly estimate for small application (20 users):**

| Resource | Size | Cost |
|----------|------|------|
| RDS db.t3.micro | 20GB storage | $0-15/month* |
| S3 Storage | ~1-5GB | $0.10-0.50/month |
| S3 Requests | Minimal | $0.10/month |
| Data Transfer | Minimal | $0.50/month |
| **Total** | | **~$1-20/month** |

*Free tier: 750 hours/month of db.t2.micro or db.t3.micro for 12 months

## Prerequisites

1. **AWS Account** with appropriate permissions
2. **AWS CLI configured**: `aws configure`
3. **Terraform installed**: `brew install terraform` (macOS)
4. **PostgreSQL client** (for database creation): `brew install postgresql`

## Configuration

### Required Variables

- `db_password`: Database master password (set in `terraform.tfvars`)

### Optional Variables

- `aws_region`: AWS region (default: `us-east-1`)
- `db_instance_class`: RDS instance size (default: `db.t3.micro`)
- `db_allocated_storage`: Initial storage GB (default: 20)
- `enable_s3_versioning`: Enable S3 versioning (default: false)

## Outputs

After `terraform apply`, you'll get:

- RDS endpoint and connection strings
- S3 bucket name and region
- AWS access keys for S3

All outputs are saved to `connection_info.json` (sensitive data).

## Security Best Practices

1. **Never commit `terraform.tfvars`** - contains passwords
2. **Use strong database password** - at least 16 characters
3. **Rotate credentials** regularly
4. **Restrict RDS security group** to specific IPs in production
5. **Enable deletion protection** in production

## Updating Infrastructure

```bash
# Make changes to .tf files
terraform plan    # Review changes
terraform apply   # Apply changes
```

## Destroying Infrastructure

**⚠️ WARNING: This deletes all resources and data!**

```bash
terraform destroy
```

## Troubleshooting

### RDS Creation Takes Too Long

- Normal: RDS takes 10-15 minutes to create
- Check AWS Console for status
- Don't interrupt the process

### Database Creation Fails

- Ensure `psql` is installed
- Check RDS security group allows your IP
- Run manually: `./scripts/create_databases.sh <endpoint> <user> <password>`

### Terraform State Issues

- State is stored locally by default
- For team use, configure S3 backend (see `main.tf` comments)
- Never commit `.tfstate` files

## Next Steps

After infrastructure is created:

1. Copy connection info to `.env` file
2. Run `python3 scripts/test_setup.py` to verify
3. Deploy to Vercel
4. Set environment variables in Vercel dashboard

