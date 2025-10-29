# tests/test_mem.py
import json
import shutil
import subprocess
import pytest

# ------------ helpers ------------
def bytes_to_gib(b: int) -> float:
    return float(b) / (1024 ** 3) if isinstance(b, (int, float)) else 0.0

def mhz_from_hz(hz) -> int:
    # lshw reports Hz as an integer (e.g., 2_666_000_000). Convert to MHz.
    return int(round(hz / 1_000_000)) if isinstance(hz, (int, float)) and hz > 0 else 0

def is_populated_dimm(node: dict) -> bool:
    # Accept only populated DIMM “bank:*” entries with positive size
    return (
        node.get("class") == "memory"
        and str(node.get("id", "")).startswith("bank:")
        and isinstance(node.get("size", 0), (int, float))
        and node["size"] > 0
    )

def format_dimms(dimms):
    return "\n".join(
        f"- {d['slot']:<12}  {d['size_gib']:.2f} GiB @ {d['mhz']} MHz  "
        f"(vendor: {d.get('vendor','')}, product: {d.get('product','')}, clock_hz: {d.get('clock_hz','?')})"
        for d in dimms
    )

# ------------ fixtures ------------
@pytest.fixture(scope="module")
def lshw_mem_json():
    if shutil.which("lshw") is None:
        pytest.skip("lshw not installed (sudo apt-get install -y lshw)")
    try:
        res = subprocess.run(
            ["sudo", "lshw", "-json", "-C", "memory"],
            check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        pytest.skip(f"lshw failed: {e.stderr or e.stdout}")
    out = (res.stdout or "").strip()
    if not out:
        pytest.skip("lshw returned empty output")
    try:
        return json.loads(out)   # usually a list
    except json.JSONDecodeError as e:
        pytest.skip(f"Failed to parse lshw JSON: {e}")

@pytest.fixture(scope="module")
def dimm_info(lshw_mem_json):
    objs = lshw_mem_json if isinstance(lshw_mem_json, list) else [lshw_mem_json]
    dimms = []
    for n in objs:
        if is_populated_dimm(n):
            clock_hz = n.get("clock")
            dimms.append(
                {
                    "id": n.get("id"),
                    "slot": n.get("slot") or n.get("physid") or n.get("id"),
                    "size_gib": bytes_to_gib(n.get("size", 0)),
                    "mhz": mhz_from_hz(clock_hz),
                    "clock_hz": clock_hz,
                    "vendor": n.get("vendor"),
                    "product": n.get("product"),
                }
            )
    return dimms

# ------------ tests ------------
def test_dimms_populated_count(cfg, dimm_info):
    exp = int(cfg["mem"]["dimms_expected"])
    assert len(dimm_info) == exp, (
        f"Found {len(dimm_info)} populated DIMMs, expected {exp}.\n"
        + format_dimms(dimm_info)
    )

def test_each_dimm_size(cfg, dimm_info):
    exp = float(cfg["mem"]["per_dimm_gib"])
    tol = float(cfg["mem"].get("size_tolerance_gib", 0.5))
    bad = [d for d in dimm_info if not (exp - tol <= d["size_gib"] <= exp + tol)]
    assert not bad, (
        f"Non-{exp} GiB DIMMs (±{tol} GiB allowed):\n"
        + format_dimms(bad)
        + "\nAll DIMMs:\n"
        + format_dimms(dimm_info)
    )

def test_each_dimm_speed_exact(cfg, dimm_info):
    exact = int(cfg["mem"]["speed_mhz"])
    # Primary check: MHZ equals exact (rounded from Hz)
    bad = [d for d in dimm_info if d["mhz"] != exact]
    # Helpful secondary diagnostic: if MHZ differs due to rounding, confirm Hz
    if bad:
        exact_hz = exact * 1_000_000
        # If some DIMMs report exact_hz, they’ll pass here; otherwise we keep them in 'bad'
        really_bad = [
            d for d in bad
            if not isinstance(d.get("clock_hz"), (int, float)) or int(d["clock_hz"]) != exact_hz
        ]
        assert not really_bad, (
            f"DIMMs not at exactly {exact} MHz ({exact_hz} Hz):\n"
            + format_dimms(really_bad)
            + "\nAll DIMMs:\n"
            + format_dimms(dimm_info)
        )

