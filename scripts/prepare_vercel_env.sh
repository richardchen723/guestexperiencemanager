#!/bin/bash
# Generate environment variables file for Vercel
# This extracts all necessary variables from .env and formats them for Vercel

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "============================================================"
echo "Vercel Environment Variables"
echo "============================================================"
echo ""
echo "Copy these to Vercel Dashboard → Settings → Environment Variables"
echo ""
echo "For each variable, select all environments: Production, Preview, Development"
echo ""
echo "============================================================"
echo ""

# Load .env file
if [ -f ".env" ]; then
    # Extract and format variables
    echo "# Database URLs"
    grep "^DATABASE_URL=" .env || echo "# DATABASE_URL=not set"
    grep "^USERS_DATABASE_URL=" .env || echo "# USERS_DATABASE_URL=not set"
    grep "^CACHE_DATABASE_URL=" .env || echo "# CACHE_DATABASE_URL=not set"
    echo ""
    
    echo "# AWS S3"
    grep "^AWS_ACCESS_KEY_ID=" .env || echo "# AWS_ACCESS_KEY_ID=not set"
    grep "^AWS_SECRET_ACCESS_KEY=" .env || echo "# AWS_SECRET_ACCESS_KEY=not set"
    grep "^AWS_S3_BUCKET_NAME=" .env || echo "# AWS_S3_BUCKET_NAME=not set"
    grep "^AWS_S3_REGION=" .env || echo "# AWS_S3_REGION=not set"
    grep "^CONVERSATIONS_S3_PREFIX=" .env || echo "# CONVERSATIONS_S3_PREFIX=conversations/"
    echo ""
    
    echo "# Hostaway API"
    grep "^HOSTAWAY_ACCOUNT_ID=" .env || echo "# HOSTAWAY_ACCOUNT_ID=not set (add if you have)"
    grep "^HOSTAWAY_API_KEY=" .env || echo "# HOSTAWAY_API_KEY=not set (add if you have)"
    echo ""
    
    echo "# OpenAI"
    grep "^OPENAI_API_KEY=" .env || echo "# OPENAI_API_KEY=not set"
    echo ""
    
    echo "# Flask"
    grep "^SECRET_KEY=" .env || echo "# SECRET_KEY=not set"
    echo "FLASK_DEBUG=False"
    echo "FLASK_HOST=0.0.0.0"
    echo "FLASK_PORT=5001"
    echo ""
    
    echo "# Other"
    echo "VERBOSE=False"
    echo "MESSAGE_SYNC_PARALLEL_WORKERS=10"
    echo "VERCEL=1"
    echo ""
    
else
    echo "⚠ .env file not found!"
    exit 1
fi

echo "============================================================"
echo ""
echo "Instructions:"
echo "1. Go to Vercel Dashboard → Your Project → Settings → Environment Variables"
echo "2. For each variable above, click 'Add' and paste the key and value"
echo "3. Select all environments (Production, Preview, Development)"
echo "4. Save and redeploy"
echo ""

