import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Radio, XCircle } from "lucide-react";
import { useState } from "react";
import { api, fmtTime, hhPath, session } from "../api";
import { Card, Empty, ErrorNote, IdentitySelect, KV } from "../components/ui";

const EMU_SCENES: { id: string; label: string }[] = [
  { id: "reference", label: "Clear path (reference)" },
  { id: "initial", label: "Parcel on walkway edge" },
  { id: "centre_block", label: "Parcel mid-walkway" },
  { id: "partial_move", label: "Parcel nudged aside" },
  { id: "moved_to_zone", label: "Parcel in delivery area" },
];

function EmulatorPanel() {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["emu-state"], queryFn: () => api(hhPath("/ring/emulator")), refetchInterval: 3000 });
  const evts = useQuery({ queryKey: ["hh-events"], queryFn: () => api(hhPath("/events")), refetchInterval: 3000 });
  const scene = useMutation({
    mutationFn: (v: { scene: string; fire_event: boolean }) => api(hhPath("/ring/emulator/scene"), { method: "POST", json: v }),
    onSettled: () => { qc.invalidateQueries(); },
  });
  const online = useMutation({ mutationFn: (v: boolean) => api(hhPath("/ring/emulator/online"), { method: "POST", json: { online: v } }), onSettled: () => qc.invalidateQueries() });
  const d = st.data;
  return (
    <Card title={<span className="flex items-center gap-2"><Radio size={16} className="text-violet-300" /> Ring API emulator controls</span>}
      right={d && <span className="chip text-violet-300 border-violet-400/40 mono">{d.device_id} · {d.online ? "online" : "offline"}</span>}>
      <p className="text-[13px] text-fog-400 mb-3">Change what the emulated camera sees. “Send motion event” makes the emulator POST a signed <span className="mono">motion_detected</span> webhook to ClearWay, which then captures a frame over WHEP — the same path a Ring event takes.</p>
      <div className="grid sm:grid-cols-2 xl:grid-cols-5 gap-2">
        {EMU_SCENES.map((x) => (
          <button key={x.id} disabled={scene.isPending} onClick={() => scene.mutate({ scene: x.id, fire_event: true })}
            className={`text-left rounded-xl border p-3 bg-ink-850 hover:border-violet-400/60 ${d?.scene === x.id ? "border-violet-400/70" : "border-ink-700"}`}>
            <div className="text-[14px] font-medium">{x.label}</div>
            <div className="text-[12px] text-fog-400 mt-0.5">set scene + send motion event</div>
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-2 mt-3">
        <button className="btn-ghost !min-h-0 !py-1.5 text-[13px]" onClick={() => online.mutate(!d?.online)} disabled={!d}>{d?.online ? "Take device offline (503)" : "Bring device online"}</button>
      </div>
      <ErrorNote error={scene.error || online.error || st.error} />
      {d && (
        <div className="grid lg:grid-cols-2 gap-4 mt-4 text-[12.5px]">
          <div>
            <div className="label mb-1.5">Webhooks sent by the emulator</div>
            <ul className="space-y-1 mono text-fog-300">{[...d.events_sent].reverse().slice(0, 6).map((e: any) => <li key={e.event_id}>{fmtTime(e.t)} · {e.scene} → HTTP {e.status ?? "error"}</li>)}</ul>
          </div>
          <div>
            <div className="label mb-1.5">WHEP sessions on the emulator</div>
            <ul className="space-y-1 mono text-fog-300">{[...d.sessions].reverse().slice(0, 6).map((x: any) => <li key={x.id}>{x.id.slice(0, 8)} · {x.scene} · {x.closed ? `${(x.closed - x.started).toFixed(1)} s, ${x.reason}` : "open"}</li>)}</ul>
          </div>
          <div className="lg:col-span-2">
            <div className="label mb-1.5">Webhooks as ClearWay processed them (v1.1 parsing, explicit outcome)</div>
            <ul className="space-y-1 mono text-fog-300">
              {(evts.data || []).filter((e: any) => e.source === "ring").slice(0, 6).map((e: any) => (
                <li key={e.event_key} className="break-all">
                  {fmtTime(e.received_at)} · {e.format || "?"} · device {e.device_id || "—"} ·{" "}
                  <span className={e.status === "COMPLETED" ? "text-teal-300" : e.status === "FAILED" || e.status === "DEAD" ? "text-rose-300" : "text-amber-300"}>{e.status}</span>
                  {e.disposition && e.disposition !== "ANALYSED" ? ` (${e.disposition})` : ""}{e.observation_id ? ` → ${e.observation_id}` : ""}
                </li>
              ))}
              {!(evts.data || []).some((e: any) => e.source === "ring") && <li className="text-fog-400">No Ring-path webhooks received for this household yet.</li>}
            </ul>
          </div>
        </div>
      )}
    </Card>
  );
}

export default function RingPage() {
  const qc = useQueryClient();
  const sess = session()!;
  const st = useQuery({ queryKey: ["ring-status"], queryFn: () => api("/api/ring/status"), refetchInterval: 10000 });
  const sessions = useQuery({ queryKey: ["ring-sessions", sess.id], queryFn: () => api(hhPath("/ring/sessions")), refetchInterval: 4000 });
  const hh = useQuery({ queryKey: ["household", sess.id], queryFn: () => api(hhPath()) });
  const devices = useMutation({ mutationFn: () => api(hhPath("/ring/devices")) });
  const [member, setMember] = useState("");
  const auth = useMutation({
    mutationFn: (cam: string) => api(hhPath(`/cameras/${cam}/authorize`), { method: "POST", member }),
    onSettled: () => qc.invalidateQueries(),
  });
  const capture = useMutation({ mutationFn: (cam: string) => api(hhPath(`/ring/${cam}/capture`), { method: "POST" }), onSettled: () => qc.invalidateQueries() });
  const ringCams = (hh.data?.cameras || []).filter((c: any) => c.kind === "ring");
  const s = st.data;

  return (
    <div className="space-y-5">
      <Card title={<span className="flex items-center gap-2"><Radio size={16} className="text-sky-400" /> Ring Partner API connection</span>}
        right={s && <span className={`chip ${s.configured ? "text-teal-300 border-teal-400/40" : "text-amber-300 border-amber-400/40"}`}>{s.configured ? "configured" : "not configured"}</span>}>
        <div className="grid lg:grid-cols-2 gap-6">
          <KV rows={[
            ["Live household", s?.live_household_provisioned ? "provisioned (credentials held by the server only)" : "not provisioned"],
            ["Verified against real Ring", <span className="text-amber-300">{s?.emulator ? "No — running against the Ring API emulator" : "No — local protocol tests only"}</span>],
            ["Source label", <span className="mono">{s?.source_label}</span>],
            ["Transport", "WHEP (WebRTC, recvonly video) on the backend via aiortc; snapshot download as fallback"],
            ["Credentials", "Server-side only; rotated tokens encrypted at rest (Fernet); never sent to the browser"],
          ]} />
          <div className="text-[13.5px] text-fog-300 space-y-2">
            <p>Calls are made server-to-server to documented endpoints only:</p>
            <ul className="mono text-[12px] text-fog-200 space-y-1 bg-ink-950 border border-ink-700 rounded-lg p-3">
              <li>GET  /v1/devices?include=status,capabilities</li>
              <li>POST /v1/devices/&#123;id&#125;/media/streaming/whep/sessions</li>
              <li>DELETE &lt;Location of WHEP session&gt;</li>
              <li>POST /v1/devices/&#123;id&#125;/media/image/download</li>
              <li>POST https://oauth.ring.com/oauth/token (refresh)</li>
              <li>POST /webhooks/ring ← X-Signature: sha256=HMAC</li>
            </ul>
          </div>
        </div>
        {s?.emulator && (
          <div className="mt-5 rounded-xl border border-violet-400/40 bg-ink-850 p-4 text-[13.5px] text-violet-200">
            <strong>Connected to the local Ring API emulator — not Ring.</strong> ClearWay is using its real live Ring code path
            (OAuth refresh, device discovery, WHEP/WebRTC capture and DELETE, signed webhooks) against an emulator built from Ring's
            public API documentation. Media is labelled <span className="mono">RING_API_EMULATOR</span>; the picture is a labelled synthetic
            scene. To use the real Developer Playground, point <span className="mono">RING_API_BASE</span> at Ring and supply your credentials.
          </div>
        )}
        {s && !s.configured && (
          <div className="mt-5 rounded-xl border border-amber-400/40 bg-amber-900/30 p-4 text-[13.5px] text-amber-300">
            <strong>Blocker (documented):</strong> no Ring Playground credentials are present in this environment. Set <span className="mono">RING_ACCESS_TOKEN</span> (or <span className="mono">RING_REFRESH_TOKEN</span> + <span className="mono">RING_CLIENT_ID</span>/<span className="mono">RING_CLIENT_SECRET</span>) and <span className="mono">RING_DEVICE_ID</span>, then restart. The WHEP client is verified against a local protocol test server in the test suite, but a genuine Ring session has not been exercised until credentials are supplied.
          </div>
        )}
        <div className="mt-5 flex flex-wrap gap-3">
          <button className="btn-ghost" onClick={() => devices.mutate()} disabled={!s?.configured || sess.demo || devices.isPending}>Discover devices</button>
          {sess.demo && <span className="text-[13px] text-fog-400 self-center">Demo households cannot reach Ring. Sign in to the live household (server-provisioned) to use the Ring camera.</span>}
        </div>
        <ErrorNote error={devices.error} />
        {devices.data && <pre className="mono text-[11.5px] mt-3 bg-ink-950 border border-ink-700 rounded-lg p-3 max-h-64 overflow-auto">{JSON.stringify(devices.data, null, 2)}</pre>}
      </Card>

      {s?.emulator && !sess.demo && <EmulatorPanel />}

      <Card title="Ring cameras for this household">
        {ringCams.length === 0 ? <Empty title="No Ring camera in this household">Ring cameras exist only in the server-provisioned live household (set RING_* and CLEARWAY_LIVE_* variables, restart, then sign in with the live household token).</Empty> : (
          <div className="space-y-4">
            {ringCams.map((c: any) => (
              <div key={c.id} className="rounded-xl border border-ink-700 p-4 flex flex-wrap items-center gap-4">
                <div className="min-w-[220px]">
                  <div className="font-medium">{c.name}</div>
                  <div className="text-[12.5px] text-fog-400 mono">{c.ring_device_id}</div>
                  <div className="text-[12.5px] mt-1">{c.authorized ? <span className="text-teal-300">access authorised</span> : <span className="text-amber-300">awaiting household authorisation</span>} · {c.calibration ? `route ${c.calibration.version}` : "route not marked"}</div>
                </div>
                {!c.authorized ? (
                  <div className="flex flex-wrap gap-2 items-end">
                    <div className="min-w-[260px]"><IdentitySelect id={`auth-${c.id}`} label="Authorise as" value={member} onChange={setMember} identities={sess.identities} demo={sess.demo} /></div>
                    <button className="btn-primary" disabled={!member} onClick={() => auth.mutate(c.id)}>Authorise camera access</button>
                  </div>
                ) : (
                  <button className="btn-primary" disabled={capture.isPending || !c.calibration?.has_reference} onClick={() => capture.mutate(c.id)}>
                    {capture.isPending ? "Opening WHEP session…" : "Capture live frame & analyse"}
                  </button>
                )}
              </div>
            ))}
            <ErrorNote error={auth.error || capture.error} />
            {capture.data && <div className="text-[13.5px] text-teal-300">Observation {capture.data.id}: {capture.data.classification}</div>}
          </div>
        )}
      </Card>

      <Card title="WHEP session log">
        {!sessions.data?.length ? <Empty title="No sessions yet" /> : (
          <ul className="space-y-3">
            {sessions.data.map((x: any) => (
              <li key={x.id} className="rounded-xl border border-ink-700 p-3 text-[13px]">
                <div className="flex flex-wrap items-center gap-2">
                  {x.state === "COMPLETED" ? <CheckCircle2 size={15} className="text-teal-400" /> : <XCircle size={15} className="text-amber-400" />}
                  <span className="mono">{x.state}</span><span className="text-fog-400">{fmtTime(x.started_at)}</span>
                  <span className="text-fog-300">frames {x.frames_received} received / {x.frames_sampled} sampled</span>
                  {x.resolution && <span className="mono text-fog-400">{x.resolution[0]}×{x.resolution[1]}</span>}
                </div>
                {x.error && <div className="text-amber-300 mt-1">{x.error}</div>}
                <ol className="mono text-[11.5px] text-fog-400 mt-2 space-y-0.5">{x.log.map((l: any, i: number) => <li key={i}>+{l.t}s {l.msg}</li>)}</ol>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
