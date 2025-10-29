# tests/test_disk.py
"""
Checks NVMe inventory using `nvme list -o json`:
  - Exactly 4x Lexar SSD NM790 4TB drives are present
  - At least one ~256 GB NVMe is present (any make/model)

Requires: nvme-cli
"""

import json
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

