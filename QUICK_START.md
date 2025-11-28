# Quick Start: Deploy to GitHub and Vercel

## 1. Create GitHub Repository

```bash
# Create a new repository on GitHub (don't initialize with README)
# Then run:
git remote add origin https://github.com/YOUR_USERNAME/hostaway-messages.git
```

## 2. Commit and Push to GitHub

```bash
# Stage all files
git add .

# Create initial commit
git commit -m "Initial commit: Production-ready Hostaway Messages Dashboard"

# Push to GitHub
git branch -M main
git push -u origin main
```

## 3. Deploy to Vercel

### Option A: Via Vercel Dashboard (Easiest)

1. Go to https://vercel.com
2. Sign up/Login with GitHub
3. Click "Add New Project"
4. Import your GitHub repository
5. Add environment variables (see `.env.example`)
6. Click "Deploy"

### Option B: Via Vercel CLI

```bash
npm install -g vercel
vercel login
vercel link
vercel env add HOSTAWAY_ACCOUNT_ID production
vercel env add HOSTAWAY_API_KEY production
vercel env add OPENAI_API_KEY production
vercel env add SECRET_KEY production
vercel env add FLASK_DEBUG production false
vercel --prod
```

## 4. Set Up GitHub Actions (Optional)

1. Get Vercel token from https://vercel.com/account/tokens
2. Get project IDs from `.vercel/project.json` (after `vercel link`)
3. Add GitHub Secrets:
   - `VERCEL_TOKEN`
   - `VERCEL_ORG_ID`
   - `VERCEL_PROJECT_ID`
4. Push to main branch to trigger deployment

## ⚠️ Important Notes

**Vercel Limitations:**
- SQLite databases won't work (read-only filesystem)
- Background sync jobs cannot run (max 300s timeout)
- File storage is ephemeral

**Recommended:** Use Railway, Render, or Fly.io for full functionality.

See `VERCEL_DEPLOYMENT.md` for details and alternatives.
