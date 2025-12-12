#!/usr/bin/env bash
# Manage Lexar NVMe drive mounts for testing

set -e

MOUNT_BASE="/mnt/nvme_test"
TARGET_MODEL="Lexar SSD NM790 4TB"

show_usage() {
    cat << EOF
Usage: $0 {mount|unmount|status}

Commands:
  mount     - Mount all Lexar NVMe drives for testing
  unmount   - Unmount all test drives
  status    - Show current mount status

Examples:
  $0 mount
  $0 unmount
  $0 status
EOF
    exit 1
}

detect_lexar_drives() {
    # Check if nvme-cli is installed
    if ! command -v nvme &> /dev/null; then
        echo "ERROR: nvme-cli not installed. Run: sudo apt-get install -y nvme-cli"
        exit 1
    fi

    # Get list of Lexar drives using nvme-cli
    NVME_JSON=$(sudo nvme list -o json 2>/dev/null)
    if [ -z "$NVME_JSON" ]; then
        echo "ERROR: Failed to get nvme list"
        exit 1
    fi

    # Extract device paths for Lexar drives
    DRIVES=($(python3 -c "
import json, sys
data = json.loads('''$NVME_JSON''')
devices = data.get('Devices', data.get('devices', []))
lexar_devs = [d.get('DevicePath', d.get('NameSpace', d.get('Name', '')))
              for d in devices
              if d.get('ModelNumber', '') == '$TARGET_MODEL']
print(' '.join(lexar_devs))
" 2>/dev/null))

    if [ ${#DRIVES[@]} -eq 0 ]; then
        echo "ERROR: No '$TARGET_MODEL' drives found"
        echo "Run 'nvme list' to see available drives"
        exit 1
    fi
}

do_mount() {
    echo "=== Detecting Lexar NVMe drives ==="
    detect_lexar_drives

    echo "Found ${#DRIVES[@]} Lexar drive(s):"
    for dev in "${DRIVES[@]}"; do
        echo "  - $dev"
    done
    echo ""

    # Mount each drive
    for i in "${!DRIVES[@]}"; do
        DEV="${DRIVES[$i]}"
        MOUNT_POINT="${MOUNT_BASE}${i}"

        # Check if device exists
        if [ ! -b "$DEV" ]; then
            echo "WARNING: $DEV does not exist, skipping"
            continue
        fi

        # Check if device or its partitions are already mounted somewhere
        EXISTING_MOUNT=$(lsblk -n -o MOUNTPOINT "$DEV" 2>/dev/null | grep -v '^$' | head -n1 || echo "")

        if [ -n "$EXISTING_MOUNT" ]; then
            echo "$DEV already mounted at $EXISTING_MOUNT (skipping)"
            continue
        fi

        # Detect the best device to mount (partition p1 if exists, else whole device)
        MOUNT_DEV="$DEV"
        PART1="${DEV}p1"

        if [ -b "$PART1" ]; then
            # Partition exists, prefer it over whole device
            MOUNT_DEV="$PART1"
        fi

        # Check if target device has a filesystem
        FS_TYPE=$(sudo blkid -o value -s TYPE "$MOUNT_DEV" 2>/dev/null || echo "")

        if [ -z "$FS_TYPE" ]; then
            echo "WARNING: $MOUNT_DEV has no filesystem, creating ext4..."
            sudo mkfs.ext4 -F "$MOUNT_DEV"
            FS_TYPE="ext4"
        fi

        # Create mount point if needed
        if [ ! -d "$MOUNT_POINT" ]; then
            echo "Creating mount point: $MOUNT_POINT"
            sudo mkdir -p "$MOUNT_POINT"
        fi

        # Check if target mount point already in use
        if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
            echo "$MOUNT_POINT already in use, skipping"
            continue
        fi

        echo "Mounting $MOUNT_DEV ($FS_TYPE) at $MOUNT_POINT"
        sudo mount "$MOUNT_DEV" "$MOUNT_POINT"

        # Set permissions so tests can write
        sudo chmod 777 "$MOUNT_POINT"
    done

    echo ""
    echo "=== Mount status ==="
    df -h | grep -E "(Filesystem|nvme)" || true
    echo ""
    echo "Drives ready for testing!"
    echo "Run tests with: ./run.sh"
}

do_unmount() {
    echo "=== Unmounting Lexar NVMe test drives ==="

    UNMOUNTED=0
    for i in {0..9}; do
        MOUNT_POINT="${MOUNT_BASE}${i}"

        if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
            echo "Unmounting $MOUNT_POINT"
            sudo umount "$MOUNT_POINT"
            UNMOUNTED=$((UNMOUNTED + 1))
        fi
    done

    if [ $UNMOUNTED -eq 0 ]; then
        echo "No test drives were mounted"
    else
        echo ""
        echo "$UNMOUNTED drive(s) unmounted successfully!"
    fi
}

do_status() {
    echo "=== Lexar Drive Mount Status ==="
    echo ""

    # Try to detect drives
    if command -v nvme &> /dev/null; then
        NVME_JSON=$(sudo nvme list -o json 2>/dev/null || echo "")
        if [ -n "$NVME_JSON" ]; then
            DRIVES=($(python3 -c "
import json, sys
data = json.loads('''$NVME_JSON''')
devices = data.get('Devices', data.get('devices', []))
lexar_devs = [d.get('DevicePath', d.get('NameSpace', d.get('Name', '')))
              for d in devices
              if d.get('ModelNumber', '') == '$TARGET_MODEL']
print(' '.join(lexar_devs))
" 2>/dev/null))

            if [ ${#DRIVES[@]} -gt 0 ]; then
                echo "Detected ${#DRIVES[@]} Lexar drive(s):"
                for DEV in "${DRIVES[@]}"; do
                    # Check if device or its partitions are mounted anywhere
                    EXISTING_MOUNT=$(lsblk -n -o MOUNTPOINT "$DEV" 2>/dev/null | grep -v '^$' | head -n1 || echo "")
                    if [ -n "$EXISTING_MOUNT" ]; then
                        echo "  ✓ $DEV → MOUNTED at $EXISTING_MOUNT"
                    else
                        echo "  ✗ $DEV → NOT MOUNTED"
                    fi
                done
            else
                echo "No Lexar drives detected"
            fi
        fi
    else
        echo "nvme-cli not installed"
    fi

    echo ""
    echo "All NVMe mounts:"
    df -h | grep -E "(Filesystem|nvme)" || echo "  (none)"
}

# Main script logic
case "${1:-}" in
    mount)
        do_mount
        ;;
    unmount)
        do_unmount
        ;;
    status)
        do_status
        ;;
    *)
        show_usage
        ;;
esac
