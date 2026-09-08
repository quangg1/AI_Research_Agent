#!/bin/bash
# Comprehensive check for all threshold imports

echo "=== Checking LOCAL code ==="
echo ""
echo "Files with app.config.thresholds (should be EMPTY):"
grep -r "from app.config.thresholds" apps/agent/app/ --include="*.py" 2>/dev/null || echo "✓ None found (GOOD)"

echo ""
echo "Files with app.conf.thresholds (should find 7 files):"
grep -r "from app.conf.thresholds" apps/agent/app/ --include="*.py" 2>/dev/null | wc -l

echo ""
echo "=== Detailed list of conf imports ==="
grep -r "from app.conf.thresholds" apps/agent/app/ --include="*.py" 2>/dev/null

echo ""
echo "=== Checking if conf folder exists ==="
ls -la apps/agent/app/conf/ 2>/dev/null || echo "✗ conf/ folder NOT FOUND!"

echo ""
echo "=== Checking if old config folder exists (should NOT) ==="
ls -la apps/agent/app/config/ 2>/dev/null && echo "✗ config/ folder STILL EXISTS (BAD)!" || echo "✓ config/ folder removed (GOOD)"
