#!/bin/bash
# Script to help deploy to Vercel

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "============================================================"
echo "Vercel Deployment Helper"
echo "============================================================"
echo ""

# Check if Vercel CLI is installed
if ! command -v vercel &> /dev/null; then
    echo "⚠ Vercel CLI not found."
    echo ""
    echo "Option 1: Install Vercel CLI (recommended)"
    echo "  npm install -g vercel"
    echo ""
    echo "Option 2: Deploy via Web Interface"
    echo "  1. Go to https://vercel.com/dashboard"
    echo "  2. Click 'Add New' → 'Project'"
    echo "  3. Import your repository"
    echo "  4. Add environment variables (see VERCEL_ENV_VARS.md)"
    echo "  5. Click 'Deploy'"
    echo ""
    read -p "Install Vercel CLI now? (y/n): " install_cli
    if [ "$install_cli" = "y" ]; then
        if command -v npm &> /dev/null; then
            npm install -g vercel
        else
            echo "✗ npm not found. Please install Node.js first: https://nodejs.org/"
            exit 1
        fi
    else
        echo ""
        echo "Proceeding with web interface deployment..."
        echo "See DEPLOYMENT_CHECKLIST.md for step-by-step instructions."
        exit 0
    fi
fi

echo "✓ Vercel CLI found"
echo ""

# Check if logged in
if ! vercel whoami &> /dev/null; then
    echo "⚠ Not logged in to Vercel"
    echo "Running: vercel login"
    vercel login
fi

echo "✓ Logged in to Vercel"
echo ""

# Check for uncommitted changes
if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ You have uncommitted changes"
    echo ""
    read -p "Commit changes before deploying? (y/n): " commit_changes
    if [ "$commit_changes" = "y" ]; then
        git add .
        git commit -m "Prepare for Vercel deployment"
        git push
        echo "✓ Changes committed and pushed"
    fi
fi

echo ""
echo "============================================================"
echo "Deployment Options"
echo "============================================================"
echo ""
echo "1. Deploy to production (vercel --prod)"
echo "2. Deploy to preview (vercel)"
echo "3. Show environment variables to add manually"
echo ""
read -p "Choose option (1/2/3): " deploy_option

case $deploy_option in
    1)
        echo ""
        echo "Deploying to production..."
        echo "⚠ Make sure you've added all environment variables in Vercel dashboard first!"
        echo ""
        read -p "Continue? (y/n): " confirm
        if [ "$confirm" = "y" ]; then
            vercel --prod
        fi
        ;;
    2)
        echo ""
        echo "Deploying to preview..."
        echo "⚠ Make sure you've added all environment variables in Vercel dashboard first!"
        echo ""
        read -p "Continue? (y/n): " confirm
        if [ "$confirm" = "y" ]; then
            vercel
        fi
        ;;
    3)
        echo ""
        echo "============================================================"
        echo "Environment Variables to Add in Vercel"
        echo "============================================================"
        echo ""
        ./scripts/prepare_vercel_env.sh
        echo ""
        echo "After adding these variables, run this script again to deploy."
        ;;
    *)
        echo "Invalid option"
        exit 1
        ;;
esac

