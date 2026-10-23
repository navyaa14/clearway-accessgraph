import os
import sys

# Faster CI: fewer Monte-Carlo samples (production default 160). Override with CLEARWAY_MC_SAMPLES=160 for a full-fidelity run.
os.environ.setdefault("CLEARWAY_MC_SAMPLES", "96")
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="cw-test-")
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", Path(d))
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "ring_access_token", None)
    monkeypatch.setattr(settings, "ring_refresh_token", None)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def ref():
    from app.fixtures import scene
    return scene("reference")


def H(hh="hh_b", member=None):
    h = {"X-Household-Token": f"demo-{hh}-token"}
    if member:
        h["X-Member-Token"] = f"demo-{member}-token"
    return h
