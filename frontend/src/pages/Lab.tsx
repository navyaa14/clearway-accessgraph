import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Play, Upload } from "lucide-react";
import { api, hhPath, session } from "../api";
import { Card, ClassBadge, Empty, ErrorNote, Guidance, num, ViewScope } from "../components/ui";
import type { PageId } from "../App";

const SCENES: { id: string; label: string; note: string; opts?: any }[] = [
  { id: "initial", label: "Parcel delivered on walkway edge", note: "Same frame: ~66 cm clear. Household A (60 cm) vs B (91 cm) differ." },
  { id: "centre_block", label: "Parcel in the middle of the walkway", note: "Blocks most of the path." },
  { id: "partial_move", label: "Correction 1: parcel nudged aside", note: "Overlap reduced but not removed." },
  { id: "moved_to_zone", label: "Correction 2: parcel moved to delivery zone", note: "Footprint inside the designated zone." },
  { id: "initial_lowlight", label: "Low-light frame", note: "Should be rejected as unverifiable." },
  { id: "initial_blurry", label: "Blurry frame", note: "Should be rejected as unverifiable." },
  { id: "obscured", label: "Lens obscured", note: "Should be rejected as unverifiable." },
  { id: "camera_moved", label: "Camera bumped after calibration", note: "Calibration invalidated → unable to verify." },
  { id: "initial", label: "Delayed old event (captured 10 min ago)", note: "Arrives after newer evidence; must not overwrite.", opts: { captured_offset_s: -600 } },
];

export default function Lab({ go }: { go: (p: PageId, inc?: string) => void }) {
  const qc = useQueryClient();
  const hh = useQuery({ queryKey: ["household", session()?.id], queryFn: () => api(hhPath()) });
  const ev = useQuery({ queryKey: ["eval"], queryFn: () => api("/api/eval/latest"), retry: false });
  const metrics = useQuery({ queryKey: ["metrics"], queryFn: () => api("/api/metrics") });
  const [last, setLast] = useState<any>(null);
  const emit = useMutation({ mutationFn: (s: any) => api(hhPath("/simulator/emit"), { method: "POST", json: { scene: s.id, ...(s.opts || {}) } }), onSuccess: (d) => { setLast(d); qc.invalidateQueries(); } });
  const dup = useMutation({
    mutationFn: async () => { const id = `sim-dup-${Date.now()}`; const a = await api(hhPath("/simulator/emit"), { method: "POST", json: { scene: "initial", event_id: id } }); const b = await api(hhPath("/simulator/emit"), { method: "POST", json: { scene: "initial", event_id: id } }); return { first: a, second: b }; },
    onSuccess: (d) => { setLast({ duplicate_test: d }); qc.invalidateQueries(); },
  });
  const [cam, setCam] = useState("");
  const [src, setSrc] = useState("INDEPENDENT_TEST_VIDEO");
  const upload = useMutation({
    mutationFn: (f: File) => { const fd = new FormData(); fd.append("file", f); fd.append("camera_id", cam); fd.append("source", src); return api(hhPath("/observations/upload"), { method: "POST", body: fd }); },
    onSuccess: (d) => { setLast({ observation: d }); qc.invalidateQueries(); },
  });
  const nonSynth = (hh.data?.cameras || []).filter((c: any) => c.kind !== "synthetic");
  const E = ev.data;

  return (
    <div className="space-y-5">
      <div className="grid xl:grid-cols-[1.3fr_1fr] gap-5">
        <Card title={<span className="flex items-center gap-2"><FlaskConical size={16} className="text-teal-400" /> Labelled test scenarios</span>}>
          <p className="text-[13px] text-fog-400 mb-4">Each button signs a Ring-shaped <span className="mono">motion_detected</span> event with the <strong>simulator</strong> key (not Ring's), passes it through signature verification, idempotency and Stage-0 filtering, renders a synthetic scene, and runs the real pipeline. Everything is labelled <span className="mono">CUSTOM_EVENT_SIMULATOR</span> / <span className="mono">SYNTHETIC_RENDER</span>.</p>
          <div className="grid sm:grid-cols-2 gap-2">
            {SCENES.map((s, i) => (
              <button key={i} className="text-left rounded-xl border border-ink-700 hover:border-teal-400/60 bg-ink-850 p-3 disabled:opacity-50" disabled={emit.isPending} onClick={() => emit.mutate(s)}>
                <div className="flex items-center gap-2 text-[14px] font-medium"><Play size={14} className="text-teal-400" /> {s.label}</div>
                <div className="text-[12px] text-fog-400 mt-1">{s.note}</div>
              </button>
            ))}
            <button className="text-left rounded-xl border border-ink-700 hover:border-teal-400/60 bg-ink-850 p-3" disabled={dup.isPending} onClick={() => dup.mutate()}>
              <div className="flex items-center gap-2 text-[14px] font-medium"><Play size={14} className="text-teal-400" /> Duplicate event delivered twice</div>
              <div className="text-[12px] text-fog-400 mt-1">Second delivery must be ignored (idempotency).</div>
            </button>
          </div>
          <ErrorNote error={emit.error || dup.error} />
        </Card>
        <Card title="Result">
          {emit.isPending || dup.isPending ? <div className="text-fog-300 text-sm pulse-dot">Running pipeline…</div> : !last ? <Empty title="Run a scenario" /> : (
            <div className="space-y-3 text-[13.5px]">
              {last.duplicate_test ? (
                <>
                  <div>First delivery: <span className="mono">{last.duplicate_test.first.observation?.classification}</span></div>
                  <div>Second delivery: {last.duplicate_test.second.duplicate ? <span className="text-teal-300">ignored as duplicate ✓</span> : <span className="text-coral-400">processed again ✗</span>}</div>
                </>
              ) : (
                <>
                  {(last.observation) && <ClassBadge c={last.observation.classification} big />}
                  <p className="text-fog-200">{last.observation?.analysis?.explanation}</p>
                  <ViewScope c={last.observation?.classification} />
                  <Guidance items={last.observation?.analysis?.verification_guidance} />
                  <div className="text-fog-400">Disposition: <span className="mono text-fog-200">{last.observation?.disposition || "—"}</span></div>
                  {last.observation?.incident_id && <button className="btn-ghost" onClick={() => go("incidents", last.observation.incident_id)}>Open incident {last.observation.incident_id}</button>}
                </>
              )}
            </div>
          )}
        </Card>
      </div>

      <Card title={<span className="flex items-center gap-2"><Upload size={16} /> Independent test footage</span>}>
        {nonSynth.length === 0 ? (
          <p className="text-[13.5px] text-fog-400">Add an upload camera (API: <span className="mono">POST /api/households/&#123;id&#125;/cameras</span>), upload a clear-scene reference, and mark its route in Spatial twin to analyse your own footage. Uploads are always labelled as independent test media — never as Ring footage.</p>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <select className="input" value={cam} onChange={(e) => setCam(e.target.value)}><option value="">Camera…</option>{nonSynth.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
            <select className="input" value={src} onChange={(e) => setSrc(e.target.value)}><option>INDEPENDENT_TEST_VIDEO</option><option>RECORDED_REPLAY</option></select>
            <label className={`btn-primary cursor-pointer ${!cam ? "opacity-40 pointer-events-none" : ""}`}><Upload size={16} /> Upload image or video<input type="file" accept="image/*,video/*" className="sr-only" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} /></label>
          </div>
        )}
        <ErrorNote error={upload.error} />
      </Card>

      <div className="grid xl:grid-cols-2 gap-5">
        <Card title="Measured evaluation (synthetic labelled set)" right={E && <span className="text-[12px] text-fog-400">{E.generated_at}</span>}>
          {!E ? <Empty title="No evaluation run found">Run <span className="mono">python -m eval.run_eval</span> in the backend.</Empty> : (
            <div className="space-y-4">
              <p className="text-[12.5px] text-amber-300">{E.disclaimer}</p>
              <div className="grid grid-cols-3 gap-4">
                <Metric k="Precision" v={E.random_set.precision} />
                <Metric k="Recall" v={E.random_set.recall} />
                <Metric k="Review / unresolved" v={E.random_set.unresolved_rate} />
              </div>
              <div className="text-[13px] text-fog-300">{E.random_set.n} scenes ({E.random_set.n_borderline} borderline) · clear-width MAE {num(E.random_set.clear_width_mae_cm, 1)} cm · footprint IoU {num(E.random_set.footprint_iou_mean, 2)} · p50 latency {num(E.random_set.latency_p50_ms, 0)} ms</div>
              <Confusion m={E.random_set.confusion} />
              <div>
                <div className="label mb-2">Scenario checklist ({E.scenarios.filter((s: any) => s.pass).length}/{E.scenarios.length} pass)</div>
                <ul className="text-[12.5px] space-y-1 max-h-72 overflow-auto pr-1">
                  {E.scenarios.map((s: any) => <li key={s.id} className="flex gap-2"><span className={s.pass ? "text-teal-300" : "text-coral-400"}>{s.pass ? "✓" : "✗"}</span><span className="text-fog-200">{s.id}. {s.name}</span><span className="text-fog-400 mono ml-auto">{s.observed}</span></li>)}
                </ul>
              </div>
            </div>
          )}
        </Card>
        <Card title="Pipeline instrumentation (this server)">
          {!metrics.data || !Object.keys(metrics.data).length ? <Empty title="No runs yet" /> : (
            <table className="w-full text-[13px]">
              <thead className="text-fog-400 text-left"><tr><th className="font-normal py-1">Stage</th><th className="font-normal">Count</th><th className="font-normal">Failures</th><th className="font-normal">Avg ms</th><th className="font-normal">p95 ms</th></tr></thead>
              <tbody>{Object.entries(metrics.data).map(([k, v]: any) => <tr key={k} className="border-t border-ink-700/60"><td className="py-1.5 mono">{k}</td><td>{v.count}</td><td>{v.failures}</td><td>{v.avg_ms || "—"}</td><td>{v.p95_ms ?? "—"}</td></tr>)}</tbody>
            </table>
          )}
          <p className="text-[12px] text-fog-400 mt-3">Bedrock (Stage 3) invocations appear here only when AWS_ENABLED=true and a call is actually made.</p>
        </Card>
      </div>
    </div>
  );
}

function Metric({ k, v }: { k: string; v: number | null }) {
  return <div><div className="label">{k}</div><div className="font-display text-[24px] font-semibold tabular-nums">{v === null || v === undefined ? "—" : v.toFixed(3)}</div></div>;
}

function Confusion({ m }: { m: Record<string, Record<string, number>> }) {
  const preds = ["POTENTIAL_OBSTRUCTION", "NO_OBSTRUCTION_OBSERVED", "REVIEW_REQUIRED", "UNABLE_TO_VERIFY"];
  const short: any = { POTENTIAL_OBSTRUCTION: "Obstr.", NO_OBSTRUCTION_OBSERVED: "Clear", REVIEW_REQUIRED: "Review", UNABLE_TO_VERIFY: "Unable" };
  return (
    <table className="w-full text-[12.5px] text-center">
      <thead><tr><th className="text-left font-normal text-fog-400">truth ↓ / predicted →</th>{preds.map((p) => <th key={p} className="font-normal text-fog-400">{short[p]}</th>)}</tr></thead>
      <tbody>{Object.entries(m).map(([t, row]) => <tr key={t} className="border-t border-ink-700/60"><td className="text-left py-1.5 mono">{t}</td>{preds.map((p) => <td key={p} className={`tabular-nums ${row[p] ? "text-fog-100" : "text-fog-400"}`}>{row[p] || 0}</td>)}</tr>)}</tbody>
    </table>
  );
}
