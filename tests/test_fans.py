# tests/test_fans_bmc.py
import os, re, shutil, subprocess, pytest

def _canon(s: str) -> str:
    s = (s or "").strip().upper()
    return re.sub(r"[^A-Z0-9]", "", s)

def _run_ipmitool(args):
    """
    Try without sudo first (some systems allow local KCS without sudo),
    then with sudo. Returns (rc, stdout, stderr).
    """
    base = ["ipmitool"]
    for cmd in (base + args, ["sudo"] + base + args):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode == 0:
                return p.returncode, p.stdout, p.stderr
            # if permission denied on first try, fall through to sudo attempt
            if "Permission denied" in (p.stderr or "") and cmd[0] != "sudo":
                continue
            return p.returncode, p.stdout, p.stderr
        except FileNotFoundError:
            break
    return 127, "", "ipmitool not found"

def _parse_pipe_table(text: str):
    """Parse `ipmitool sensor` (pipe table). Yield dicts with name, reading, unit, status."""
    rows = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue
        name, reading, unit, status = parts[:4]
        rows.append({"name": name, "reading": reading, "unit": unit, "status": status})
    return rows

def _parse_csv(text: str):
    """Parse `ipmitool sensor -c` (CSV). Yield dicts with name, reading, unit, status."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        name, reading, unit, status = parts[:4]
        rows.append({"name": name, "reading": reading, "unit": unit, "status": status})
    return rows

def _to_rpm(reading: str):
    return float(reading) if re.fullmatch(r"[0-9]+(\.[0-9]+)?", (reading or "").strip()) else None

@pytest.mark.parametrize(
    "want_names",
    [ {"SYSFAN1","SYSFAN2","GPUFAN12E","GPUFAN34","GPUFAN56E","GPUFAN78"} ]
)
def test_fans_local_ipmi_kcs(cfg, want_names):
    if shutil.which("ipmitool") is None:
        pytest.skip("ipmitool not installed")

    # Allow overriding expected fans from config.yaml
    mem = cfg.get("bmc", {}).get("fans_expect", None)
    if isinstance(mem, list) and mem:
        want_names = { _canon(x) for x in mem }

    # 1) Prefer CSV (simpler), fall back to pipe table
    rc, out, err = _run_ipmitool(["sensor", "-c"])
    rows = _parse_csv(out) if rc == 0 and out.strip() else []
    if not rows:
        rc, out, err = _run_ipmitool(["sensor"])
        if rc != 0 or not out.strip():
            pytest.skip(f"ipmitool failed: {err or out}")
        rows = _parse_pipe_table(out)

    # 2) Scan and validate
    seen = set()
    bad  = []
    for r in rows:
        key = _canon(r["name"])
        if key not in want_names:
            continue
        unit_ok = bool(re.search(r"rpm", r["unit"], re.I))
        stat_ok = bool(re.fullmatch(r"ok", r["status"], re.I))
        rpm     = _to_rpm(r["reading"])
        speed_ok = (rpm is not None and rpm > 0.0)
        if unit_ok and stat_ok and speed_ok:
            seen.add(key)
        else:
            bad.append((r["name"], r["reading"], r["unit"], r["status"]))
            seen.add(key)  # mark as present but invalid

    missing = sorted(want_names - seen)
    assert not missing and not bad, (
        (f"Missing fans: {', '.join(missing)}\n" if missing else "")
        + "\n".join(f"Bad: {n} reading={rd} unit={u} status={st}" for n, rd, u, st in bad)
    )

