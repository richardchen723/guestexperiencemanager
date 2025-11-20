#!/bin/bash
# Script to remove secrets from git history using git filter-branch
# WARNING: This rewrites git history. Only use if you haven't shared the repo widely.

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "============================================================"
echo "Remove Secrets from Git History"
echo "============================================================"
echo ""
echo "⚠️  WARNING: This will rewrite git history!"
echo "⚠️  Only proceed if you haven't shared this repo widely."
echo "⚠️  If others have cloned it, coordinate with them first."
echo ""
read -p "Continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Removing secrets from git history..."

# List of secrets to remove (partial matches to catch variations)
# NOTE: Add your actual secrets here before running
# Example format (replace with your actual secrets):
# SECRETS=(
#     "your_aws_access_key_id_here"
#     "your_aws_secret_access_key_here"
#     "your_openai_api_key_prefix_here"
#     "your_database_password_here"
# )
SECRETS=(
    "REPLACE_WITH_YOUR_ACTUAL_SECRETS"
)

# Use git filter-branch to remove secrets
for secret in "${SECRETS[@]}"; do
    echo "Removing: $secret"
    git filter-branch --force --index-filter \
        "git rm --cached --ignore-unmatch -r . || true" \
        --prune-empty --tag-name-filter cat -- --all
    
    # Replace in all files
    git filter-branch --force --tree-filter \
        "find . -type f -exec sed -i '' 's/$secret/REMOVED_SECRET/g' {} + 2>/dev/null || true" \
        --prune-empty --tag-name-filter cat -- --all
done

echo ""
echo "✅ Secrets removed from history"
echo ""
echo "Next steps:"
echo "1. Force push: git push --force --all"
echo "2. Clean up: git for-each-ref --format='delete %(refname)' refs/original | git update-ref --stdin"
echo "3. Garbage collect: git reflog expire --expire=now --all && git gc --prune=now --aggressive"

