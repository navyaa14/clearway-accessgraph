"""Reproducible evaluation suite.

Part A: randomised labelled set (synthetic renders with exact ground truth),
        evaluated under three household configurations.
Part B: the 15 named scenarios from the brief, executed against the real
        pipeline / API, each with an explicit pass/fail.

Usage:  python -m eval.run_eval [--n 100] [--workers 2]
Writes: eval/results/latest.json and eval/results/REPORT.md

All numbers are measured on SYNTHETIC renders only. They say nothing about
accuracy on real Ring footage.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import fixtures as FX  # noqa: E402
from app.vision import geometry as G  # noqa: E402
from app.vision.synth import Box, SceneSpec, render, world_poly_to_image  # noqa: E402

OUT = ROOT / "eval" / "results"
CONFIGS = [("hh_a", True), ("hh_b", True), ("hh_b", False)]
CLASSES = ["POTENTIAL_OBSTRUCTION", "NO_OBSTRUCTION_OBSERVED", "REVIEW_REQUIRED", "UNABLE_TO_VERIFY"]
_REF = None


def ref():
    global _REF
    if _REF is None:
        _REF = FX.scene("reference")
    return _REF


def random_box(rng) -> Box:
    col = np.array([95, 145, 190]) * rng.uniform(0.8, 1.15) + rng.normal(0, 8, 3)
    return Box(x=rng.uniform(40, 270), y=rng.uniform(130, 470), w=rng.uniform(30, 60), d=rng.uniform(25, 50),
               h=rng.uniform(15, 45), rot_deg=rng.uniform(-30, 30), color=tuple(np.clip(col, 30, 240)))


def eval_scene(i: int) -> list[dict]:
    from app.vision.pipeline import analyze
    rng = np.random.default_rng(1000 + i)
    box = random_box(rng)
    spec = SceneSpec(boxes=[box], gain=float(rng.uniform(0.88, 1.12)), noise=float(rng.uniform(2, 4)), seed=i)
    img = render(spec)
    rows = []
    for hh, metric in CONFIGS:
        gt = FX.ground_truth(box, hh, metric)
        t = time.perf_counter()
        res, _ = analyze(img, FX.profile_for(hh, metric), ref())
        lat = (time.perf_counter() - t) * 1000
        prim = next((o for o in res["objects"] if o["id"] == res.get("primary_object")), None)
        row = {"scene": i, "household": hh, "mode": "METRIC" if metric else "APPARENT", "gt_obstructed": gt["obstructed"],
               "borderline": gt["borderline"], "pred": res["classification"], "p": prim.get("p_obstructed") if prim else None,
               "latency_ms": round(lat, 1), "n_objects": len(res["objects"]),
               "box": {"x": round(box.x, 1), "y": round(box.y, 1), "w": round(box.w, 1), "d": round(box.d, 1), "h": round(box.h, 1), "rot": round(box.rot_deg, 1)}}
        if prim and prim.get("footprint"):
            fp_pred = G.poly([[x * 960, y * 720] for x, y in prim["footprint"]])
            fp_true = G.poly(world_poly_to_image(FX.CAM, box.base_corners()))
            inter = fp_pred.intersection(fp_true).area
            row["footprint_iou"] = round(inter / max(fp_pred.union(fp_true).area, 1e-9), 4)
            if metric:
                cw = (prim.get("nominal") or {}).get("clear_width")
                fp_w = Polygon_or_none(box)
                gt_inter = fp_w.intersects(G.poly(FX.ROUTE_WORLD[FX.HOUSEHOLDS[hh]["route_key"]]))
                if cw and gt_inter:
                    row["clear_width_err_cm"] = round(cw["min_clear_width"] - gt["clear_width_cm"], 2)
            else:
                row["overlap_frac_err"] = round(prim["nominal"]["fraction_of_footprint"] - gt["fraction"], 4)
        rows.append(row)
    return rows


def Polygon_or_none(box):
    return G.poly(box.base_corners())


def summarise(rows: list[dict]) -> dict:
    def block(rs):
        clean = [r for r in rs if not r["borderline"]]
        pos = [r for r in clean if r["gt_obstructed"]]
        neg = [r for r in clean if not r["gt_obstructed"]]
        tp = sum(r["pred"] == "POTENTIAL_OBSTRUCTION" for r in pos)
        fp = sum(r["pred"] == "POTENTIAL_OBSTRUCTION" for r in neg)
        false_clear = sum(r["pred"] == "NO_OBSTRUCTION_OBSERVED" for r in pos)
        flagged = sum(r["pred"] in ("POTENTIAL_OBSTRUCTION", "REVIEW_REQUIRED") for r in pos)
        unresolved = sum(r["pred"] in ("REVIEW_REQUIRED", "UNABLE_TO_VERIFY") for r in rs)
        conf = {t: {c: 0 for c in CLASSES} for t in ("obstructed", "clear", "borderline")}
        for r in rs:
            conf["borderline" if r["borderline"] else ("obstructed" if r["gt_obstructed"] else "clear")][r["pred"]] += 1
        cwe = [abs(r["clear_width_err_cm"]) for r in rs if "clear_width_err_cm" in r]
        cwb = [r["clear_width_err_cm"] for r in rs if "clear_width_err_cm" in r]
        ious = [r["footprint_iou"] for r in rs if "footprint_iou" in r]
        lat = [r["latency_ms"] for r in rs]
        return {
            "n": len(rs), "n_borderline": len(rs) - len(clean), "n_positive": len(pos), "n_negative": len(neg),
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / len(pos), 4) if pos else None,
            "recall_including_review": round(flagged / len(pos), 4) if pos else None,
            "false_clear_rate": round(false_clear / len(pos), 4) if pos else None,
            "false_alarm_rate": round(fp / len(neg), 4) if neg else None,
            "unresolved_rate": round(unresolved / len(rs), 4) if rs else None,
            "clear_width_mae_cm": round(float(np.mean(cwe)), 2) if cwe else None,
            "clear_width_bias_cm": round(float(np.mean(cwb)), 2) if cwb else None,
            "clear_width_p95_abs_err_cm": round(float(np.percentile(cwe, 95)), 2) if cwe else None,
            "footprint_iou_mean": round(float(np.mean(ious)), 3) if ious else None,
            "latency_p50_ms": round(float(np.percentile(lat, 50)), 1) if lat else None,
            "latency_p95_ms": round(float(np.percentile(lat, 95)), 1) if lat else None,
            "confusion": conf,
        }

    out = block(rows)
    out["by_config"] = {f"{hh}/{m}": block([r for r in rows if r["household"] == hh and r["mode"] == m]) for hh, m in
                        [("hh_a", "METRIC"), ("hh_b", "METRIC"), ("hh_b", "APPARENT")]}
    return out


# ------------------------------------------------------------------ scenarios
def run_scenarios() -> list[dict]:
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.ring import webhook as WH
    from app.vision.pipeline import analyze

    settings.data_dir = Path(tempfile.mkdtemp(prefix="cw-eval-"))
    settings.ring_access_token = settings.ring_refresh_token = None
    from app.main import app

    S: list[dict] = []

    def add(i, name, expected, observed, ok):
        S.append({"id": i, "name": name, "expected": expected, "observed": observed, "pass": bool(ok)})

    def cls(img, hh="hh_b", metric=True):
        return analyze(img, FX.profile_for(hh, metric), ref())[0]

    r = cls(render(SceneSpec(boxes=[Box(45, 230)])))
    add(1, "Parcel outside the marked route", "NO_OBSTRUCTION_OBSERVED", r["classification"], r["classification"] == "NO_OBSTRUCTION_OBSERVED")
    r = cls(FX.scene("centre_block"))
    add(2, "Parcel visibly overlapping the route", "POTENTIAL_OBSTRUCTION", r["classification"], r["classification"] == "POTENTIAL_OBSTRUCTION")
    a, b = cls(FX.scene("initial"), "hh_a"), cls(FX.scene("initial"), "hh_b")
    add(3, "Identical frame, different household route/need", "A: NO_OBSTRUCTION, B: POTENTIAL", f"A: {a['classification']}, B: {b['classification']}",
        a["classification"] == "NO_OBSTRUCTION_OBSERVED" and b["classification"] == "POTENTIAL_OBSTRUCTION")

    H = {"X-Household-Token": "demo-hh_b-token"}
    with TestClient(app) as c:
        def emit(scene, **kw):
            return c.post("/api/households/hh_b/simulator/emit", json={"scene": scene, **kw}, headers=H).json()

        def inc():
            lst = c.get("/api/households/hh_b/incidents", headers=H).json()
            return c.get(f"/api/households/hh_b/incidents/{lst[0]['id']}", headers=H).json()

        emit("initial")
        i0 = inc()
        d = c.post(f"/api/households/hh_b/incidents/{i0['id']}/assistance/draft", headers=H).json()
        # 12: unauthorised attempts first
        s1 = c.post(f"/api/households/hh_b/actions/{d['id']}/send", headers=H).status_code
        s2 = c.post(f"/api/households/hh_b/actions/{d['id']}/approve", json={"member_id": "hh_b_resident"},
                    headers={**H, "X-Member-Token": "demo-hh_b_guest-token"}).status_code  # guest impersonating the resident
        add(12, "Assistance request without authorisation (incl. impersonation)", "send 403, impersonated approval 403, nothing delivered",
            f"send {s1}, impersonated approve {s2}, inbox {len(c.get('/api/households/hh_b/inbox', headers=H).json())}",
            s1 == 403 and s2 == 403 and not c.get("/api/households/hh_b/inbox", headers=H).json())
        c.post(f"/api/households/hh_b/actions/{d['id']}/approve", headers={**H, "X-Member-Token": "demo-hh_b_resident-token"})
        c.post(f"/api/households/hh_b/actions/{d['id']}/send", headers=H)
        emit("partial_move")
        st = inc()["state"]
        add(4, "Parcel partially moved but overlap remains", "PARTIALLY_RESOLVED (not closed)", st, st == "PARTIALLY_RESOLVED")
        # delayed event: a distinct (clear) frame captured an hour earlier than the incident's latest evidence
        r = emit("reference", captured_offset_s=-3600)
        st = inc()["state"]
        disp = r["observation"]["disposition"]
        add(11, "Old event arrives after a newer observation", "STALE, state unchanged", f"{disp}, {st}", disp == "STALE" and st == "PARTIALLY_RESOLVED")
        emit("moved_to_zone")
        i1 = inc()
        add(5, "Parcel repositioned into the delivery zone", "RESOLVED (verified)", f"{i1['state']} / {i1['resolution']}",
            i1["state"] == "RESOLVED" and i1["resolution"] == "VERIFIED_NO_OBSTRUCTION_OBSERVED")
        r1 = emit("centre_block", event_id="dup-evt")
        r2 = emit("centre_block", event_id="dup-evt")  # same event id delivered again
        add(10, "Duplicate event delivered twice", "second delivery ignored", f"first processed={r1.get('processing')}, second duplicate={r2.get('duplicate')}",
            r1.get("processing") and r2.get("duplicate"))
        body = json.dumps({"id": "sig-1", "type": "motion_detected", "data": {"attributes": {"clearway_household": "hh_b", "clearway_fixture": "reference"}}}).encode()
        bad = c.post("/webhooks/simulator", content=body, headers={"X-Signature": "sha256=" + "0" * 64}).status_code
        good = c.post("/webhooks/simulator", content=body, headers={"X-Signature": WH.sign(settings.simulator_secret, body)}).status_code
        add(15, "Valid vs invalid webhook signature", "valid 200, invalid 401", f"valid {good}, invalid {bad}", good == 200 and bad == 401)
        from app.main import STATE
        s = STATE["svc"]
        s.provision_live_household("hh_live", "eval-live", "eval-approver", None, "ring-dev-eval")
        L = {"X-Household-Token": "eval-live"}
        rs = c.get("/api/households/hh_live/ring/devices", headers=L)
        add(13, "Ring stream/API unavailable (no credentials here)", "explicit 503 for the live household, app keeps working",
            f"{rs.status_code}: {str(rs.json().get('detail', ''))[:60]}", rs.status_code == 503)

        # ---- audit regressions (policy tests: synthetic pixels through the server-side Ring ingest path)
        s.db.update("cameras", "id", "hh_live_ring", {"authorized": 1})
        s.save_calibration("hh_live", "hh_live_ring", FX.profile_for("hh_b"), FX.scene("reference"), "RING_PLAYGROUND")
        s.ingest("hh_live", "hh_live_ring", FX.scene("initial"), "RING_PLAYGROUND", "RING_API_CAPTURE")
        up = c.post("/api/households/hh_live/observations/upload", headers=L,
                    files={"file": ("r.png", cv2.imencode(".png", FX.scene("moved_to_zone", seed=77))[1].tobytes(), "image/png")},
                    data={"camera_id": "hh_live_ring", "source": "RECORDED_REPLAY", "captured_at": str(time.time() + 99999)})
        st_live = c.get("/api/households/hh_live/incidents", headers=L).json()[0]["state"]
        add(16, "Replay with fabricated future timestamp vs live Ring incident", "rejected (403), live incident stays open",
            f"upload {up.status_code}, incident {st_live}", up.status_code == 403 and st_live == "ASSESSED")
        from app.config import settings as _S
        _S.live_min_clear_separation_s = 3.0  # shortened from the 60 s default so the eval runs quickly
        s.ingest("hh_live", "hh_live_ring", FX.scene("moved_to_zone", seed=78), "RING_PLAYGROUND", "RING_API_CAPTURE", extra={"capture_session": "eval-A"})
        st1 = c.get("/api/households/hh_live/incidents", headers=L).json()[0]["state"]
        s.ingest("hh_live", "hh_live_ring", FX.scene("moved_to_zone", seed=79), "RING_PLAYGROUND", "RING_API_CAPTURE", extra={"capture_session": "eval-A"})
        st_same = c.get("/api/households/hh_live/incidents", headers=L).json()[0]["state"]
        add(22, "Second clear frame from the SAME capture session", "does not close the live incident", st_same, st_same != "RESOLVED")
        time.sleep(3.2)
        s.ingest("hh_live", "hh_live_ring", FX.scene("moved_to_zone", seed=80), "RING_PLAYGROUND", "RING_API_CAPTURE", extra={"capture_session": "eval-B"})
        st2 = c.get("/api/households/hh_live/incidents", headers=L).json()[0]["state"]
        add(17, "Live closure needs two independent server-captured clear observations (separate sessions, time-separated)",
            "open after 1st, RESOLVED after an independent 2nd", f"{st1} -> {st2}", st1 != "RESOLVED" and st2 == "RESOLVED")
        A = {"X-Household-Token": "demo-hh_a-token"}
        emit_a = c.post("/api/households/hh_a/simulator/emit", json={"scene": "centre_block"}, headers=A).json()["observation"]
        c.post("/api/households/hh_b/retention/purge?days=0", headers={**H, "X-Member-Token": "demo-hh_b_resident-token"})
        fa = c.get(emit_a["frame_url"]).status_code
        add(18, "Household B retention purge vs household A evidence", "household A frame still served (200)", str(fa), fa == 200)
        leaked = "token" in emit_a["frame_url"] or c.get(emit_a["frame_url"].split("?")[0] + "?token=demo-hh_a-token").status_code != 403
        add(19, "Credentials never accepted in media URLs", "signed URLs only", "token accepted" if leaked else "signed only", not leaked)

    r = cls(FX.scene("initial", obscure=0.55))
    add(6, "Camera view obscured", "UNABLE_TO_VERIFY", r["classification"], r["classification"] == "UNABLE_TO_VERIFY")
    r1, r2 = cls(FX.scene("initial", gain=0.14, noise=6)), cls(FX.scene("initial", blur_sigma=7))
    add(7, "Low-light or blurry observation", "UNABLE_TO_VERIFY (both)", f"low-light {r1['classification']}, blur {r2['classification']}",
        r1["classification"] == r2["classification"] == "UNABLE_TO_VERIFY")
    r = cls(FX.scene("initial", camera="moved"))
    add(8, "Camera displaced after calibration", "UNABLE_TO_VERIFY (calibration DISPLACED)", f"{r['classification']} ({r['calibration_check']['status']})",
        r["classification"] == "UNABLE_TO_VERIFY" and r["calibration_check"]["status"] == "DISPLACED")
    r = cls(render(SceneSpec(boxes=[Box(150, 62, h=40)])))
    add(9, "Object detected but ground contact unreliable", "REVIEW_REQUIRED", r["classification"], r["classification"] == "REVIEW_REQUIRED")
    img = FX.scene("reference").copy()
    for y in range(260, 600, 40):
        cv2.line(img, (430, y), (470, y), (240, 240, 240), 2)
    r = cls(img)
    add(20, "Unexplained change on the route, no segmentable object", "REVIEW_REQUIRED (not clear)", r["classification"], r["classification"] == "REVIEW_REQUIRED")
    r = analyze(FX.scene("reference"), FX.profile_for("hh_b"), None)[0]
    add(21, "No clear-scene reference (colour-prior fallback)", "REVIEW_REQUIRED (advisory only)", r["classification"], r["classification"] == "REVIEW_REQUIRED")
    r = cls(render(SceneSpec(boxes=[Box(213.5, 300)])))
    p = r["objects"][0]["p_obstructed"] if r["objects"] else None
    add(14, "Uncertainty straddles decision threshold (true clear width = 91.0 cm = requirement)", "REVIEW_REQUIRED", f"{r['classification']} (p={p})",
        r["classification"] == "REVIEW_REQUIRED")
    return sorted(S, key=lambda s: s["id"])


def report_md(res: dict) -> str:
    R = res["random_set"]
    lines = [f"# ClearWay evaluation report", "", f"Generated {res['generated_at']} · pipeline `{res['pipeline_version']}`", "",
             f"> {res['disclaimer']}", "", "## A. Randomised labelled set", "",
             f"{R['n']} analyses ({res['n_scenes']} rendered scenes × 3 household configurations), {R['n_borderline']} borderline cases excluded from precision/recall.", "",
             "| Config | n | Precision | Recall | Recall incl. review | False-clear rate | False-alarm rate | Unresolved | Clear-width MAE (cm) | Footprint IoU | p50 ms |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, b in [("ALL", R)] + list(R["by_config"].items()):
        lines.append(f"| {k} | {b['n']} | {b['precision']} | {b['recall']} | {b['recall_including_review']} | {b['false_clear_rate']} | {b['false_alarm_rate']} | {b['unresolved_rate']} | {b['clear_width_mae_cm']} | {b['footprint_iou_mean']} | {b['latency_p50_ms']} |")
    lines += ["", "### Confusion matrix (all configurations)", "", "| truth \\ predicted | " + " | ".join(CLASSES) + " |", "|---|" + "---|" * len(CLASSES)]
    for t, row in R["confusion"].items():
        lines.append(f"| {t} | " + " | ".join(str(row[c]) for c in CLASSES) + " |")
    lines += ["", "### Explicit failure cases", ""]
    fails = res["failures"]
    if not fails:
        lines.append("None among non-borderline cases.")
    for f in fails[:25]:
        lines.append(f"- scene {f['scene']} {f['household']}/{f['mode']}: truth {'obstructed' if f['gt_obstructed'] else 'clear'}, predicted {f['pred']} (p={f['p']}), box {f['box']}")
    lines += ["", "## B. Named scenarios", "", "| # | Scenario | Expected | Observed | Result |", "|---|---|---|---|---|"]
    for s in res["scenarios"]:
        lines.append(f"| {s['id']} | {s['name']} | {s['expected']} | {s['observed']} | {'PASS' if s['pass'] else 'FAIL'} |")
    lines += ["", "## Definitions", "",
              "- Precision = obstructions predicted that are truly obstructed / all predicted obstructions (non-borderline).",
              "- Recall = truly obstructed cases predicted POTENTIAL_OBSTRUCTION; REVIEW_REQUIRED counts as a miss. 'Recall incl. review' counts it as flagged.",
              "- False-clear rate = truly obstructed cases predicted NO_OBSTRUCTION_OBSERVED (the safety-critical error).",
              "- Borderline = true clear width within ±6 cm of the requirement, or true footprint within 3 cm of the route (metric); 1–6% overlap (apparent).",
              "- Clear-width error is measured only where the true footprint intersects the route."]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    args = ap.parse_args()
    t0 = time.time()
    rows = []
    with ProcessPoolExecutor(args.workers) as ex:
        for rs in ex.map(eval_scene, range(args.n)):
            rows.extend(rs)
    print(f"random set: {len(rows)} analyses in {time.time() - t0:.0f}s")
    summary = summarise(rows)
    failures = [r for r in rows if not r["borderline"] and ((r["gt_obstructed"] and r["pred"] == "NO_OBSTRUCTION_OBSERVED") or (not r["gt_obstructed"] and r["pred"] == "POTENTIAL_OBSTRUCTION"))]
    scen = run_scenarios()
    from app.vision.pipeline import PIPELINE_VERSION
    res = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "pipeline_version": PIPELINE_VERSION,
        "disclaimer": "Measured on procedurally rendered SYNTHETIC scenes with exact ground truth (single cardboard box, fixed porch, CPU). "
                      "Not validated on real Ring footage; real-world accuracy is unknown.",
        "n_scenes": args.n, "random_set": summary, "failures": failures, "scenarios": scen,
        "runtime_s": round(time.time() - t0, 1), "cpu_count": os.cpu_count(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(res, indent=1, default=float))
    (OUT / "rows.json").write_text(json.dumps(rows, default=float))
    (OUT / "REPORT.md").write_text(report_md(res))
    print(json.dumps({k: summary[k] for k in ("precision", "recall", "recall_including_review", "false_clear_rate", "unresolved_rate", "clear_width_mae_cm", "footprint_iou_mean", "latency_p50_ms")}, indent=1))
    print(f"scenarios: {sum(s['pass'] for s in scen)}/{len(scen)} pass")
    for s in scen:
        if not s["pass"]:
            print("FAIL", s)


if __name__ == "__main__":
    main()
