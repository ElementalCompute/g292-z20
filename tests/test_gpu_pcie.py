# tests/test_gpu_pcie.py
# Port of your Bash GPU/PCIe mapping script into pytest.
# Requires: pciutils (lspci), dmidecode.

import os
import re
import shutil
import subprocess
import warnings

import pytest


# ------------------------------- shell helpers --------------------------------
def run(cmd):
    """Run command (list or str) and return stdout (str). Raises on non-zero."""
    if isinstance(cmd, str):
        proc = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
    else:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return proc.stdout


def try_run(cmd):
    """Run command and return stdout, or '' on error."""
    try:
        return run(cmd)
    except Exception:
        return ""


# ------------------------------- color helpers --------------------------------
RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"


def color_line(status: str, line: str) -> str:
    if "DOWNGRADED" in status:
        return f"{RED}{line}{RESET}"
    return f"{GREEN}{line}{RESET}"


# ------------------------------- functions port -------------------------------

def dmi_key_from_bdf(bdf: str) -> str:
    """
    DMI "Bus Address" mapping. From 0000:BB:DD.F -> <BB (4-hex)>:<DD (2-hex)>:00.<F>
    Matches your bash:
      printf "%04x:%02x:%02x.%x" bus dev 0 fn
    """
    m = re.match(r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])$", bdf)
    if not m:
        return ""
    _dom, bus, dev, fn = m.group(1), m.group(2), m.group(3), m.group(4)
    return f"{int(bus,16):04x}:{int(dev,16):02x}:{0:02x}.{int(fn,16)}"


def get_chain_for_device(dev_nodom: str):
    """
    Return list of BDFs from root->...->endpoint for device 'BB:DD.F'.
    Uses sysfs real path and extracts all '0000:BB:DD.F' components.
    """
    p = os.path.realpath(f"/sys/bus/pci/devices/0000:{dev_nodom}")
    return re.findall(r"0000:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", p)


def get_link_fields(dev_nodom: str):
    """
    Parse lspci -vv for endpoint 'BB:DD.F' (no domain) and extract:
      cap_speed, cap_width, sta_speed, sta_width
    Speeds are GT/s as strings (e.g. '16.0'), widths are ints as strings (e.g. '16').
    """
    txt = try_run(["bash", "-lc", f"lspci -s {dev_nodom} -vv | grep -E 'Lnk(Cap|Sta):'"])
    cap_speed = cap_width = sta_speed = sta_width = ""
    for line in txt.splitlines():
        if "LnkCap:" in line:
            m = re.search(r"Speed\s+([0-9.]+)GT/s", line)
            if m:
                cap_speed = m.group(1)
            m = re.search(r"Width x(\d+)", line)
            if m:
                cap_width = m.group(1)
        elif "LnkSta:" in line:
            m = re.search(r"Speed\s+([0-9.]+)GT/s", line)
            if m:
                sta_speed = m.group(1)
            m = re.search(r"Width x(\d+)", line)
            if m:
                sta_width = m.group(1)
    return cap_speed, cap_width, sta_speed, sta_width


def link_status_tag(cap_s: str, cap_w: str, sta_s: str, sta_w: str) -> str:
    """
    Status tag:
      - DOWNGRADED if sta_width < cap_width
      - IDLE if sta_speed < cap_speed (width OK)
      - OK otherwise
    """
    lane_down = False
    idle = False
    try:
        if cap_w and sta_w and int(sta_w) < int(cap_w):
            lane_down = True
    except Exception:
        pass
    try:
        if cap_s and sta_s and float(sta_s) < float(cap_s):
            idle = True
    except Exception:
        pass
    if lane_down:
        return "DOWNGRADED"
    if idle:
        return "IDLE"
    return "OK"


def get_gpu_short_name(dev_nodom: str) -> str:
    """
    Best-effort short descriptor from lspci -nn 'BB:DD.F' line.
    """
    line = try_run(["bash", "-lc", f"lspci -s {dev_nodom} -nn | head -n1"])
    line = re.sub(r"^[^:]*:\s*", "", line).strip()
    line = re.sub(r"\s\((rev|prog-if).*$", "", line)
    tokens = re.findall(r"\[[^\]]+\]", line)
    for i, t in enumerate(tokens):
        if re.match(r"^\[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\]$", t):
            return f"{tokens[i-1]} {t}" if i > 0 else t
    return re.sub(r"^[^\[]*(.*)$", r"\1", line)


def parse_dmi_slots():
    """
    Parse `dmidecode -t 9` (System Slots). Build maps:
      DMI_DESIG[busAddr] = Designation
      DMI_TYPE[busAddr]  = Type
    """
    if shutil.which("dmidecode") is None:
        return {}, {}
    out = try_run(["sudo", "dmidecode", "-t", "9"])
    DMI_DESIG, DMI_TYPE = {}, {}
    current_designation = ""
    current_type = ""
    # Work in blocks separated by blank lines
    for block in re.split(r"\n\s*\n", out):
        if not block.strip():
            continue
        mD = re.search(r"^\s*Designation:\s*(.+)$", block, re.M)
        if mD:
            current_designation = mD.group(1).strip()
        mT = re.search(r"^\s*Type:\s*(.+)$", block, re.M)
        if mT:
            current_type = mT.group(1).strip()
        mB = re.search(r"^\s*Bus Address:\s*([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])$", block, re.M)
        if mB and current_designation:
            bus_addr = mB.group(1)
            DMI_DESIG[bus_addr] = current_designation
            DMI_TYPE[bus_addr] = current_type or "Unknown"
    return DMI_DESIG, DMI_TYPE


# ------------------------- location & slot heuristics -------------------------

def riser_side(group: str) -> str:
    # Front/Rear by riser group
    if group in ("PCIE_3", "PCIE_4"):
        return "Front"
    if group in ("PCIE_1", "PCIE_2"):
        return "Rear"
    return "ERROR"


def riser_lr(group: str) -> str:
    # Left/Right mapping
    return {
        "PCIE_1": "Right",
        "PCIE_2": "Left",
        "PCIE_3": "Right",
        "PCIE_4": "Left",
    }.get(group, "ERROR")


def riser_group_from_desig(desig: str) -> str:
    m = re.match(r"^(PCIE_\d+)_.*", desig or "")
    return m.group(1) if m else ""


def inner_slot_from_dev_with_group(group: str, dev_hex: str) -> str:
    # Inversions:
    #   - PCIE_3 (Front-Right): dev 00 → Bottom, dev 01 → Top
    #   - PCIE_2 (Rear-Left):   dev 00 → Bottom, dev 01 → Top
    # Defaults (PCIE_1, PCIE_4): dev 00 → Top, dev 01 → Bottom
    mapping = {
        "PCIE_3": {"00": "Bottom", "01": "Top"},
        "PCIE_2": {"00": "Bottom", "01": "Top"},
        "PCIE_1": {"00": "Top",    "01": "Bottom"},
        "PCIE_4": {"00": "Top",    "01": "Bottom"},
    }
    return mapping.get(group, {}).get(dev_hex, "ERROR")


def gpu_label_from_key(key: str) -> str:
    # key like "PCIE_1_PCIE_1" → GPU indices
    table = {
        "PCIE_1_PCIE_1": "GPU1",
        "PCIE_1_PCIE_2": "GPU2",
        "PCIE_2_PCIE_1": "GPU5",
        "PCIE_2_PCIE_2": "GPU6",
        "PCIE_3_PCIE_1": "GPU3",
        "PCIE_3_PCIE_2": "GPU4",
        "PCIE_4_PCIE_1": "GPU7",
        "PCIE_4_PCIE_2": "GPU8",
    }
    return table.get(key, "ERROR")


# ------------------------------ GPU discovery ---------------------------------


def discover_gpu_bdfs():
    """
    Return a sorted list of NVIDIA GPU BDFs (no domain) by parsing lspci output in Python.
    We accept class codes [0300] (VGA) and [0302] (3D).
    """
    if shutil.which("lspci") is None:
        pytest.skip("pciutils not installed (sudo apt-get install -y pciutils)")

    # Prefer numeric IDs so vendor matches are stable
    # -Dnns prints: DOMAIN:BUS:DEV.F <class text> [classcode]: ... [VEN:DEV]
    txt = try_run(["lspci", "-Dnns", "10de:"]) or try_run(["lspci", "-Dnn"])
    if not txt:
        pytest.skip("lspci returned no output")

    bdfs = []
    for line in txt.splitlines():
        # Require NVIDIA vendor 10de
        if "[10de:" not in line.lower():
            continue

        # Extract BDF (domainful)
        m = re.match(r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])\s+(.*)$", line)
        if not m:
            continue
        domain, nodom, rest = m.group(1), m.group(2), m.group(3)

        # Accept class [0300] VGA or [0302] 3D; also tolerate class text containing those labels
        ok = ("[0300]" in rest) or ("[0302]" in rest) \
             or ("VGA compatible controller" in rest) or ("3D controller" in rest)

        if ok:
            bdfs.append(nodom)

    # Sort by bus, device, function
    def keyfn(s):
        bus, devfn = s.split(":")
        dev, fn = devfn.split(".")
        return (int(bus, 16), int(dev, 16), int(fn))

    return sorted(set(bdfs), key=keyfn)


# ------------------------------ main pytest test ------------------------------

def test_gpu_pcie_topology_and_links(capfd):
    """
    Print a PCIe link table matching your script and fail if any GPU is DOWNGRADED
    (i.e., running with fewer lanes than its capability).
    """
    gpus = discover_gpu_bdfs()
    if not gpus:
        pytest.skip("No NVIDIA GPUs detected by lspci")

    dmi_desig, dmi_type = parse_dmi_slots()

    # Print DMI table (for visibility)
    print(f"{'BusAddr':<15}  {'Designation':<12}  Type")
    print(f"{'-------':<15}  {'-----------':<12}  {'----'}")
    for addr in sorted(dmi_desig.keys()):
        print(f"{addr:<15}  {dmi_desig[addr]:<12}  {dmi_type.get(addr,'')}")
    print()

    rows = []
    downgraded = []

    for g in gpus:
        chain = get_chain_for_device(g)
        if len(chain) < 2:
            print(f"{g} | (no bridges found)")
            continue

        root_bdf = chain[0]              # root port (domainful)
        dmikey = dmi_key_from_bdf(root_bdf)
        slot = dmi_desig.get(dmikey, "-")
        verdict = "[OK]" if slot != "-" else "[MISS]"

        # Endpoint-first chain text
        rev = list(reversed(chain))
        chain_text = f"{g} <- {' '.join(rev)} | Root Port {dmikey} -> {slot} {verdict}"
        print(chain_text)

        # Find the last downstream bridge (exclude endpoint -> last prior element)
        last_bridge = chain[-2]
        _dom, _bus, dev, _fn = re.match(r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])$", last_bridge).groups()

        group = riser_group_from_desig(slot)
        side = riser_side(group)
        lr = riser_lr(group)
        inner = inner_slot_from_dev_with_group(group, dev)
        key = f"{group}_PCIE_{'1' if inner=='Top' else '2'}"
        gpulabel = gpu_label_from_key(key)

        cap_s, cap_w, sta_s, sta_w = get_link_fields(g)
        status = link_status_tag(cap_s, cap_w, sta_s, sta_w)
        name = get_gpu_short_name(g)

        # Save for table
        rows.append({
            "bdf": g, "name": name, "slot_name": f"{gpulabel} ({key})",
            "location": f"{side}-{lr}-{inner}", "status": status,
            "cap_s": cap_s or "?", "sta_s": sta_s or "?", "cap_w": cap_w or "?", "sta_w": sta_w or "?"
        })
        if status == "DOWNGRADED":
            downgraded.append(g)

    # Print table
    print()
    header = f"{'BUS:DEV:FN':<11} {'Name':<34} {'Slot':<24} {'Location':<18} {'Status':<12} {'CapGT/s':<8} {'StaGT/s':<8} {'CapWidth':<9} {'StaWidth':<9}"
    print(header)
    print(f"{'-'*11:<11} {'-'*4:<34} {'-'*4:<24} {'-'*9:<18} {'-'*6:<12} {'-'*7:<8} {'-'*7:<8} {'-'*8:<9} {'-'*8:<9}")
    for r in rows:
        line = f"{r['bdf']:<11} {r['name']:<34} {r['slot_name']:<24} {r['location']:<18} {r['status']:<12} {r['cap_s']:<8} {r['sta_s']:<8} {r['cap_w']:<9} {r['sta_w']:<9}"
        print(color_line(r["status"], line))

    # Make pytest output show the printed table
    capfd.readouterr()

    # Hard assertion: no lane-count downgrade
    assert not downgraded, f"Some GPUs are lane-DOWNGRADED: {', '.join(downgraded)}"

