#!/bin/bash
# Session startup hook for Claude Code on the web
set -euo pipefail

# Only run in remote Claude Code environment
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    exit 0
fi

echo "Setting up development environment..."

pip install -r "$CLAUDE_PROJECT_DIR/circuit_gen/requirements.txt"

# Toolchain for circuit_gen (KiCad, FreeRouting, SKiDL)
echo "Setting up circuit_gen toolchain..."
"$CLAUDE_PROJECT_DIR/circuit_gen/setup.sh" || echo "Warning: circuit_gen setup failed"

echo "Session startup complete"
