# Terraform Infrastructure for Hostaway Messages

This Terraform configuration sets up AWS infrastructure for the Hostaway Messages application, optimized for cost with a small internal application (10-20 users).

## Prerequisites

1. AWS CLI configured with appropriate credentials
2. Terraform >= 1.0 installed
3. Appropriate AWS permissions for:
   - RDS (create instances, security groups, subnet groups)
   - S3 (create buckets, set policies)
   - Secrets Manager (create secrets)
   - IAM (create policies)

## Quick Start

1. **Copy the example variables file:**
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```

2. **Edit `terraform.tfvars` with your configuration:**
   - Set `s3_bucket_name` to a unique bucket name (must be globally unique)
   - Adjust `aws_region` if needed
   - Configure network settings if not using default VPC

3. **Initialize Terraform:**
   ```bash
   terraform init
   ```

4. **Review the plan:**
   ```bash
   terraform plan
   ```

5. **Apply the configuration:**
   ```bash
   terraform apply
   ```

6. **Retrieve database credentials:**
   ```bash
   # Get the secret ARN from outputs
   terraform output database_secret_arn
   
   # Retrieve credentials using AWS CLI
   aws secretsmanager get-secret-value --secret-id <secret-arn> --query SecretString --output text | jq
   ```

## Configuration

### Free Tier Compatible Defaults

**IMPORTANT**: The default configuration uses `db.t4g.micro` to be compatible with AWS Free Tier accounts. Free Tier accounts can only use micro instances (`db.t4g.micro`, `db.t3.micro`, or `db.t2.micro`).

- **RDS Instance**: `db.t4g.micro` (FREE for 12 months, then ~$15/month, ARM-based)
  - 1 GB RAM, 2 vCPU (Free Tier compatible)
  - Single-AZ deployment (saves ~50% vs multi-AZ)
  - 20 GB storage (auto-scales up to 100 GB if needed, FREE for 20GB in first 12 months)
  - 1-day backup retention (Free Tier compatible, increase to 7+ days after upgrading account)
  - Performance Insights disabled (saves ~$7/month)

**After Upgrading Your AWS Account** (for better performance):
- Change `db_instance_class` to `db.t4g.medium` (~$50/month, 4GB RAM, 2 vCPU)
- Increase `db_backup_retention` to 7 days
- Update PostgreSQL parameters in `rds.tf` for 4GB RAM (see comments in file)

- **S3 Storage**:
  - Intelligent-Tiering enabled (automatic cost optimization)
  - Lifecycle policies:
    - Move to Standard-IA after 90 days (50% cost reduction)
    - Move to Glacier after 180 days (80% cost reduction)
  - Server-side encryption (SSE-S3, free)

### Estimated Monthly Costs

**Free Tier (First 12 Months)**:
- **RDS**: FREE
  - Instance: FREE (db.t4g.micro)
  - Storage: FREE (20GB included)
  - Backups: FREE (20GB included)
- **S3**: FREE (5GB included)
- **Total**: FREE for first 12 months

**After Free Tier Expires**:
- **RDS**: ~$17/month
  - Instance: ~$15/month (db.t4g.micro)
  - Storage: ~$2/month (20GB)
  - Backups: ~$0.50/month (1 day retention)

**After Upgrading Account (Recommended for Production)**:
- **RDS**: ~$53/month
  - Instance: ~$50/month (db.t4g.medium, 4GB RAM, better performance)
  - Storage: ~$2/month (20GB)
  - Backups: ~$1/month (7 days retention)

- **S3**: Varies by usage
  - Standard storage: ~$0.023/GB/month
  - Standard-IA: ~$0.0125/GB/month (after 90 days)
  - Glacier: ~$0.004/GB/month (after 180 days)
  - Intelligent-Tiering: ~$0.0025/1000 objects/month
  - Requests: ~$0.005/1000 requests

- **Total**: ~$53-60/month for typical small application usage with good performance

## Outputs

After applying, Terraform outputs:

- `rds_endpoint`: Database connection endpoint
- `rds_port`: Database port (5432)
- `database_name`: Database name
- `database_secret_arn`: ARN of secret containing credentials
- `s3_bucket_name`: S3 bucket name
- `s3_bucket_arn`: S3 bucket ARN
- `s3_bucket_region`: S3 bucket region
- `estimated_monthly_cost`: Cost breakdown

## Database Connection String

After deployment, construct the connection string:

```
postgresql://<username>:<password>@<rds_endpoint>:<rds_port>/<database_name>
```

Credentials are stored in AWS Secrets Manager. Retrieve them using:

```bash
aws secretsmanager get-secret-value --secret-id <secret-arn> --query SecretString --output text
```

## S3 Bucket Structure

The S3 bucket will store files in the following structure:

- `conversations/` - Conversation files
- `photos/listings/` - Listing photos
- `exports/` - Export files

## Security

- RDS security group restricts access to specified CIDR blocks or VPC
- S3 bucket has public access blocked
- Database credentials stored in AWS Secrets Manager
- IAM policy created for application access (attach to application role)

## Scaling

If you need more performance:

1. **Upgrade RDS instance:**
   - Change `db_instance_class` to `db.t3.small` (~$30/month) or larger
   - Apply: `terraform apply`

2. **Enable multi-AZ:**
   - Set `db_multi_az = true` (doubles cost but provides high availability)
   - Apply: `terraform apply`

3. **Enable Performance Insights:**
   - Set `db_performance_insights = true` (adds ~$7/month)
   - Apply: `terraform apply`

## Backup and Disaster Recovery

- **Automated backups**: 7-day retention (configurable)
- **Single-AZ**: No automatic failover (acceptable for internal app)
- **Manual snapshots**: Can be created via AWS Console or CLI
- **S3**: Lifecycle policies automatically archive old files

For production with higher availability requirements, consider:
- Enabling multi-AZ (`db_multi_az = true`)
- Increasing backup retention
- Setting up cross-region backups

## Cost Monitoring

1. **AWS Cost Explorer**: Monitor costs in AWS Console
2. **Billing Alerts**: Set up CloudWatch billing alarms
3. **Cost Allocation Tags**: Resources are tagged for cost tracking

## Troubleshooting

### RDS Connection Issues

- Verify security group allows your IP address
- Check that RDS is in a public subnet if accessing from outside VPC
- Verify credentials from Secrets Manager

### S3 Access Issues

- Verify IAM policy is attached to application role
- Check bucket policies and CORS configuration
- Ensure bucket name is correct

### Terraform State

- State is stored locally by default
- For team collaboration, consider using S3 backend:
  ```hcl
  terraform {
    backend "s3" {
      bucket = "your-terraform-state-bucket"
      key    = "hostaway-infrastructure/terraform.tfstate"
      region = "us-east-1"
    }
  }
  ```

## Cleanup

To destroy all resources:

```bash
terraform destroy
```

**Warning**: This will delete the RDS instance and all data. Ensure you have backups before destroying.

## Additional Resources

- [AWS RDS Pricing](https://aws.amazon.com/rds/postgresql/pricing/)
- [AWS S3 Pricing](https://aws.amazon.com/s3/pricing/)
- [Terraform AWS Provider Documentation](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)


