# Vercel Deployment Checklist

## ✅ Pre-Deployment Checklist

- [x] AWS RDS PostgreSQL instance created
- [x] 3 databases created (hostaway_main, hostaway_users, hostaway_ai_cache)
- [x] S3 bucket created and configured
- [x] IAM user with S3 access created
- [x] Environment variables configured in `.env`
- [x] Database connection tested
- [x] S3 connection tested
- [x] `app.py` configured for Vercel (module-level `app` export)
- [x] `vercel.json` configured
- [x] Requirements files ready

## Step 1: Commit Code

```bash
git add .
git commit -m "Ready for Vercel deployment with AWS RDS and S3"
git push
```

## Step 2: Create Vercel Project

1. Go to https://vercel.com/dashboard
2. Click **"Add New"** → **"Project"**
3. Import your repository (GitHub/GitLab/Bitbucket)
4. Vercel will auto-detect `vercel.json`

## Step 3: Configure Build Settings

- **Framework Preset**: Other
- **Root Directory**: (leave empty)
- **Build Command**: (leave empty)
- **Output Directory**: (leave empty)
- **Install Command**: 
  ```
  pip install -r requirements.txt -r dashboard/requirements.txt
  ```

## Step 4: Add Environment Variables

Go to **Project Settings** → **Environment Variables**

**See `VERCEL_ENV_VARS.md` for complete list with values.**

Key variables to add:
- All 3 database URLs
- AWS S3 credentials (5 variables)
- SECRET_KEY
- Hostaway API credentials (if you have)
- OpenAI API key
- Flask settings
- VERCEL=1

**Important:** Select all environments (Production, Preview, Development) for each variable.

## Step 5: Deploy

1. Click **"Deploy"**
2. Monitor deployment logs
3. Wait for completion (~2-5 minutes)

## Step 6: Verify Deployment

1. Visit your Vercel URL
2. Test application:
   - Login/authentication
   - View listings
   - Check sync history
   - Verify data persistence

## Step 7: Post-Deployment Checks

### Check Logs
- Go to Vercel Dashboard → Your Project → Functions
- View function logs for any errors

### Test Data Persistence
1. Create some test data (trigger a sync)
2. Redeploy the application
3. Verify data still exists

### Monitor Performance
- Check function execution times
- Monitor database connections
- Check S3 access patterns

## Troubleshooting

### Database Connection Fails
- Verify RDS security group allows connections from anywhere (0.0.0.0/0)
- Check connection string format in Vercel env vars
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

### Import Errors
- Verify all dependencies in requirements.txt
- Check Python version (3.11 in vercel.json)
- Review build logs for missing packages

## Next Steps

After successful deployment:
1. Set up monitoring
2. Configure custom domain (optional)
3. Set up automated backups for RDS
4. Configure S3 lifecycle policies
5. Set up Vercel Cron for scheduled syncs (optional)

