# tests/test_cpu_min.py
from hw import Cmd  # helper to run shell commands

def _cpu_model():
    """Return CPU model from lscpu (e.g., 'AMD EPYC 7xx...')."""
    out = Cmd.out("lscpu")
    for line in out.splitlines():
        if line.lower().startswith("model name:"):
            return line.split(":", 1)[1].strip()
    # Fallback: unexpected format — return full output for debugging
    return out.strip()

def test_cpu_model(cfg):
    """Check that lscpu model contains the expected substring from config.yaml."""
    expected_substr = cfg["cpu"]["model_contains"]
    model = _cpu_model()
    assert expected_substr in model, (
        f"CPU model mismatch.\nExpected substring: {expected_substr}\nGot: {model}"
    )

