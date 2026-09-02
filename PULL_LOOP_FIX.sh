#!/bin/bash
# Linux/macOS script to pull infinite loop fix

echo "🔄 Pulling infinite quality loop fix..."

cd /d/AI_Research_Agent || cd ~/AI_Research_Agent || exit 1

# Stash any uncommitted changes
echo "📦 Stashing local changes..."
git stash push -m "auto-stash before loop fix pull"

# Fetch and checkout PR branch
echo "🌿 Checking out PR branch..."
git fetch origin cursor/fix-kiln-memo-quality-4dd5
git checkout cursor/fix-kiln-memo-quality-4dd5
git pull origin cursor/fix-kiln-memo-quality-4dd5

echo "✅ Loop fix pulled! Changes:"
echo "  - Added quality_regeneration_count to prevent infinite rewrites"
echo "  - Max 2 quality regenerations, then force publish"
echo "  - Fixed memo_gate and state schema"

echo ""
echo "🔄 Restart Docker containers:"
echo "  docker-compose down && docker-compose up -d"
