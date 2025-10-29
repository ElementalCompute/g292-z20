# tests/test_nics.py
"""
Detect exactly two *physical* Mellanox ConnectX-5 cards, and warn if:
  - a card exposes multiple PCI functions (e.g., .0 and .1 -> dual-port/personality)
  - any of the functions report as Infiniband (0207) instead of Ethernet (0200)

We derive physical cards by collapsing BDF functions:
  01:00.0 and 01:00.1 -> one physical card at 01:00
"""

import os
import re
import shutil
import subprocess
import warnings

import pytest


# You can drive the expected card count from config.yaml if you prefer.
EXPECTED_CARDS = 2  # physical ConnectX-5 cards


def _run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def _lspci_lines():
    if shutil.which("lspci") is None:
        pytest.skip("pciutils not installed (sudo apt-get install -y pciutils)")
    out = _run(["lspci", "-nn"])
    # Match only Mellanox ConnectX-5 (any variant, e.g. 'ConnectX-5', 'ConnectX-5 Ex')
    lines = [ln for ln in out.splitlines()
             if re.search(r"Mellanox", ln, re.I) and re.search(r"ConnectX-5", ln, re.I)]
    return lines


def _parse_cards(lines):
    """
    Return:
      cards: dict[ 'BB:DD' ] = {
          'bdfs': ['BB:DD.F', ...],
          'classes': ['Ethernet controller [0200]', 'Infiniband controller [0207]', ...],
          'raw': [original lspci lines ...]
      }
    """
    cards = {}
    for ln in lines:
        # Example:
        # 01:00.0 Infiniband controller [0207]: Mellanox Technologies MT28800 Family [ConnectX-5 Ex] [15b3:1019]
        m = re.match(r"^([0-9a-fA-F]{2}:[0-9a-fA-F]{2})\.([0-7])\s+(.+)$", ln)
        if not m:
            # fallback for domains like 0000:01:00.0 (rare on some lspci formats)
            m2 = re.match(r"^[0-9a-fA-F]{4}:([0-9a-fA-F]{2}:[0-9a-fA-F]{2})\.([0-7])\s+(.+)$", ln)
            if not m2:
                continue
            base, func, rest = m2.group(1), m2.group(2), m2.group(3)
        else:
            base, func, rest = m.group(1), m.group(2), m.group(3)

        bdf_full = f"{base}.{func}"
        # Grab the controller class text e.g. "Ethernet controller [0200]" or "Infiniband controller [0207]"
        cls = rest.split(":", 1)[0].strip()

        entry = cards.setdefault(base, {"bdfs": [], "classes": [], "raw": []})
        entry["bdfs"].append(bdf_full)
        entry["classes"].append(cls)
        entry["raw"].append(ln)

    return cards


def _format_cards(cards):
    parts = []
    for base, info in sorted(cards.items()):
        parts.append(f"{base}: funcs={','.join(sorted(info['bdfs']))}  classes={'; '.join(info['classes'])}")
    return "\n".join(parts)


def test_two_physical_connectx5_cards_and_warn_on_dual_port_or_ib():
    lines = _lspci_lines()
    cards = _parse_cards(lines)

    # Assert physical count
    physical_count = len(cards)
    assert physical_count == EXPECTED_CARDS, (
        f"Expected {EXPECTED_CARDS} physical ConnectX-5 cards, found {physical_count}.\n"
        f"Details:\n{_format_cards(cards)}\n\nRaw matches:\n" + "\n".join(lines)
    )

    # Warn if any card exposes multiple functions (likely dual-port/personality)
    multi_func = {base: info for base, info in cards.items() if len(set(info["bdfs"])) > 1}
    if multi_func:
        warnings.warn(
            "Some ConnectX-5 cards expose multiple PCI functions (dual-port/personality firmware):\n"
            + _format_cards(multi_func),
            UserWarning,
        )

    # Warn if any function is Infiniband rather than Ethernet
    ib_cards = {base: info for base, info in cards.items()
                if any("Infiniband controller" in c for c in info["classes"])}
    if ib_cards:
        warnings.warn(
            "Some ConnectX-5 functions are Infiniband (class 0207). "
            "If you expect Ethernet, adjust firmware/personality:\n"
            + _format_cards(ib_cards),
            UserWarning,
        )

