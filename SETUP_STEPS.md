# Setup Steps - Quick Reference

Follow these steps in order to set up your application for Vercel deployment.

## Current Status

Run this to check your current setup:
```bash
python3 scripts/test_setup.py
```

## Step 1: AWS RDS PostgreSQL Setup

**Time: 10-15 minutes**

1. **Create RDS Instance**
   - Follow detailed guide: `scripts/step1_rds_setup.md`
   - Or use AWS Console: https://console.aws.amazon.com/rds/
   - Create PostgreSQL instance
   - Note: Enable "Public access" (needed for Vercel)

2. **Create 3 Databases**
   - Connect to RDS (use Query Editor or psql)
   - Run:
     ```sql
     CREATE DATABASE hostaway_main;
     CREATE DATABASE hostaway_users;
     CREATE DATABASE hostaway_ai_cache;
     ```

3. **Generate Connection Strings**
   ```bash
   python3 scripts/generate_connection_strings.py
   ```
   - This will prompt for your RDS details
   - Copy the output connection strings

4. **Add to .env file**
   - Add the 3 connection strings to your `.env` file

## Step 2: AWS S3 Bucket Setup

**Time: 5-10 minutes**

1. **Create S3 Bucket**
   - Go to: https://console.aws.amazon.com/s3/
   - Create bucket (e.g., `hostaway-conversations`)
   - Choose region (same as RDS recommended)
   - Enable versioning (optional but recommended)

2. **Create IAM User**
   - Go to: https://console.aws.amazon.com/iam/
   - Create user: `hostaway-s3-user`
   - Attach policy (see `SETUP_GUIDE.md` for policy JSON)
   - Create access key
   - **Save access key ID and secret** (shown only once!)

3. **Add to .env file**
   ```bash
   AWS_ACCESS_KEY_ID=your_access_key_id
   AWS_SECRET_ACCESS_KEY=your_secret_access_key
   AWS_S3_BUCKET_NAME=your-bucket-name
   AWS_S3_REGION=us-east-1
   CONVERSATIONS_S3_PREFIX=conversations/
   ```

## Step 3: Configure Environment Variables

**Time: 5 minutes**

1. **Add Missing Variables to .env**
   
   Check what's missing:
   ```bash
   python3 scripts/test_setup.py
   ```

   Add to `.env`:
   - Database URLs (from Step 1)
   - AWS S3 credentials (from Step 2)
   - Hostaway API credentials (if not already set)
   - SECRET_KEY (generate with: `python3 -c "import secrets; print(secrets.token_hex(32))"`)

2. **Verify Setup**
   ```bash
   python3 scripts/test_setup.py
   ```
   - Should show all ✓ checks

## Step 4: Test Locally (Optional)

**Time: 5 minutes**

1. **Test Database Connection**
   ```bash
   python3 -c "
   from database.models import get_engine, init_models
   import os
   from dotenv import load_dotenv
   load_dotenv()
   db_url = os.getenv('DATABASE_URL')
   if db_url:
       engine = get_engine(db_url)
       init_models(db_url)
       print('✓ Database connection successful!')
   "
   ```

2. **Test S3 Connection**
   ```bash
   python3 -c "
   from utils.s3_storage import S3Storage
   import os
   from dotenv import load_dotenv
   load_dotenv()
   if os.getenv('AWS_S3_BUCKET_NAME'):
       s3 = S3Storage()
       print(f'✓ S3 connection successful! Bucket: {s3.bucket_name}')
   "
   ```

## Step 5: Migrate Existing Data (If Applicable)

**Time: Varies based on data size**

If you have existing SQLite databases:

1. **Migrate Database**
   ```bash
   python3 scripts/migrate_to_postgres.py \
     --main-db data/database/hostaway.db \
     --users-db dashboard/data/users.db \
     --cache-db dashboard/data/ai_cache.db \
     --main-url "your-main-database-url" \
     --users-url "your-users-database-url" \
     --cache-url "your-cache-database-url"
   ```

2. **Migrate Files to S3**
   ```bash
   python3 scripts/migrate_to_s3.py \
     --conversations-dir ./conversations \
     --update-db
   ```

## Step 6: Deploy to Vercel

**Time: 10-15 minutes**

1. **Push Code to Repository**
   ```bash
   git add .
   git commit -m "Ready for Vercel deployment"
   git push
   ```

2. **Create Vercel Project**
   - Go to: https://vercel.com/dashboard
   - Import your repository
   - Vercel will auto-detect `vercel.json`

3. **Add Environment Variables in Vercel**
   - Go to Project Settings → Environment Variables
   - Add ALL variables from your `.env` file
   - Select environments: Production, Preview, Development

4. **Deploy**
   - Click "Deploy"
   - Monitor deployment logs
   - Wait for completion

5. **Verify**
   - Visit your Vercel URL
   - Test application functionality
   - Check logs for any errors

## Quick Commands Reference

```bash
# Check setup status
python3 scripts/test_setup.py

# Generate connection strings
python3 scripts/generate_connection_strings.py

# Test database connection
python3 -c "from database.models import get_engine, init_models; import os; from dotenv import load_dotenv; load_dotenv(); init_models(os.getenv('DATABASE_URL'))"

# Test S3 connection
python3 -c "from utils.s3_storage import S3Storage; s3 = S3Storage(); print(f'Bucket: {s3.bucket_name}')"

# Generate SECRET_KEY
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## Need Help?

- Detailed RDS setup: `scripts/step1_rds_setup.md`
- Full deployment guide: `VERCEL_DEPLOYMENT.md`
- Complete setup guide: `SETUP_GUIDE.md`

