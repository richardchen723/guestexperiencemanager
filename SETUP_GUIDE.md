# Step-by-Step Setup Guide

This guide will walk you through setting up the Hostaway Messages Dashboard for Vercel deployment with AWS RDS and S3.

## Prerequisites Checklist

Before starting, ensure you have:
- [ ] AWS account with appropriate permissions
- [ ] Vercel account
- [ ] GitHub/GitLab/Bitbucket repository (or ability to push code)
- [ ] Hostaway API credentials
- [ ] OpenAI API key

## Step 1: AWS RDS PostgreSQL Setup

### 1.1 Create RDS Instance

1. Go to AWS RDS Console: https://console.aws.amazon.com/rds/
2. Click "Create database"
3. Choose:
   - **Engine**: PostgreSQL
   - **Version**: PostgreSQL 15.x or 16.x (recommended)
   - **Template**: Free tier (for testing) or Production (for production)
   - **DB instance identifier**: `hostaway-db` (or your preferred name)
   - **Master username**: `hostaway_admin` (or your preferred username)
   - **Master password**: Generate a strong password (save it securely!)
   - **DB instance class**: Choose based on your needs
   - **Storage**: 20 GB minimum (adjust as needed)
   - **VPC**: Use default VPC or create new
   - **Public access**: Yes (needed for Vercel to connect)
   - **Security group**: Create new or use existing

4. Click "Create database"
5. Wait for instance to be available (5-10 minutes)

### 1.2 Configure Security Group

1. Go to EC2 Console → Security Groups
2. Find the security group attached to your RDS instance
3. Click "Edit inbound rules"
4. Add rule:
   - **Type**: PostgreSQL
   - **Protocol**: TCP
   - **Port**: 5432
   - **Source**: `0.0.0.0/0` (for now - we'll restrict later)
   - **Description**: "Allow Vercel connections"

5. Save rules

### 1.3 Create Databases

1. Connect to your RDS instance using a PostgreSQL client (psql, pgAdmin, etc.)
   - **Host**: Your RDS endpoint (e.g., `hostaway-db.xxxxx.us-east-1.rds.amazonaws.com`)
   - **Port**: 5432
   - **Username**: Your master username
   - **Password**: Your master password
   - **Database**: `postgres` (default)

2. Run these SQL commands:
   ```sql
   CREATE DATABASE hostaway_main;
   CREATE DATABASE hostaway_users;
   CREATE DATABASE hostaway_ai_cache;
   ```

3. Verify databases were created:
   ```sql
   \l
   ```

### 1.4 Document Connection Strings

Create connection strings in this format:
```
postgresql://username:password@host:port/database
```

Example:
```
postgresql://hostaway_admin:YourPassword123@hostaway-db.xxxxx.us-east-1.rds.amazonaws.com:5432/hostaway_main
```

Save these for Step 3:
- Main database URL
- Users database URL  
- Cache database URL

## Step 2: AWS S3 Bucket Setup

### 2.1 Create S3 Bucket

1. Go to AWS S3 Console: https://console.aws.amazon.com/s3/
2. Click "Create bucket"
3. Configure:
   - **Bucket name**: `hostaway-conversations` (must be globally unique)
   - **AWS Region**: Choose same region as RDS (e.g., `us-east-1`)
   - **Object Ownership**: ACLs disabled (recommended)
   - **Block Public Access**: Keep all settings enabled (we'll use IAM)
   - **Bucket Versioning**: Enable (optional, recommended)
   - **Default encryption**: Enable (SSE-S3 or SSE-KMS)

4. Click "Create bucket"

### 2.2 Create IAM User for S3 Access

1. Go to IAM Console: https://console.aws.amazon.com/iam/
2. Click "Users" → "Create user"
3. User name: `hostaway-s3-user`
4. Click "Next"
5. **Attach policies directly**:
   - Click "Create policy"
   - Switch to JSON tab
   - Paste this policy (replace `your-bucket-name` with your actual bucket name):
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "s3:GetObject",
           "s3:PutObject",
           "s3:DeleteObject",
           "s3:ListBucket"
         ],
         "Resource": [
           "arn:aws:s3:::your-bucket-name/*",
           "arn:aws:s3:::your-bucket-name"
         ]
       }
     ]
   }
   ```
   - Name: `HostawayS3Access`
   - Create policy
   - Attach this policy to the user

6. Click "Next" → "Create user"

### 2.3 Generate Access Keys

1. Click on the user you just created
2. Go to "Security credentials" tab
3. Click "Create access key"
4. Choose "Application running outside AWS"
5. Click "Next" → "Create access key"
6. **IMPORTANT**: Copy and save:
   - Access key ID
   - Secret access key (only shown once!)

## Step 3: Configure Environment Variables

### 3.1 Local Testing (Optional)

Create a `.env` file in project root for local testing:

```bash
# Database URLs
DATABASE_URL=postgresql://username:password@host:port/hostaway_main
USERS_DATABASE_URL=postgresql://username:password@host:port/hostaway_users
CACHE_DATABASE_URL=postgresql://username:password@host:port/hostaway_ai_cache

# AWS S3
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_access_key
AWS_S3_BUCKET_NAME=hostaway-conversations
AWS_S3_REGION=us-east-1
CONVERSATIONS_S3_PREFIX=conversations/

# Hostaway API
HOSTAWAY_ACCOUNT_ID=your_account_id
HOSTAWAY_API_KEY=your_api_key

# OpenAI
OPENAI_API_KEY=your_openai_api_key

# Flask
SECRET_KEY=your_secret_key_here
FLASK_DEBUG=False

# Other
VERBOSE=False
MESSAGE_SYNC_PARALLEL_WORKERS=10
```

### 3.2 Vercel Environment Variables

1. Go to your Vercel project dashboard
2. Navigate to Settings → Environment Variables
3. Add each variable from the list above
4. For each variable:
   - **Key**: Variable name (e.g., `DATABASE_URL`)
   - **Value**: Variable value
   - **Environment**: Select "Production", "Preview", and "Development" as needed

## Step 4: Test Database Connection Locally

### 4.1 Install Dependencies

```bash
pip install -r requirements.txt
pip install -r dashboard/requirements.txt
```

### 4.2 Test Connection

Create a test script to verify database connection:

```bash
python3 -c "
from database.models import get_engine, init_models
import os
db_url = os.getenv('DATABASE_URL')
if db_url:
    print(f'Testing connection to: {db_url.split(\"@\")[1] if \"@\" in db_url else \"unknown\"}')
    engine = get_engine(db_url)
    init_models(db_url)
    print('✓ Database connection successful!')
else:
    print('DATABASE_URL not set')
"
```

### 4.3 Test S3 Connection

```bash
python3 -c "
from utils.s3_storage import S3Storage
import os
if os.getenv('AWS_S3_BUCKET_NAME'):
    s3 = S3Storage()
    print(f'✓ S3 connection successful! Bucket: {s3.bucket_name}')
    # List existing files (if any)
    files = s3.list_conversations()
    print(f'Found {len(files)} files in S3')
else:
    print('AWS_S3_BUCKET_NAME not set')
"
```

## Step 5: Migrate Existing Data (If Applicable)

### 5.1 Migrate Database

If you have existing SQLite databases:

```bash
python3 scripts/migrate_to_postgres.py \
  --main-db data/database/hostaway.db \
  --users-db dashboard/data/users.db \
  --cache-db dashboard/data/ai_cache.db \
  --main-url "postgresql://user:password@host:port/hostaway_main" \
  --users-url "postgresql://user:password@host:port/hostaway_users" \
  --cache-url "postgresql://user:password@host:port/hostaway_ai_cache"
```

### 5.2 Migrate Files to S3

If you have existing conversation files:

```bash
python3 scripts/migrate_to_s3.py \
  --conversations-dir ./conversations \
  --db-path data/database/hostaway.db \
  --update-db
```

## Step 6: Deploy to Vercel

### 6.1 Prepare Repository

1. Ensure all code is committed:
   ```bash
   git add .
   git commit -m "Prepare for Vercel deployment"
   git push
   ```

### 6.2 Connect to Vercel

1. Go to https://vercel.com/dashboard
2. Click "Add New" → "Project"
3. Import your repository
4. Vercel will detect `vercel.json` automatically

### 6.3 Configure Build Settings

- **Framework Preset**: Other
- **Root Directory**: (leave empty)
- **Build Command**: (leave empty - Vercel handles Python)
- **Output Directory**: (leave empty)
- **Install Command**: `pip install -r requirements.txt -r dashboard/requirements.txt`

### 6.4 Deploy

1. Click "Deploy"
2. Monitor deployment logs
3. Wait for deployment to complete

### 6.5 Verify Deployment

1. Visit your Vercel URL
2. Test application functionality:
   - Login
   - View listings
   - Check sync history
   - Verify data persistence

## Step 7: Post-Deployment Verification

### 7.1 Check Logs

1. Go to Vercel Dashboard → Your Project → Functions
2. View function logs
3. Check for any errors

### 7.2 Test Data Persistence

1. Create some test data (e.g., trigger a sync)
2. Redeploy the application
3. Verify data still exists after redeployment

### 7.3 Monitor Performance

1. Check function execution times
2. Monitor database connections
3. Check S3 access patterns

## Troubleshooting

### Database Connection Fails

- Verify RDS security group allows connections
- Check connection string format
- Verify database exists
- Test connection from local machine first

### S3 Access Denied

- Verify IAM user has correct permissions
- Check bucket name matches exactly
- Verify AWS credentials are correct
- Test S3 access from local machine first

### Function Timeout

- Check function execution time in logs
- Consider splitting long operations
- Increase `maxDuration` in `vercel.json` if needed

## Next Steps

After successful deployment:
1. Set up monitoring and alerts
2. Configure custom domain (optional)
3. Set up automated backups for RDS
4. Configure S3 lifecycle policies
5. Set up Vercel Cron for scheduled syncs (optional)

