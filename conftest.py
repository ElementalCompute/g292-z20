import os, yaml, pytest

@pytest.fixture(scope="session")
def cfg():
    with open("tests/config.yaml", "r") as f:
        return yaml.safe_load(f)
