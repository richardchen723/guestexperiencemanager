# Vercel Environment Variables Checklist

## ⚠️ CRITICAL: Required for Deployment

The application **requires** these environment variables in Vercel. Without them, deployment will fail with database connection errors.

## Required Environment Variables

### Database URLs (REQUIRED)

```
DATABASE_URL=postgresql://hostaway_admin:your_db_password@hostaway-messages-db.c41c020ya32p.us-east-1.rds.amazonaws.com:5432/hostaway_main
```

```
USERS_DATABASE_URL=postgresql://hostaway_admin:your_db_password@hostaway-messages-db.c41c020ya32p.us-east-1.rds.amazonaws.com:5432/hostaway_users
```

```
CACHE_DATABASE_URL=postgresql://hostaway_admin:your_db_password@hostaway-messages-db.c41c020ya32p.us-east-1.rds.amazonaws.com:5432/hostaway_ai_cache
```

**Get values from:**
- Your local `.env` file
- Terraform outputs: `cd infrastructure/terraform && terraform output database_urls`
- See `GET_ENV_VALUES.md` for more options

### AWS S3 (REQUIRED)

```
AWS_ACCESS_KEY_ID=your_aws_access_key_id_here
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key_here
AWS_S3_BUCKET_NAME=hostaway-messages-conversations-de99bdee
AWS_S3_REGION=us-east-1
CONVERSATIONS_S3_PREFIX=conversations/
```

**Get values from:**
- Your local `.env` file
- Terraform outputs: `cd infrastructure/terraform && terraform output`
- AWS IAM Console (for access keys)

### Flask (REQUIRED)

```
SECRET_KEY=your_secret_key_here
FLASK_DEBUG=False
FLASK_HOST=0.0.0.0
FLASK_PORT=5001
```

**Generate SECRET_KEY:**
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Other Required

```
VERCEL=1
VERBOSE=False
MESSAGE_SYNC_PARALLEL_WORKERS=10
```

### Optional (but recommended)

```
OPENAI_API_KEY=your_openai_api_key_here
HOSTAWAY_ACCOUNT_ID=your_account_id_here
HOSTAWAY_API_KEY=your_api_key_here
```

## How to Add in Vercel

1. Go to **Vercel Dashboard** → Your Project
2. **Settings** → **Environment Variables**
3. For each variable:
   - Click **"Add"**
   - Enter the **Key** (e.g., `DATABASE_URL`)
   - Enter the **Value** (from your `.env` file)
   - Select all environments: ✅ Production, ✅ Preview, ✅ Development
   - Click **"Save"**

## Verification

After adding all variables, redeploy. The error should be resolved.

If you still see database errors, check:
1. All 3 database URLs are set
2. Database passwords are correct
3. RDS security group allows connections
4. Databases exist in RDS

## Quick Script

To see all values from your local `.env`:

```bash
./scripts/prepare_vercel_env.sh
```

Note: Sensitive values will be masked for security.

