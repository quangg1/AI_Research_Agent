# Windows PowerShell script to pull infinite loop fix
Write-Host "🔄 Pulling infinite quality loop fix..." -ForegroundColor Cyan

cd D:\AI_Research_Agent

# Stash any uncommitted changes
Write-Host "📦 Stashing local changes..." -ForegroundColor Yellow
git stash push -m "auto-stash before loop fix pull"

# Fetch and checkout PR branch
Write-Host "🌿 Checking out PR branch..." -ForegroundColor Yellow
git fetch origin cursor/fix-kiln-memo-quality-4dd5
git checkout cursor/fix-kiln-memo-quality-4dd5
git pull origin cursor/fix-kiln-memo-quality-4dd5

Write-Host "✅ Loop fix pulled! Changes:" -ForegroundColor Green
Write-Host "  - Added quality_regeneration_count to prevent infinite rewrites" -ForegroundColor White
Write-Host "  - Max 2 quality regenerations, then force publish" -ForegroundColor White
Write-Host "  - Fixed memo_gate and state schema" -ForegroundColor White

Write-Host ""
Write-Host "🔄 Restart Docker containers:" -ForegroundColor Yellow
Write-Host "  docker-compose down && docker-compose up -d" -ForegroundColor White
