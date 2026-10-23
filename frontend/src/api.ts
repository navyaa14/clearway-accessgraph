import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

export type Pt = [number, number];
export type Classification = "POTENTIAL_OBSTRUCTION" | "NO_OBSTRUCTION_OBSERVED" | "REVIEW_REQUIRED" | "UNABLE_TO_VERIFY";

/** A member credential the UI can act with. In demo mode these are published, simulated identities. */
export interface Identity { id: string; name: string; role: string; can_approve: number; is_helper: number; token: string }
export interface Session { id: string; name: string; need?: string; token: string; demo: boolean; identities: Identity[] }

let current: Session | null = null;
export const setSession = (s: Session | null) => { current = s; };
export const session = () => current;

export class ApiError extends Error {
  constructor(public status: number, public detail: any) { super(typeof detail === "string" ? detail : detail?.message || JSON.stringify(detail)); }
}

/** All credentials travel in headers; none are ever placed in URLs. */
export async function api<T = any>(path: string, opts: RequestInit & { json?: any; member?: string } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as any) };
  if (current) headers["X-Household-Token"] = current.token;
  if (opts.member) headers["X-Member-Token"] = opts.member;
  let body = opts.body;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.json); }
  let r: Response;
  try {
    r = await fetch(path, { ...opts, headers, body });
  } catch {
    throw new ApiError(0, "Cannot reach the ClearWay server. Check that it is running and try again.");
  }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new ApiError(r.status, (data as any)?.detail ?? data);
  return data as T;
}

export const hhPath = (p = "") => `/api/households/${current?.id}${p}`;

/** Server-sent events via a short-lived signed ticket (EventSource cannot send headers). */
export function useLiveUpdates(onEvent?: (kind: string, data: any) => void) {
  const qc = useQueryClient();
  const hh = current?.id;
  useEffect(() => {
    if (!hh) return;
    let es: EventSource | null = null;
    let timer: number | undefined;
    let closed = false;
    const connect = async () => {
      try {
        const t = await api<{ url: string }>(hhPath("/stream-ticket"), { method: "POST" });
        if (closed) return;
        es = new EventSource(t.url);
        const handler = (e: MessageEvent) => {
          qc.invalidateQueries();
          try { const m = JSON.parse(e.data); onEvent?.(m.kind, m.data); } catch { /* keepalive */ }
        };
        ["observation", "incident", "action", "calibration"].forEach((k) => es!.addEventListener(k, handler as any));
        es.onerror = () => { es?.close(); if (!closed) timer = window.setTimeout(connect, 4000); };
      } catch {
        if (!closed) timer = window.setTimeout(connect, 8000);
      }
    };
    connect();
    return () => { closed = true; es?.close(); window.clearTimeout(timer); };
  }, [hh]);
}

export const fmtTime = (t?: number | null) => t ? new Date(t * 1000).toLocaleString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", month: "short", day: "numeric" }) : "—";
export const ago = (t?: number | null) => {
  if (!t) return "—";
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

export const RESOLUTION_LABEL: Record<string, string> = {
  VERIFIED_NO_OBSTRUCTION_OBSERVED: "Camera-verified: no obstruction observed",
  MANUAL_OVERRIDE_NOT_CAMERA_VERIFIED: "Closed manually — not camera-verified",
};

/** Wording for a closed incident. `verification_basis` says whose captures verified it, so an emulator- or
 *  demo-verified closure can never read as genuine Ring verification. */
export function resolutionText(d: { resolution?: string | null; verification_basis?: string | null; namespace?: string | null }): string {
  if (!d.resolution) return "";
  if (d.resolution === "MANUAL_OVERRIDE_NOT_CAMERA_VERIFIED" || d.verification_basis === "MANUAL_CONFIRMATION")
    return RESOLUTION_LABEL.MANUAL_OVERRIDE_NOT_CAMERA_VERIFIED;
  switch (d.verification_basis) {
    case "GENUINE_RING_CAPTURE": return "Camera-verified on genuine Ring captures: no obstruction observed";
    case "EMULATOR_CAPTURE": return "Verified on Ring API emulator captures — not genuine Ring evidence";
    case "DEMO_EVIDENCE": return "Verified on labelled test evidence (demo) — not a live camera verification";
  }
  if (d.namespace !== "LIVE") return "Verified on labelled test evidence (demo) — not a live camera verification";
  return RESOLUTION_LABEL[d.resolution] || d.resolution;
}
