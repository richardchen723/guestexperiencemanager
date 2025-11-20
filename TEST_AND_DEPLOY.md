# Test Setup and Deploy to Vercel

## Step 1: Get Connection Information

You have two options:

### Option A: If you used Terraform

```bash
cd infrastructure/terraform
terraform output -json connection_info > connection_info.json
./scripts/extract_env_vars.sh
```

### Option B: If you created resources manually via AWS Console

```bash
# Interactive script to collect info
./scripts/collect_connection_info.sh
```

Or manually collect from AWS Console:
- **RDS**: Go to RDS Console → Your database → Copy endpoint
- **S3**: Go to S3 Console → Your bucket → Copy bucket name
- **IAM**: Go to IAM Console → Users → Your user → Security credentials → Access keys

## Step 2: Add to .env File

Add the connection information to your `.env` file:

```bash
# Option A: Use the setup script
./scripts/setup_env_from_terraform.sh

# Option B: Manual - copy from connection_info.txt
cat connection_info.txt >> .env
```

Required variables:
- `DATABASE_URL`
- `USERS_DATABASE_URL`
- `CACHE_DATABASE_URL`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_S3_BUCKET_NAME`
- `AWS_S3_REGION`
- `SECRET_KEY` (generate if missing)

## Step 3: Test Setup

```bash
python3 scripts/test_setup.py
```

All checks should pass with ✓. If any fail, fix the issues before proceeding.

## Step 4: Create Databases (if not already created)

If the databases don't exist yet:

```bash
# Get RDS endpoint from .env or AWS Console
# Then connect and create:
psql -h <rds-endpoint> -U hostaway_admin -d postgres

# In psql, run:
CREATE DATABASE hostaway_main;
CREATE DATABASE hostaway_users;
CREATE DATABASE hostaway_ai_cache;
\q
```

Or use the script:
```bash
cd infrastructure/terraform
./scripts/create_databases.sh <rds-endpoint> hostaway_admin <password>
```

## Step 5: Test Database Connection

```bash
python3 -c "
from database.models import get_engine, init_models
import os
from dotenv import load_dotenv
load_dotenv()
db_url = os.getenv('DATABASE_URL')
if db_url:
    print('Testing database connection...')
    engine = get_engine(db_url)
    init_models(db_url)
    print('✓ Database connection successful!')
else:
    print('✗ DATABASE_URL not set')
"
```

## Step 6: Test S3 Connection

```bash
python3 -c "
from utils.s3_storage import S3Storage
import os
from dotenv import load_dotenv
load_dotenv()
if os.getenv('AWS_S3_BUCKET_NAME'):
    s3 = S3Storage()
    print(f'✓ S3 connection successful!')
    print(f'  Bucket: {s3.bucket_name}')
    print(f'  Region: {s3.region}')
    files = s3.list_conversations()
    print(f'  Found {len(files)} files in S3')
else:
    print('✗ AWS_S3_BUCKET_NAME not set')
"
```

## Step 7: Prepare for Vercel Deployment

### 7.1 Generate SECRET_KEY (if not already set)

```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
```

Add this to your `.env` file.

### 7.2 Verify All Environment Variables

```bash
python3 scripts/test_setup.py
```

Should show all ✓ checks.

### 7.3 Commit Code (if using git)

```bash
git add .
git commit -m "Ready for Vercel deployment"
git push
```

## Step 8: Deploy to Vercel

### 8.1 Create Vercel Project

1. Go to https://vercel.com/dashboard
2. Click "Add New" → "Project"
3. Import your repository
4. Vercel will auto-detect `vercel.json`

### 8.2 Configure Build Settings

- **Framework Preset**: Other
- **Root Directory**: (leave empty)
- **Build Command**: (leave empty)
- **Output Directory**: (leave empty)
- **Install Command**: `pip install -r requirements.txt -r dashboard/requirements.txt`

### 8.3 Add Environment Variables

In Vercel project settings → Environment Variables, add ALL variables from your `.env`:

**Database:**
- `DATABASE_URL`
- `USERS_DATABASE_URL`
- `CACHE_DATABASE_URL`

**AWS S3:**
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_S3_BUCKET_NAME`
- `AWS_S3_REGION`
- `CONVERSATIONS_S3_PREFIX`

**Hostaway API:**
- `HOSTAWAY_ACCOUNT_ID`
- `HOSTAWAY_API_KEY`

**OpenAI:**
- `OPENAI_API_KEY`

**Flask:**
- `SECRET_KEY`
- `FLASK_DEBUG=False`
- `FLASK_HOST=0.0.0.0`
- `FLASK_PORT=5001`

**Other:**
- `VERBOSE=False`
- `MESSAGE_SYNC_PARALLEL_WORKERS=10`

**Important:** Select all environments (Production, Preview, Development) for each variable.

### 8.4 Deploy

1. Click "Deploy"
2. Monitor deployment logs
3. Wait for completion (~2-5 minutes)

### 8.5 Verify Deployment

1. Visit your Vercel URL
2. Test application:
   - Login/authentication
   - View listings
   - Check sync history
   - Verify data persistence

## Step 9: Post-Deployment Verification

### 9.1 Check Logs

- Go to Vercel Dashboard → Your Project → Functions
- View function logs for any errors

### 9.2 Test Data Persistence

1. Create some test data (trigger a sync)
2. Redeploy the application
3. Verify data still exists

### 9.3 Monitor Performance

- Check function execution times
- Monitor database connections
- Check S3 access patterns

## Troubleshooting

### Database Connection Fails in Vercel

- Verify RDS security group allows connections from anywhere (0.0.0.0/0)
- Check connection string format
- Verify database exists
- Check Vercel function logs

### S3 Access Denied

- Verify IAM user has correct permissions
- Check bucket name matches exactly
- Verify AWS credentials in Vercel

### Function Timeout

- Check function execution time in logs
- Long-running syncs may need to be split
- Consider using background workers

## Next Steps

After successful deployment:
1. Set up monitoring
2. Configure custom domain (optional)
3. Set up automated backups
4. Configure Vercel Cron for scheduled syncs (optional)

