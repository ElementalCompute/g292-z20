# tests/test_nvbandwidth.py
"""
Validates nvbandwidth host->device memcpy throughput per GPU.

Config (tests/config.yaml):
  nvbandwidth:
    # Minimum acceptable Host->Device memcpy GB/s per GPU (tune for risers/Gen)
    min_h2d_gbps: 26.0
    # Optional: explicit path to the binary (else we'll auto-detect)
    # bin: "./nvbandwidth/nvbandwidth"

Also uses (optionally) gpus.expect_count if present to sanity-check GPU count.
"""

import json
import os
import shutil
import subprocess
import pytest


def _pick_binary(cfg):
    # 1) config.yaml -> nvbandwidth.bin
    bin_from_cfg = (cfg.get("nvbandwidth") or {}).get("bin")
    if bin_from_cfg and shutil.which(bin_from_cfg) or (bin_from_cfg and os.path.exists(bin_from_cfg)):
        return bin_from_cfg
    # 2) env override NVBANDWIDTH_BIN
    env_bin = os.environ.get("NVBANDWIDTH_BIN")
    if env_bin and shutil.which(env_bin) or (env_bin and os.path.exists(env_bin)):
        return env_bin
    # 3) common relative path used in your example
    if os.path.exists("nvbandwidth/nvbandwidth"):
        return "nvbandwidth/nvbandwidth"
    # 4) PATH
    if shutil.which("nvbandwidth"):
        return "nvbandwidth"
    return None


def _run_json(cmd):
    try:
        res = subprocess.run(
            cmd + ["-t", "0", "--json"],
            check=True, capture_output=True, text=True
        )
    except FileNotFoundError:
        return None, "not found"
    except subprocess.CalledProcessError as e:
        return None, (e.stderr or e.stdout or str(e))
    out = (res.stdout or "").strip()
    try:
        data = json.loads(out)
        return data, None
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}\nOutput:\n{out[:1000]}"


def _find_h2d_test(nvb_json):
    """
    Locate the Host->Device memcpy testcase.
    We match by name 'host_to_device_memcpy_ce' or by a descriptive substring.
    """
    spec = (nvb_json or {}).get("nvbandwidth") or {}
    tcs = spec.get("testcases") or []
    for tc in tcs:
        name = (tc.get("name") or "").lower()
        desc = (tc.get("bandwidth_description") or "").lower()
        if "host_to_device" in name or "cpu(row) -> gpu(column)" in desc:
            return tc, spec
    return None, spec


def _matrix_to_floats(mat):
    """nvbandwidth emits strings; convert to float list (flatten row-wise)."""
    out = []
    for row in mat or []:
        out.extend([float(x) for x in row])
    return out


def _summarize_gpu_list(spec):
    devs = (spec.get("GPU Device list") or [])
    return "\n".join(f"- {d}" for d in devs)


def test_nvbandwidth_h2d(cfg):
    # Pick binary
    bin_path = _pick_binary(cfg)
    if not bin_path:
        pytest.skip("nvbandwidth binary not found. Set nvbandwidth.bin in config.yaml or NVBANDWIDTH_BIN env.")

    # Run with JSON output
    data, err = _run_json([bin_path])
    if err or not data:
        pytest.fail(f"Failed to run nvbandwidth: {err}")

    # Locate the host->device test case
    tc, spec = _find_h2d_test(data)
    assert tc is not None, "Did not find host->device memcpy test in nvbandwidth JSON."

    # Parse per-GPU GB/s numbers
    mat = tc.get("bandwidth_matrix") or []
    vals = _matrix_to_floats(mat)
    assert vals, f"No bandwidth values found.\nTestcase: {tc}"

    # Optional sanity: GPU count matches values
    gpu_list = spec.get("GPU Device list") or []
    if "gpus" in cfg and "expect_count" in cfg["gpus"]:
        exp = int(cfg["gpus"]["expect_count"])
        assert len(gpu_list) == exp, (
            f"nvbandwidth saw {len(gpu_list)} GPUs; expected {exp}.\n" + _summarize_gpu_list(spec)
        )
        # host->device test is CPU(row)->GPU(column): typically 1 row, N columns
        assert len(vals) >= exp, f"Bandwidth entries ({len(vals)}) < expected GPUs ({exp})."

    # Threshold (tune for your risers/Gen/width); defaults to 26.0 GB/s
    min_gbps = float((cfg.get("nvbandwidth") or {}).get("min_h2d_gbps", 26.0))

    # Find offenders
    bad = [(i, v) for i, v in enumerate(vals) if v < min_gbps]

    # Nice failure message with details
    details = (
        f"CUDA RT={spec.get('CUDA Runtime Version')}  Driver={spec.get('Driver Version')}  git={spec.get('git_version')}\n"
        f"GPUs:\n{_summarize_gpu_list(spec)}\n"
        f"H2D GB/s: {', '.join(f'{v:.2f}' for v in vals)}\n"
        f"Min required: {min_gbps:.2f} GB/s"
    )

    assert not bad, (
        "Some GPUs are below the H2D bandwidth floor:\n"
        + "\n".join(f"- GPU{idx}: {val:.2f} GB/s" for idx, val in bad)
        + "\n\n" + details
    )

    # Also assert testcase reported Passed (if present)
    status = (tc.get("status") or "").lower()
    assert status in ("", "passed"), f"nvbandwidth testcase status: {tc.get('status')}\n\n{details}"

