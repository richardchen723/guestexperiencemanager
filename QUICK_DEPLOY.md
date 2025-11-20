# Quick Deploy to Vercel

## Option 1: Using Vercel CLI (Fastest)

### Step 1: Install Vercel CLI (if not installed)

```bash
npm install -g vercel
```

### Step 2: Login to Vercel

```bash
vercel login
```

### Step 3: Add Environment Variables

**IMPORTANT:** Before deploying, add all environment variables in Vercel Dashboard:

1. Go to https://vercel.com/dashboard
2. Create a new project (or select existing)
3. Go to **Settings** → **Environment Variables**
4. Add all variables from `VERCEL_ENV_VARS.md`

Or use the helper script:
```bash
./scripts/prepare_vercel_env.sh
```

### Step 4: Deploy

```bash
# Preview deployment
vercel

# Production deployment
vercel --prod
```

## Option 2: Using Web Interface (Easier for First Time)

### Step 1: Push Code to Git

```bash
git add .
git commit -m "Ready for Vercel deployment"
git push
```

### Step 2: Create Vercel Project

1. Go to https://vercel.com/dashboard
2. Click **"Add New"** → **"Project"**
3. Import your repository (GitHub/GitLab/Bitbucket)
4. Vercel will auto-detect `vercel.json`

### Step 3: Configure Build Settings

- **Framework Preset**: Other
- **Root Directory**: (leave empty)
- **Build Command**: (leave empty)
- **Output Directory**: (leave empty)
- **Install Command**: 
  ```
  pip install -r requirements.txt -r dashboard/requirements.txt
  ```

### Step 4: Add Environment Variables

**CRITICAL:** Add ALL variables before deploying!

Go to **Settings** → **Environment Variables** and add:

**See `VERCEL_ENV_VARS.md` for complete list with values.**

Quick list:
- `DATABASE_URL`
- `USERS_DATABASE_URL`
- `CACHE_DATABASE_URL`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_S3_BUCKET_NAME`
- `AWS_S3_REGION`
- `CONVERSATIONS_S3_PREFIX`
- `SECRET_KEY`
- `OPENAI_API_KEY`
- `VERCEL=1`
- Plus Hostaway API credentials (if you have them)

**For each variable:**
- Select all environments: ✅ Production, ✅ Preview, ✅ Development

### Step 5: Deploy

1. Click **"Deploy"**
2. Wait for deployment (~2-5 minutes)
3. Visit your Vercel URL

### Step 6: Verify

1. Visit your Vercel URL
2. Test login
3. Check sync history
4. Verify database connectivity

## Using the Helper Script

```bash
./scripts/deploy_to_vercel.sh
```

This script will:
- Check if Vercel CLI is installed
- Guide you through installation if needed
- Help you deploy or show environment variables

## Troubleshooting

### "Module not found" errors
- Check that all dependencies are in `requirements.txt` and `dashboard/requirements.txt`
- Verify Python version is 3.11 (set in `vercel.json`)

### Database connection fails
- Verify RDS security group allows connections from anywhere
- Check connection strings in environment variables
- Verify databases exist

### S3 access denied
- Check IAM user permissions
- Verify bucket name matches exactly
- Check AWS credentials

### Function timeout
- Long-running syncs may exceed 60-second limit
- Consider splitting operations or using background workers

## Next Steps After Deployment

1. Test all functionality
2. Monitor logs in Vercel dashboard
3. Set up custom domain (optional)
4. Configure monitoring and alerts

