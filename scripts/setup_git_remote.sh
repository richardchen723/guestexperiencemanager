#!/bin/bash
# Helper script to set up git remote and push

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "============================================================"
echo "Git Remote Setup"
echo "============================================================"
echo ""

# Check if remote already exists
if git remote get-url origin &> /dev/null; then
    echo "✓ Remote 'origin' already configured:"
    git remote get-url origin
    echo ""
    read -p "Push to this remote? (y/n): " confirm
    if [ "$confirm" = "y" ]; then
        echo ""
        echo "Pushing to remote..."
        git push -u origin main
        echo ""
        echo "✅ Successfully pushed to remote!"
        exit 0
    else
        echo "Aborted."
        exit 0
    fi
fi

echo "No remote repository configured."
echo ""
echo "Options:"
echo "1. Add existing repository URL"
echo "2. Create new repository on GitHub (opens browser)"
echo ""
read -p "Choose option (1/2): " option

case $option in
    1)
        echo ""
        read -p "Enter repository URL (e.g., https://github.com/user/repo.git): " repo_url
        if [ -z "$repo_url" ]; then
            echo "✗ No URL provided"
            exit 1
        fi
        
        echo ""
        echo "Adding remote..."
        git remote add origin "$repo_url"
        echo "✓ Remote added"
        
        echo ""
        echo "Pushing to remote..."
        git push -u origin main
        echo ""
        echo "✅ Successfully pushed to remote!"
        ;;
    2)
        echo ""
        echo "Opening GitHub to create new repository..."
        echo "After creating the repository, come back and run:"
        echo "  git remote add origin <your-repo-url>"
        echo "  git push -u origin main"
        echo ""
        
        # Try to open GitHub in browser
        if command -v open &> /dev/null; then
            open "https://github.com/new"
        elif command -v xdg-open &> /dev/null; then
            xdg-open "https://github.com/new"
        else
            echo "Please visit: https://github.com/new"
        fi
        ;;
    *)
        echo "Invalid option"
        exit 1
        ;;
esac

