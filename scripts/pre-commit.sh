#!/usr/bin/env bash
# Pre-commit hook: Run fast regression check (3 test cases)
# Install: cp scripts/pre-commit.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

set -e

echo "Running pre-commit regression check..."

cd apps/agent

# Run fast regression check (3 quick cases)
python3 -m app.eval.regression_check --fast

exit_code=$?

if [ $exit_code -ne 0 ]; then
    echo ""
    echo "❌ Regression check FAILED. Commit blocked."
    echo "Fix regressions or run 'git commit --no-verify' to skip (not recommended)."
    exit 1
fi

echo "✅ Regression check passed"
exit 0
