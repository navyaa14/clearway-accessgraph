"""Evaluate labelled REAL photographs (see eval/real_images/README.md). Writes eval/results/real_images.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DIR = ROOT / "eval" / "real_images"
EXPECT = {"obstructed": {"POTENTIAL_OBSTRUCTION"}, "clear": {"NO_OBSTRUCTION_OBSERVED"}, "unverifiable": {"UNABLE_TO_VERIFY", "REVIEW_REQUIRED"}}


def run() -> dict | None:
    from app.vision.pipeline import analyze
    mf = DIR / "manifest.json"
    if not mf.exists():
        return None
    m = json.loads(mf.read_text())
    rows = []
    for case in m["cases"]:
        cam = m["cameras"][case["camera"]]
        img, ref = cv2.imread(str(DIR / case["image"])), cv2.imread(str(DIR / cam["reference"]))
        if img is None or ref is None:
            raise FileNotFoundError(case)
        prof = {"version": f"real-{case['camera']}", **{k: cam.get(k) for k in ("route", "drop_zone", "floor_points_img", "floor_points_world", "clearance_cm")}}
        res, _ = analyze(img, prof, ref)
        rows.append({**case, "pred": res["classification"], "correct": res["classification"] in EXPECT[case["label"]],
                     "false_clear": case["label"] == "obstructed" and res["classification"] == "NO_OBSTRUCTION_OBSERVED",
                     "explanation": res["explanation"]})
    out = {"n": len(rows), "correct": sum(r["correct"] for r in rows), "false_clear": sum(r["false_clear"] for r in rows), "rows": rows,
           "note": "Real photographs supplied by the user; small sets are indicative only."}
    (ROOT / "eval" / "results").mkdir(parents=True, exist_ok=True)
    (ROOT / "eval" / "results" / "real_images.json").write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    r = run()
    print("no eval/real_images/manifest.json — add labelled photos first" if r is None else json.dumps({k: r[k] for k in ("n", "correct", "false_clear")}))
