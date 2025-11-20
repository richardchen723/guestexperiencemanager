# Fixing Secrets in Git History

GitHub is blocking the push because secrets exist in previous commits (even though we've removed them from current files).

## The Problem

The secrets are in these old commits:
- `409836c` - Fix vercel.json configuration and add .vercelignore
- `0015774` - Prepare for Vercel deployment with AWS RDS and S3  
- `53a55f5` - Ready for Vercel deployment with AWS RDS and S3

## Solution Options

### Option 1: Allow Secrets in GitHub (Quick Fix)

If these are test/development credentials you can rotate:

1. Go to the URLs provided in the error message:
   - https://github.com/richardchen723/guestexperiencemanager/security/secret-scanning/unblock-secret/35kYUKnx2aKyZRamnwL2MpJUkGN
   - https://github.com/richardchen723/guestexperiencemanager/security/secret-scanning/unblock-secret/35kYUFXI5AFPxzrp8wiDnjWsjKO
   - https://github.com/richardchen723/guestexperiencemanager/security/secret-scanning/unblock-secret/35kYUIIcVhKTtrHTPCaiSyDHkdY

2. Click "Allow secret" for each one
3. Push again: `git push --set-upstream origin main`

**⚠️ Note:** Only do this if you plan to rotate these credentials immediately!

### Option 2: Rewrite Git History (Recommended for Production)

Remove secrets from git history entirely:

```bash
# Install git-filter-repo (recommended) or use git filter-branch
pip install git-filter-repo

# Remove secrets from history
git filter-repo --path DEPLOYMENT_READY.md --path DEPLOY_NOW.md --path VERCEL_DEPLOYMENT_STEPS.md --path VERCEL_ENV_VARS.md --invert-paths

# Or use BFG Repo-Cleaner (easier)
# Download from: https://rtyley.github.io/bfg-repo-cleaner/
java -jar bfg.jar --replace-text secrets.txt

# Force push (coordinate with team first!)
git push --force --all
```

### Option 3: Create New Repository (Simplest)

If this is a new project and you haven't shared it widely:

1. Create a fresh repository
2. Copy only the current code (without .git folder)
3. Initialize new git repo
4. Commit and push

## Recommended: Option 1 (Quick Fix)

Since you're just starting deployment and can rotate credentials:

1. **Rotate the exposed credentials:**
   - Generate new AWS access keys in IAM Console
   - Generate new SECRET_KEY
   - Update your `.env` file
   - Update Vercel environment variables

2. **Allow the secrets in GitHub** (using the URLs above)

3. **Push successfully**

4. **Update documentation** to reflect that old credentials are invalid

## After Fixing

Once pushed successfully:

1. Update Vercel environment variables with new credentials
2. Test the deployment
3. Document the credential rotation

