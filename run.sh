#!/usr/bin/env bash

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Track if we auto-mounted (so we can auto-unmount after)
AUTO_MOUNTED=false

# Check if auto-mount is enabled in config (simple grep for boolean flag)
if grep -q "auto_mount_for_testing: true" "${SCRIPT_DIR}/tests/config.yaml" 2>/dev/null; then
    echo "=== Auto-mounting Lexar drives (disk.auto_mount_for_testing=true) ==="
    if [ -x "${SCRIPT_DIR}/lexar-drives.sh" ]; then
        "${SCRIPT_DIR}/lexar-drives.sh" mount
        AUTO_MOUNTED=true
        echo ""
    else
        echo "WARNING: lexar-drives.sh not found or not executable"
        echo ""
    fi
fi

# Setup cleanup trap to ensure unmount happens even if tests fail
cleanup() {
    if [ "$AUTO_MOUNTED" = true ]; then
        echo ""
        echo "=== Auto-unmounting Lexar drives ==="
        "${SCRIPT_DIR}/lexar-drives.sh" unmount
    fi
}

trap cleanup EXIT

# Run pytest as root using the virtualenv python
sudo "${SCRIPT_DIR}/.venv/bin/python" -m pytest "$@"
