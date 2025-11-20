# How to Get Environment Variable Values

Since secrets have been removed from documentation for security, here's how to get the actual values:

## For Local Development

All values are in your `.env` file (which is gitignored and not committed).

## For Vercel Deployment

### Option 1: From Your Local .env File

```bash
# View all environment variables (from your local .env)
cat .env

# Or use the helper script (masks sensitive values)
./scripts/prepare_vercel_env.sh
```

### Option 2: From Terraform Outputs

If you used Terraform to create infrastructure:

```bash
cd infrastructure/terraform
terraform output -json connection_info
```

This will show:
- Database URLs
- S3 bucket name
- AWS access keys

### Option 3: From AWS Console

**AWS Access Keys:**
1. Go to AWS IAM Console
2. Users → `hostaway-messages-s3-user`
3. Security credentials tab
4. Access keys section

**S3 Bucket Name:**
1. Go to AWS S3 Console
2. Find bucket: `hostaway-messages-conversations-*`

**RDS Endpoint:**
1. Go to AWS RDS Console
2. Select database: `hostaway-messages-db`
3. Copy endpoint from Connectivity & security

### Option 4: Generate New Values

**SECRET_KEY:**
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

**Database Password:**
- Check `infrastructure/terraform/terraform.tfvars` (gitignored)
- Or reset in AWS RDS Console

## Quick Reference

When adding to Vercel, you need:

1. **Database URLs** - From `.env` or Terraform outputs
2. **AWS Credentials** - From `.env`, Terraform outputs, or AWS IAM Console
3. **OpenAI API Key** - From your `.env` file
4. **SECRET_KEY** - Generate new or use from `.env`
5. **Hostaway API** - From your `.env` file (if you have it)

## Security Note

Never commit actual secrets to git. The `.env` file is gitignored, and all documentation now uses placeholders.

