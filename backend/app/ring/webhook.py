"""Signed webhook verification, Ring v1.1 event parsing and Stage 0 filtering.

Ring: header X-Signature = "sha256=" + hex(HMAC-SHA256(key, raw_body)).
The CUSTOM_EVENT_SIMULATOR uses the same scheme with a *different* key and a
different endpoint, so simulated events can never be mistaken for Ring events.

Ring webhook v1.1 (documented; corroborated by independent implementations):
    {"meta": {"version": "1.1", "account_id": "...", "request_id": "..."},
     "data": {"id": "<device>_<sub_type>_<epoch_ms>", "type": "motion_detected",
              "attributes": {"source": "<device id>", "source_type": "devices",
                             "timestamp": <epoch ms>, "sub_type": "motion", "component_ids": ["0"]},
              "relationships": {"devices": {...}}}}
  * the device is data.attributes.source when source_type == "devices"
    (app_integration_* events use source_type "users"; subscription_* use source "subscriptions")
  * data.id identifies the event; meta.request_id is the documented idempotency key
A pre-v1.1 / test shape (no "meta"; top-level id/type; attributes.device_id) is still accepted, but ONLY when no
"meta" block is present, so a v1.1 event can never be misread through the fallback.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json

# Ring CV guideline: read data.attributes.sub_type, default "unknown"; treat unknown as "process".
SKIP_SUBTYPES: set[str] = set()  # ClearWay cares about any object; nothing skipped by default
RELEVANT_TYPES = {"motion_detected"}
DEVICE_EVENT_TYPES = {"motion_detected", "button_press", "device_added", "device_removed", "device_online", "device_offline"}
MAX_BODY_BYTES = 64 * 1024  # documented payloads are well under 2 KB


class MalformedEvent(ValueError):
    pass


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str | None, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    expected = sign(secret, body)
    return hmac.compare_digest(expected, header.strip())


def _ts_seconds(v) -> float | None:
    """v1.1 timestamps are epoch milliseconds; also accept epoch seconds or ISO-8601 (legacy/test)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().lstrip("-").replace(".", "", 1).isdigit()):
        x = float(v)
        return x / 1000.0 if x > 1e11 else x
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_event(body: bytes) -> dict:
    """Parse a webhook body. Raises MalformedEvent for non-JSON / non-object bodies."""
    try:
        j = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise MalformedEvent(f"body is not JSON: {e}") from None
    if not isinstance(j, dict):
        raise MalformedEvent("body is not a JSON object")
    meta = j.get("meta") if isinstance(j.get("meta"), dict) else None
    data = j.get("data") if isinstance(j.get("data"), dict) else {}
    attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}

    if meta is not None:  # ---------------- documented v1.1
        fmt = f"v{meta.get('version') or '1.1'}"
        etype = data.get("type") or "unknown"
        event_id = data.get("id")
        source, source_type = attrs.get("source"), attrs.get("source_type")
        device_id = source if source_type == "devices" and isinstance(source, str) and source else None
        request_id = meta.get("request_id")
        account_id = meta.get("account_id")
    else:  # ---------------------------------- legacy / test shape (no meta block)
        fmt = "legacy"
        etype = j.get("type") or data.get("type") or "unknown"
        event_id = j.get("id") or data.get("id") or j.get("event_id")
        source, source_type = attrs.get("source"), attrs.get("source_type")
        device_id = (source if source_type == "devices" else None) or attrs.get("device_id") or data.get("device_id") or j.get("device_id")
        request_id = j.get("request_id")
        account_id = j.get("account_id")

    if not event_id:
        event_id = "body-" + hashlib.sha256(body).hexdigest()  # last resort; flagged below
    ts_raw = attrs.get("timestamp") if attrs.get("timestamp") is not None else (data.get("timestamp") or j.get("timestamp"))
    comps = attrs.get("component_ids")
    return {
        "format": fmt,
        "event_id": str(event_id),
        "event_id_from_body_hash": str(event_id).startswith("body-"),
        "request_id": str(request_id) if request_id else None,
        "account_id": account_id,
        "type": etype,
        "sub_type": attrs.get("sub_type") or "unknown",
        "device_id": device_id,
        "source": source,
        "source_type": source_type,
        "component_ids": [str(c) for c in comps] if isinstance(comps, list) else None,
        "timestamp": ts_raw,
        "timestamp_s": _ts_seconds(ts_raw),
        "raw": j,
    }


def stage0(evt: dict) -> tuple[bool, str, str]:
    """Returns (process, disposition, reason). Disposition is recorded on the event:
    PROCESS | FILTERED_IRRELEVANT_TYPE | FILTERED_SUBTYPE | REJECTED_NO_DEVICE_IDENTITY."""
    if evt["type"] not in RELEVANT_TYPES:
        return False, "FILTERED_IRRELEVANT_TYPE", f"event type '{evt['type']}' not relevant to route analysis"
    if not evt.get("device_id"):
        why = (f"source_type is '{evt.get('source_type')}', not 'devices'" if evt.get("source_type") not in (None, "devices")
               else "no data.attributes.source")
        return False, "REJECTED_NO_DEVICE_IDENTITY", f"motion event without a device identity ({why}); cannot capture or analyse"
    if evt["sub_type"] in SKIP_SUBTYPES:
        return False, "FILTERED_SUBTYPE", f"sub_type '{evt['sub_type']}' skipped"
    return True, "PROCESS", f"process (sub_type={evt['sub_type']}, device={evt['device_id']})"
