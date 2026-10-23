"""Runs only when labelled real photographs are present (eval/real_images/manifest.json)."""
from pathlib import Path

import pytest

MANIFEST = Path(__file__).resolve().parents[1] / "eval" / "real_images" / "manifest.json"


@pytest.mark.skipif(not MANIFEST.exists(), reason="no real-photo set yet: see eval/real_images/README.md")
def test_real_photos_have_no_false_clears():
    from eval.run_real import run
    r = run()
    assert r["n"] > 0
    assert r["false_clear"] == 0, [x for x in r["rows"] if x["false_clear"]]
