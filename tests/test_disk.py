# tests/test_disk.py
"""
Checks NVMe inventory using `nvme list -o json`:
  - Exactly 4x Lexar SSD NM790 4TB drives are present
  - At least one ~256 GB NVMe is present (any make/model)
  - Write/read/verify test on each Lexar drive using fio

Requires: nvme-cli, fio
"""

import json
import os
import shutil
import subprocess
import pytest


# ---- helpers ----
def _nvme_list_json():
    """
    Return parsed JSON from `nvme list -o json`.
    Skip tests if nvme-cli is not installed or command fails.
    """
    if shutil.which("nvme") is None:
        pytest.skip("nvme-cli not installed (sudo apt-get install -y nvme-cli)")
    try:
        res = subprocess.run(
            ["nvme", "list", "-o", "json"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        pytest.skip(f"`nvme list -o json` failed: {e.stderr or e.stdout}")
    out = (res.stdout or "").strip()
    if not out:
        pytest.skip("`nvme list -o json` returned empty output")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        pytest.skip(f"Failed to parse nvme JSON: {e}")
    return data


def _devices(data):
    """Return the list of device dicts from nvme list JSON."""
    devs = data.get("Devices") or []
    # Some nvme-cli builds return "devices" in lowercase
    if not devs and isinstance(data, dict):
        devs = data.get("devices") or []
    return devs


def _bytes_to_gb(b):
    """Decimal gigabytes (GB) for coarse matching."""
    try:
        return float(b) / 1e9
    except Exception:
        return 0.0


def _summarize(devs):
    lines = []
    for d in devs:
        node = d.get("DevicePath") or d.get("NameSpace") or d.get("Name") or "?"
        model = d.get("ModelNumber") or d.get("Model") or "?"
        size_b = d.get("PhysicalSize") or d.get("Size") or 0
        size_gb = _bytes_to_gb(size_b)
        lines.append(f"- {node}: {model} ~{size_gb:.2f} GB")
    return "\n".join(lines)


# ---- tests ----
def test_lexar_nm790_4tb_count():
    data = _nvme_list_json()
    devs = _devices(data)
    # Count model matches (exact string as shown by nvme-cli)
    target_model = "Lexar SSD NM790 4TB"
    count = sum(1 for d in devs if (d.get("ModelNumber") or "") == target_model)
    assert count == 4, (
        f"Expected 4x '{target_model}', found {count}.\nInventory:\n{_summarize(devs)}"
    )


def test_has_one_approx_256gb_drive():
    data = _nvme_list_json()
    devs = _devices(data)
    # Look for any NVMe with size between 200 GB and 300 GB (broad '≈256 GB' band)
    low_gb, high_gb = 200.0, 300.0
    sized = []
    for d in devs:
        size_b = d.get("PhysicalSize") or d.get("Size") or 0
        size_gb = _bytes_to_gb(size_b)
        if low_gb <= size_gb <= high_gb:
            sized.append(d)

    assert len(sized) >= 1, (
        f"No ~256 GB NVMe found in [{low_gb}, {high_gb}] GB band.\nInventory:\n{_summarize(devs)}"
    )


def _get_mount_point(device_path):
    """Get the mount point for a device or its partitions."""
    try:
        # Use lsblk to get mount points for device and all partitions
        result = subprocess.run(
            ["lsblk", "-n", "-o", "MOUNTPOINT", device_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            # lsblk returns multiple lines if there are partitions
            # Return the first non-empty mount point
            for line in result.stdout.strip().split('\n'):
                mount_point = line.strip()
                if mount_point:
                    return mount_point
    except Exception:
        pass
    return None


def _run_fio_verify_test(test_file_path, size_mb=100):
    """
    Run fio write/verify test on a specific file path.
    Returns (success: bool, message: str)
    """
    # Clean up any leftover test file from previous interrupted runs
    try:
        if os.path.exists(test_file_path):
            os.remove(test_file_path)
    except Exception:
        pass  # Ignore cleanup errors

    # fio test: write with verification, then cleanup
    cmd = [
        "fio",
        "--name=lexar_nvme_test",
        f"--filename={test_file_path}",
        f"--size={size_mb}M",
        "--rw=write",
        "--verify=crc32c",
        "--direct=1",
        "--ioengine=libaio",
        "--iodepth=4",
        "--unlink=1",  # Auto-cleanup
        "--output-format=terse",
    ]

    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,  # 2 minute timeout
        )
        return True, "PASS"
    except subprocess.TimeoutExpired:
        return False, "fio timed out (>2 min)"
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        stdout = (e.stdout or "").strip()
        err_msg = stderr if stderr else stdout
        return False, f"fio failed: {err_msg[:200]}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"
    finally:
        # Explicit cleanup in case fio was interrupted or --unlink=1 failed
        try:
            if os.path.exists(test_file_path):
                os.remove(test_file_path)
        except Exception:
            pass  # Don't fail the test due to cleanup issues


def test_lexar_drives_write_functionality(cfg):
    """Test that all 4 Lexar drives can be written to and read from using fio."""
    # Check if fio is installed
    if shutil.which("fio") is None:
        pytest.skip("fio not installed (sudo apt-get install -y fio)")

    data = _nvme_list_json()
    devs = _devices(data)

    # Find all Lexar drives
    target_model = "Lexar SSD NM790 4TB"
    lexar_devs = [d for d in devs if (d.get("ModelNumber") or "") == target_model]

    if len(lexar_devs) != 4:
        pytest.skip(f"Expected 4 Lexar drives, found {len(lexar_devs)}")

    # Check config for test settings
    disk_cfg = cfg.get("disk", {})
    skip_write_test = disk_cfg.get("skip_write_test", False)
    test_size_mb = int(disk_cfg.get("write_test_size_mb", 100))

    if skip_write_test:
        pytest.skip("Disk write test disabled in config (disk.skip_write_test=true)")

    results = []
    failures = []

    for d in lexar_devs:
        device_path = d.get("DevicePath") or d.get("NameSpace") or d.get("Name")
        if not device_path:
            failures.append(f"Unknown device path for {d.get('ModelNumber')}")
            continue

        mount_point = _get_mount_point(device_path)

        if not mount_point:
            # Drive not mounted - skip for safety
            results.append(f"{device_path}: SKIPPED (not mounted)")
            continue

        # Create test file path on mounted drive
        test_file = os.path.join(mount_point, ".fio_nvme_write_test")

        # Run fio test
        success, message = _run_fio_verify_test(test_file, size_mb=test_size_mb)

        if success:
            results.append(f"{device_path} at {mount_point}: {message}")
        else:
            failures.append(f"{device_path} at {mount_point}: {message}")

    # Check that we tested at least some drives
    tested_count = len([r for r in results if "PASS" in r])
    skipped_count = len([r for r in results if "SKIPPED" in r])

    if tested_count == 0 and skipped_count == len(lexar_devs):
        pytest.skip(
            "All 4 Lexar drives are unmounted - cannot safely test writes.\n"
            "To enable write testing, run: ./lexar-drives.sh mount\n"
            "Or set 'disk.auto_mount_for_testing: true' in config.yaml for CI/CD"
        )

    # Report results
    summary = "\n".join(results)

    if failures:
        failure_msg = "\n".join(failures)
        pytest.fail(
            f"Write test failures on {len(failures)} drive(s):\n{failure_msg}\n\n"
            f"Results:\n{summary}"
        )

    # Assert we tested at least one drive successfully
    assert tested_count > 0, f"No drives were successfully tested.\n{summary}"

