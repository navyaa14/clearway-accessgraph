"""Stage 3: optional Amazon Bedrock escalation for grounded explanations.

Only called when AWS_ENABLED=true AND credentials/model access work. It never
changes a classification, geometry or incident state; its text is stored
separately as `llm_explanation` with model id, latency and token usage.
When disabled, callers get an explicit 'disabled' status (no fake output).
"""
from __future__ import annotations

import json
import time

from .config import settings

PROMPT = (
    "You are assisting an accessibility tool. A deterministic computer-vision pipeline has already measured the scene. "
    "Using ONLY the measurements in the JSON and what is visible in the annotated image (teal = resident's marked route, "
    "red = estimated object ground footprint, dashed blue = delivery zone), write 2-3 plain sentences for the resident "
    "explaining the result and its uncertainty. Do not change the classification, do not invent measurements, and do not "
    "claim regulatory accessibility compliance."
)


def status() -> dict:
    if not settings.aws_enabled:
        return {"enabled": False, "reason": "AWS_ENABLED is false"}
    try:
        import boto3  # noqa: F401
    except ImportError:
        return {"enabled": False, "reason": "boto3 not installed"}
    return {"enabled": True, "model_id": settings.bedrock_model_id, "region": settings.aws_region}


def explain(analysis: dict, annotated_jpeg: bytes) -> dict:
    st = status()
    if not st["enabled"]:
        return {"status": "disabled", **st}
    import boto3

    summary = {k: analysis.get(k) for k in ("classification", "explanation", "mode", "limitations")}
    summary["objects"] = [{k: o.get(k) for k in ("id", "classification", "p_obstructed", "nominal", "envelope", "contact")}
                          for o in analysis.get("objects", [])]
    t0 = time.perf_counter()
    try:
        client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
        resp = client.converse(
            modelId=settings.bedrock_model_id,
            messages=[{"role": "user", "content": [
                {"text": PROMPT + "\n\nMEASUREMENTS:\n" + json.dumps(summary, default=str)[:6000]},
                {"image": {"format": "jpeg", "source": {"bytes": annotated_jpeg}}},
            ]}],
            inferenceConfig={"maxTokens": 300, "temperature": 0.2},
        )
        text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
        return {"status": "ok", "model_id": settings.bedrock_model_id, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "usage": resp.get("usage"), "text": text}
    except Exception as e:  # report the real failure; never fabricate output
        return {"status": "error", "model_id": settings.bedrock_model_id, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": f"{type(e).__name__}: {e}"[:400]}
