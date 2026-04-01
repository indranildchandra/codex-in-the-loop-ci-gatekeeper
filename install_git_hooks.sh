#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$REPO_ROOT"

chmod +x .githooks/pre-commit
git config core.hooksPath .githooks

echo "Git hooks installed."
echo "Configured core.hooksPath=.githooks"
echo "Use SKIP_CI_GATEKEEPER_PRE_COMMIT=1 to bypass the hook for local testing."
