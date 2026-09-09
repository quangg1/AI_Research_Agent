@echo off
REM Script to pull PR fixes to your local D:\AI_Research_Agent

echo ========================================
echo  Pull Kiln Quality Fixes to Local
echo ========================================
echo.

cd /d D:\AI_Research_Agent

echo [1/5] Checking current branch...
git branch --show-current
echo.

echo [2/5] Fetching latest from GitHub...
git fetch origin
echo.

echo [3/5] Checking out PR branch...
git checkout cursor/fix-kiln-memo-quality-4dd5
echo.

echo [4/5] Pulling latest changes...
git pull origin cursor/fix-kiln-memo-quality-4dd5
echo.

echo [5/5] Verifying commits...
git log --oneline -5
echo.

echo ========================================
echo  Pull Complete!
echo ========================================
echo.
echo Next steps:
echo 1. Rebuild Docker containers: docker-compose down ^&^& docker-compose up -d --build
echo 2. Run tests: cd apps\agent ^&^& python -m pytest tests\test_memo_quality_fixes.py -v
echo 3. Test research query with new code
echo.

pause
