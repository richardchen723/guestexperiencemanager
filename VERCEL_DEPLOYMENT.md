# Vercel Deployment Guide

This guide covers deploying the Hostaway Messages Dashboard to Vercel with AWS RDS PostgreSQL and S3 for persistent storage.

## Prerequisites

- Vercel account
- AWS account with:
  - RDS PostgreSQL instance (or ability to create one)
  - S3 bucket (or ability to create one)
  - IAM user/role with appropriate permissions

## Phase 1: AWS Infrastructure Setup

### 1.1 Create AWS RDS PostgreSQL Instance

1. **Create RDS Instance:**
   - Go to AWS RDS Console
   - Create PostgreSQL instance (recommended: PostgreSQL 15+)
   - Choose instance size based on your needs
   - Note the endpoint, port, username, and password

2. **Create Databases:**
   Connect to your RDS instance and create 3 databases:
   ```sql
   CREATE DATABASE hostaway_main;
   CREATE DATABASE hostaway_users;
   CREATE DATABASE hostaway_ai_cache;
   ```

3. **Configure Security:**
   - Update security group to allow connections from Vercel IP ranges
   - Or use RDS Proxy for better connection management
   - Document connection string format: `postgresql://user:password@host:port/database`

### 1.2 Create S3 Bucket

1. **Create Bucket:**
   - Go to AWS S3 Console
   - Create bucket (e.g., `hostaway-conversations`)
   - Choose region (e.g., `us-east-1`)
   - Configure bucket settings (versioning, encryption, etc.)

2. **Configure IAM:**
   - Create IAM user or use existing role
   - Attach policy with S3 read/write permissions:
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
   - Generate access keys for the IAM user

## Phase 2: Vercel Project Setup

### 2.1 Create Vercel Project

1. **Connect Repository:**
   - Push code to GitHub/GitLab/Bitbucket
   - Import project in Vercel dashboard
   - Vercel will detect `vercel.json` configuration

2. **Configure Build Settings:**
   - Framework Preset: Other
   - Root Directory: (leave empty or set to project root)
   - Build Command: (leave empty - Vercel Python handles this)
   - Output Directory: (leave empty)

### 2.2 Set Environment Variables

In Vercel project settings, add all required environment variables:

**Database URLs:**
```
DATABASE_URL=postgresql://user:password@host:port/hostaway_main
USERS_DATABASE_URL=postgresql://user:password@host:port/hostaway_users
CACHE_DATABASE_URL=postgresql://user:password@host:port/hostaway_ai_cache
```

**AWS S3:**
```
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_access_key
AWS_S3_BUCKET_NAME=your-bucket-name
AWS_S3_REGION=us-east-1
CONVERSATIONS_S3_PREFIX=conversations/
```

**Hostaway API:**
```
HOSTAWAY_ACCOUNT_ID=your_account_id
HOSTAWAY_API_KEY=your_api_key
```

**OpenAI:**
```
OPENAI_API_KEY=your_openai_api_key
```

**Flask:**
```
SECRET_KEY=your_secret_key_here
FLASK_DEBUG=False
FLASK_HOST=0.0.0.0
FLASK_PORT=5001
```

**Google OAuth (optional):**
```
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
```

**Other:**
```
VERBOSE=False
MESSAGE_SYNC_PARALLEL_WORKERS=10
```

## Phase 3: Data Migration

### 3.1 Migrate Database to PostgreSQL

If you have existing SQLite data, migrate it to PostgreSQL:

```bash
python3 scripts/migrate_to_postgres.py \
  --main-db data/database/hostaway.db \
  --users-db dashboard/data/users.db \
  --cache-db dashboard/data/ai_cache.db \
  --main-url postgresql://user:password@host:port/hostaway_main \
  --users-url postgresql://user:password@host:port/hostaway_users \
  --cache-url postgresql://user:password@host:port/hostaway_ai_cache
```

### 3.2 Migrate Files to S3

If you have existing conversation files, upload them to S3:

```bash
python3 scripts/migrate_to_s3.py \
  --conversations-dir ./conversations \
  --db-path data/database/hostaway.db \
  --update-db
```

This will:
- Upload all conversation files to S3
- Update database records with S3 keys instead of local paths

## Phase 4: Deploy to Vercel

### 4.1 Initial Deployment

1. **Push Code:**
   ```bash
   git add .
   git commit -m "Prepare for Vercel deployment"
   git push
   ```

2. **Deploy:**
   - Vercel will automatically detect the push and start deployment
   - Monitor deployment logs in Vercel dashboard

3. **Verify:**
   - Check deployment logs for errors
   - Visit your Vercel URL
   - Test application functionality

### 4.2 Post-Deployment Verification

1. **Check Logs:**
   - View function logs in Vercel dashboard
   - Verify no database connection errors
   - Verify no S3 access errors

2. **Test Functionality:**
   - Login/authentication
   - View listings
   - View sync history
   - Trigger sync operations
   - View conversation files

3. **Verify Data Persistence:**
   - Create test data
   - Redeploy application
   - Verify data still exists after redeployment

## Phase 5: Long-Running Operations

### 5.1 Sync Operations

**Note:** Vercel serverless functions have a maximum execution time (default 10s, can be increased to 60s with Pro plan).

For long-running sync operations, consider:

1. **Vercel Cron Jobs:**
   - Set up scheduled incremental syncs via Vercel Cron
   - Create API endpoint that triggers sync
   - Configure cron schedule (e.g., daily at 2 AM)

2. **Background Workers:**
   - Use separate worker service (e.g., AWS Lambda, Railway, Render)
   - Trigger syncs via API calls from Vercel
   - Or use Vercel Background Functions (if available)

3. **Split Operations:**
   - Break sync into smaller chunks
   - Process in multiple function invocations
   - Use job queue system if needed

### 5.2 Configure Vercel Cron (Optional)

Create `vercel.json` cron configuration:

```json
{
  "crons": [
    {
      "path": "/sync/api/trigger/incremental",
      "schedule": "0 2 * * *"
    }
  ]
}
```

## Troubleshooting

### Database Connection Issues

- **Error: "Connection timeout"**
  - Check RDS security group allows Vercel IPs
  - Verify connection string format
  - Check RDS instance is running

- **Error: "Too many connections"**
  - Use RDS Proxy for connection pooling
  - Reduce `pool_size` in database connection config
  - Check connection limits on RDS instance

### S3 Access Issues

- **Error: "Access Denied"**
  - Verify IAM user has correct permissions
  - Check bucket policy
  - Verify AWS credentials are correct

- **Error: "Bucket not found"**
  - Verify bucket name matches `AWS_S3_BUCKET_NAME`
  - Check bucket region matches `AWS_S3_REGION`

### Function Timeout

- **Error: "Function execution timeout"**
  - Increase `maxDuration` in `vercel.json`
  - Split long operations into smaller chunks
  - Consider using background workers

### Environment Variables

- **Error: "Environment variable not set"**
  - Verify all required variables are set in Vercel
  - Check variable names match exactly
  - Redeploy after adding variables

## Monitoring

1. **Vercel Dashboard:**
   - Monitor function invocations
   - Check error rates
   - View execution times

2. **AWS CloudWatch:**
   - Monitor RDS performance
   - Check S3 access logs
   - Set up alarms for errors

3. **Application Logs:**
   - View logs in Vercel dashboard
   - Logs are written to stdout/stderr
   - Use structured logging for better analysis

## Cost Optimization

1. **RDS:**
   - Use appropriate instance size
   - Enable auto-scaling if needed
   - Use reserved instances for predictable workloads

2. **S3:**
   - Use appropriate storage class (Standard, IA, Glacier)
   - Set up lifecycle policies
   - Enable compression if applicable

3. **Vercel:**
   - Monitor function execution time
   - Optimize code to reduce execution time
   - Use appropriate plan for your needs

## Security Best Practices

1. **Database:**
   - Use strong passwords
   - Enable SSL/TLS connections
   - Restrict access to Vercel IPs only
   - Use RDS Proxy for additional security

2. **S3:**
   - Enable bucket encryption
   - Use IAM roles instead of access keys when possible
   - Enable versioning for important data
   - Set up bucket policies

3. **Environment Variables:**
   - Never commit secrets to git
   - Use Vercel's environment variable encryption
   - Rotate credentials regularly
   - Use different credentials for staging/production

## Rollback Plan

If deployment fails:

1. **Revert Code:**
   - Rollback to previous git commit
   - Vercel will redeploy previous version

2. **Restore Database:**
   - Use RDS snapshots if available
   - Or restore from backup

3. **Restore Files:**
   - Files in S3 are persistent
   - No restoration needed unless deleted

## Next Steps

After successful deployment:

1. Set up monitoring and alerts
2. Configure custom domain (optional)
3. Set up CI/CD pipeline
4. Document runbooks for common issues
5. Plan for scaling as usage grows

