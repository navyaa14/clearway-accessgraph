"""Deterministic incident state machine (Proof-of-Clearance protocol).

States:  OBSERVED -> ASSESSED -> ASSISTANCE_REQUESTED -> AWAITING_NEW_EVIDENCE
         -> REASSESSED -> RESOLVED | PARTIALLY_RESOLVED | REVIEW_REQUIRED | UNABLE_TO_VERIFY
No LLM or UI can move an incident; only these guarded transitions can.
A request for help NEVER implies clearance: closure requires a later, distinct,
quality-checked observation under a valid calibration whose independently
recomputed geometry supports "no obstruction observed".
"""
from __future__ import annotations

from dataclasses import dataclass

OPEN_STATES = {"OBSERVED", "ASSESSED", "ASSISTANCE_REQUESTED", "AWAITING_NEW_EVIDENCE", "REASSESSED",
               "PARTIALLY_RESOLVED", "REVIEW_REQUIRED", "UNABLE_TO_VERIFY"}
TERMINAL = {"RESOLVED"}

ALLOWED = {
    "OBSERVED": {"ASSESSED"},
    "ASSESSED": {"ASSISTANCE_REQUESTED", "REASSESSED", "RESOLVED"},
    "ASSISTANCE_REQUESTED": {"AWAITING_NEW_EVIDENCE", "REASSESSED", "RESOLVED"},
    "AWAITING_NEW_EVIDENCE": {"REASSESSED", "ASSISTANCE_REQUESTED", "RESOLVED"},
    "REASSESSED": {"RESOLVED", "PARTIALLY_RESOLVED", "REVIEW_REQUIRED", "UNABLE_TO_VERIFY", "AWAITING_NEW_EVIDENCE", "ASSESSED"},
    "PARTIALLY_RESOLVED": {"REASSESSED", "ASSISTANCE_REQUESTED", "RESOLVED"},
    "REVIEW_REQUIRED": {"REASSESSED", "ASSISTANCE_REQUESTED", "RESOLVED"},
    "UNABLE_TO_VERIFY": {"REASSESSED", "ASSISTANCE_REQUESTED", "RESOLVED"},
    "RESOLVED": set(),
}

# Evidence trust. Only media the backend itself captured from Ring (server clock)
# can verify a LIVE incident. Uploads, replays, synthetic renders and simulator
# events can only drive DEMO incidents.
# RING_API_EMULATOR exercises the identical server-side capture path (WHEP from the API host ClearWay is configured
# for), so it follows the live evidence rules; its media is always labelled as emulated, never as Ring.
TRUSTED_LIVE_SOURCES = {"RING_PLAYGROUND", "RING_LIVE", "RING_API_EMULATOR"}


def trust_of(media_source: str) -> str:
    return "LIVE" if media_source in TRUSTED_LIVE_SOURCES else "DEMO"


IMPROVEMENT_CM = 5.0  # clear-width gain that counts as partial progress (metric mode)
IMPROVEMENT_FRAC = 0.25  # relative overlap reduction that counts as partial progress (apparent mode)


class TransitionError(Exception):
    pass


def check(src: str, dst: str):
    if dst not in ALLOWED.get(src, set()):
        raise TransitionError(f"transition {src} -> {dst} not permitted")


def severity_metric(analysis: dict) -> tuple[float | None, str]:
    """Scalar for progress comparison: metric clear width (higher=better) or -overlap fraction."""
    pid = analysis.get("primary_object")
    obj = next((o for o in analysis.get("objects", []) if o["id"] == pid), None)
    if not obj or "nominal" not in obj:
        return None, "none"
    cw = obj["nominal"].get("clear_width")
    if cw:
        return float(cw["min_clear_width"]), "clear_width_cm"
    return -float(obj["nominal"]["fraction_of_footprint"]), "neg_overlap_fraction"


@dataclass
class Verdict:
    accepted: bool
    disposition: str  # NEW_EVIDENCE | STALE | DUPLICATE | PRE_ASSISTANCE
    next_state: str | None
    reasons: list
    counts_as_clear: bool = False  # True only for an independent clear observation (advances the closure streak)


def verify_new_evidence(incident: dict, obs: dict, analysis: dict, seen_hashes: set[str], close_after: int = 1,
                        min_clear_separation_s: float = 0.0) -> Verdict:
    """Apply the verification conditions to a candidate observation for an open incident."""
    reasons = []
    if obs["frame_sha256"] in seen_hashes:
        return Verdict(False, "DUPLICATE", None, ["identical frame content already used as evidence for this incident; not new evidence"])
    if (incident.get("namespace") or "DEMO") == "LIVE":
        if obs.get("trust") != "LIVE" or obs.get("chronology") != "SERVER_CAPTURED":
            return Verdict(False, "UNTRUSTED_SOURCE", None, [
                f"{obs.get('media_source', 'this source')} with {obs.get('chronology', 'unknown')} timing cannot verify a live Ring incident; "
                "only frames the server captured from the Ring camera can (a manual close remains available)"])
    # Evidence ORIGIN is separate from capture trust: RING and RING_API_EMULATOR are both server-captured
    # ("LIVE" trust), but they are different technologies and must never verify each other's incidents.
    inc_origin, obs_origin = incident.get("evidence_origin"), obs.get("evidence_origin")
    if inc_origin and obs_origin and inc_origin != obs_origin:
        if inc_origin == "RING":
            return Verdict(False, "ORIGIN_MISMATCH", None, [
                f"evidence origin {obs_origin} cannot verify an incident opened on genuine Ring evidence; "
                "only genuine Ring captures can (a manual close remains available)"])
        if (incident.get("namespace") or "DEMO") == "LIVE":
            # e.g. a deployment switched from the emulator to genuine Ring: never silently reuse or upgrade
            # earlier clearance evidence — a person must review, and the clear streak does not carry over.
            return Verdict(True, "ORIGIN_MISMATCH", "REVIEW_REQUIRED", [
                f"evidence origin changed during this incident ({inc_origin} → {obs_origin}); earlier evidence is not "
                "comparable, so the incident needs review (or a new incident) instead of automatic closure"])
    if incident.get("latest_captured_at") and obs["captured_at"] <= incident["latest_captured_at"]:
        return Verdict(False, "STALE", None, [f"observation captured at {obs['captured_at']:.0f} is not later than the incident's latest evidence ({incident['latest_captured_at']:.0f}); recorded but cannot change status"])
    cls = analysis["classification"]
    cal = analysis.get("calibration_check", {})
    q = analysis.get("quality", {})
    if cls == "UNABLE_TO_VERIFY":
        return Verdict(True, "NEW_EVIDENCE", "UNABLE_TO_VERIFY", ["new observation cannot support a judgement: " + analysis.get("explanation", "")])
    if cal.get("valid") is False or not q.get("usable", False):
        return Verdict(True, "NEW_EVIDENCE", "UNABLE_TO_VERIFY", ["calibration invalid or image quality insufficient"])
    if incident.get("calibration_version") and obs.get("calibration_version") and obs["calibration_version"] != incident["calibration_version"]:
        return Verdict(True, "NEW_EVIDENCE", "REVIEW_REQUIRED", [
            f"camera calibration changed since the incident opened ({incident['calibration_version']} → {obs['calibration_version']}); "
            "measurements are not directly comparable, so a person must confirm"])
    if cls == "REVIEW_REQUIRED":
        return Verdict(True, "NEW_EVIDENCE", "REVIEW_REQUIRED", ["recomputed geometry is inconclusive under uncertainty"])
    if cls == "NO_OBSTRUCTION_OBSERVED":
        prev = int(incident.get("clear_streak") or 0)
        if prev >= 1 and (incident.get("namespace") or "DEMO") == "LIVE":
            # Two frames with different hashes are not necessarily two independent physical observations:
            # a later clear frame counts only if it comes from a separate capture session, sufficiently later.
            same_session = bool(obs.get("capture_session")) and obs.get("capture_session") == incident.get("last_clear_session")
            gap = obs["captured_at"] - float(incident.get("last_clear_at") or 0)
            if same_session or gap < min_clear_separation_s:
                why = "it comes from the same capture session" if same_session else f"it was captured only {gap:.0f} s after the previous one"
                return Verdict(True, "NEW_EVIDENCE", "AWAITING_NEW_EVIDENCE", [
                    f"clear, but not independent of the previous clear observation ({why}; policy needs a separate capture "
                    f"session at least {min_clear_separation_s:.0f} s later) — does not count toward closure"])
        streak = prev + 1
        if streak >= close_after:
            return Verdict(True, "NEW_EVIDENCE", "RESOLVED", [f"fresh observation independently supports no obstruction in this camera view "
                                                              f"({streak} independent clear observation(s); policy requires {close_after})"], True)
        return Verdict(True, "NEW_EVIDENCE", "AWAITING_NEW_EVIDENCE", [f"clear observation {streak}/{close_after}; awaiting an independent confirmation"], True)
    # still a potential obstruction: partial progress?
    m, kind = severity_metric(analysis)
    base = incident.get("last_metric")
    if m is not None and base is not None:
        improved = (m - base >= IMPROVEMENT_CM) if kind == "clear_width_cm" else (base < 0 and (m - base) >= IMPROVEMENT_FRAC * abs(base))
        if improved:
            unit = "cm" if kind == "clear_width_cm" else ""
            reasons.append(f"obstruction reduced ({abs(base):.2f}->{abs(m):.2f}{unit}) but the route is still potentially obstructed")
            return Verdict(True, "NEW_EVIDENCE", "PARTIALLY_RESOLVED", reasons)
    return Verdict(True, "NEW_EVIDENCE", "AWAITING_NEW_EVIDENCE" if incident["state"] != "ASSESSED" else "ASSESSED",
                   ["potential obstruction persists in the new observation; incident stays open"])
