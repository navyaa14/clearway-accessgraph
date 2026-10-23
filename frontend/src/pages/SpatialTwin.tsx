import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, fmtTime, hhPath, session } from "../api";
import { CalibrationEditor } from "../components/CalibrationEditor";
import { EvidenceViewer } from "../components/EvidenceViewer";
import { Card, ClassBadge, Empty, Guidance, KV, num, pct, SourceLine, Spinner, TrustBadge, ViewScope } from "../components/ui";

export default function SpatialTwin() {
  const hh = useQuery({ queryKey: ["household", session()?.id], queryFn: () => api(hhPath()) });
  const [cam, setCam] = useState<string>("");
  const [tab, setTab] = useState<"analysis" | "calibrate">("analysis");
  useEffect(() => { if (!cam && hh.data?.cameras?.length) setCam(hh.data.cameras[0].id); }, [hh.data]);
  const camera = hh.data?.cameras?.find((c: any) => c.id === cam);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <select className="input" value={cam} onChange={(e) => setCam(e.target.value)} aria-label="Camera">
          {hh.data?.cameras?.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <div className="flex rounded-xl border border-ink-600 p-1 bg-ink-900" role="tablist">
          {(["analysis", "calibrate"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`px-4 py-1.5 rounded-lg text-[14px] ${tab === t ? "bg-ink-700 text-fog-100" : "text-fog-300"}`}>
              {t === "analysis" ? "Scene analysis" : "Mark route & zones"}
            </button>
          ))}
        </div>
      </div>
      {!camera ? <Spinner /> : tab === "calibrate" ? (
        <Card title={`Spatial calibration · ${camera.name}`}><CalibrationEditor camera={camera} /></Card>
      ) : <Analysis camera={camera} />}
    </div>
  );
}

function Analysis({ camera }: { camera: any }) {
  const obs = useQuery({ queryKey: ["observations", session()?.id], queryFn: () => api(hhPath("/observations?limit=40")) });
  const list = (obs.data || []).filter((o: any) => o.camera_id === camera.id);
  const [sel, setSel] = useState<string | null>(null);
  const o = list.find((x: any) => x.id === sel) || list[0];
  const [objId, setObjId] = useState<string | null>(null);
  if (obs.isLoading) return <Spinner />;
  if (!o) return <Empty title="No observations for this camera yet">Use Ring capture or the Demo lab to produce one.</Empty>;
  const a = o.analysis;
  const ob = (a.objects || []).find((x: any) => x.id === (objId || a.primary_object)) || a.objects?.[0];
  const nom = ob?.nominal || {};
  const u = nom.units || "px";

  return (
    <div className="grid xl:grid-cols-[minmax(0,1fr)_440px] 2xl:grid-cols-[minmax(0,1fr)_480px] gap-5">
      <div className="space-y-4">
        <Card title="Spatial twin" right={<span className="text-[12.5px] text-fog-400">{fmtTime(o.captured_at)}</span>}>
          <EvidenceViewer key={o.id} obs={o} showProposed />
        </Card>
        <Card title="Recent observations">
          <div className="flex gap-3 overflow-x-auto pb-1">
            {list.map((x: any) => (
              <button key={x.id} onClick={() => { setSel(x.id); setObjId(null); }} aria-pressed={x.id === o.id}
                className={`shrink-0 w-[168px] text-left rounded-xl border p-2 ${x.id === o.id ? "border-teal-400/70 bg-ink-800" : "border-ink-700 hover:border-ink-500"}`}>
                <div className="text-[12px] mono text-fog-300 truncate">{x.id}</div>
                <div className="mt-1.5"><ClassBadge c={x.classification} /></div>
                <div className="text-[11.5px] text-fog-400 mt-1.5">{fmtTime(x.captured_at)}</div>
              </button>
            ))}
          </div>
        </Card>
      </div>

      <div className="space-y-4">
        <Card title="Decision" right={<ClassBadge c={a.classification} />}>
          <p className="text-[14.5px] leading-relaxed text-fog-100">{a.explanation}</p>
          <ViewScope c={a.classification} className="mt-2" />
          <div className="mt-3"><SourceLine s={o.media_source} note={a.provenance?.media_source_note} /></div>
          <div className="mt-2 flex flex-wrap gap-2"><TrustBadge trust={o.trust} chronology={o.chronology} source={o.media_source} /><span className="chip border-ink-600 text-fog-300 mono">{o.event_source}</span></div>
          {a.verification_guidance?.length > 0 && <div className="mt-3"><Guidance items={a.verification_guidance} /></div>}
        </Card>
        {ob && (
          <Card title="Geometry & uncertainty" right={(a.objects?.length || 0) > 1 && (
            <select className="input !min-h-0 !py-1 text-[13px]" value={ob.id} onChange={(e) => setObjId(e.target.value)} aria-label="Object">{a.objects.map((x: any) => <option key={x.id}>{x.id}</option>)}</select>)}>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div><div className="label" title="Share of perturbed re-measurements (mask boundary, unseen depth, calibration) that conclude obstructed. Not a calibrated probability.">Perturbation agreement</div><div className="font-display text-[26px] font-semibold tabular-nums">{ob.p_obstructed === null || ob.p_obstructed === undefined ? "—" : ob.p_obstructed.toFixed(2)}</div><div className="text-[12px] text-fog-400">of {ob.samples} re-measurements agree · not a probability</div></div>
              {nom.clear_width ? (
                <div><div className="label">Min clear width</div><div className="font-display text-[26px] font-semibold tabular-nums">{num(nom.clear_width.min_clear_width, 0, " cm")}</div><div className="text-[12px] text-fog-400">p5–p95 {num(ob.envelope?.clear_width_p5, 0)}–{num(ob.envelope?.clear_width_p95, 0)} cm · need {a.geometry_inputs?.clearance_cm} cm</div></div>
              ) : (
                <div><div className="label">Footprint in route</div><div className="font-display text-[26px] font-semibold tabular-nums">{pct(nom.fraction_of_footprint)}</div><div className="text-[12px] text-fog-400">p5–p95 {pct(ob.envelope?.fraction_p5)}–{pct(ob.envelope?.fraction_p95)}</div></div>
              )}
            </div>
            <ConfidenceBar p={ob.p_obstructed} />
            <div className="mt-4">
              <KV rows={[
                ["Mode", <span className="mono">{a.mode}{a.homography_error_cm != null ? ` · reprojection consistency ${a.homography_error_cm} cm` : ""}</span>],
                ["Intersection", `${num(nom.intersection_area, u === "cm" ? 0 : 0)} ${u}²`],
                ["Intrusion depth", `${num(nom.intrusion_depth, 1)} ${u}`],
                ["Gap to route", `${num(nom.distance_to_route, 1)} ${u}`],
                ["Ground contact", <span className={ob.contact?.reliable ? "" : "text-amber-300"}>{ob.contact?.reason}{ob.contact?.contact_width_cm ? ` · width ${ob.contact.contact_width_cm} cm, depth ${ob.contact.depth_cm} cm` : ""}</span>],
                ["Segmentation", `${ob.segmentation_method} · conf ${ob.segmentation_confidence}`],
                ["In delivery zone", pct(ob.inside_drop_zone_fraction)],
              ]} />
            </div>
          </Card>
        )}
        <Card title="Evidence provenance">
          <KV rows={[
            ["Frame SHA-256", <span className="mono break-all">{a.frame?.sha256?.slice(0, 32)}…</span>],
            ["Calibration", <span className="mono">{a.calibration_version}</span>],
            ["Camera check", <span className={a.calibration_check?.valid === false ? "text-coral-400" : ""}>{a.calibration_check?.status} {a.calibration_check?.shift_px != null && `· shift ${a.calibration_check.shift_px}px · ${a.calibration_check.inliers} landmarks`}</span>],
            ["Image quality", a.quality?.usable ? `usable · sharpness ×${a.quality?.relative_sharpness ?? "—"} · luminance ${a.quality?.brightness}` : <span className="text-amber-300">{a.quality?.reasons?.join("; ")}</span>],
            ["Route observed", a.detection_gate ? `unexplained route change ${a.detection_gate.route_residual_change == null ? "n/a" : (a.detection_gate.route_residual_change * 100).toFixed(2) + "%"} · route detail lost ${a.detection_gate.route_detail_loss == null ? "n/a" : (a.detection_gate.route_detail_loss * 100).toFixed(0) + "%"}` : "—"],
            ["Pipeline", <span className="mono">{a.pipeline_version} / {a.model_version}</span>],
            ["Latency", a.timings ? `${a.timings.total_ms} ms` : "—"],
          ]} />
        </Card>
        <Card title="What ClearWay could not determine">
          <ul className="text-[13.5px] text-fog-300 list-disc pl-5 space-y-1.5">{(a.limitations || []).map((l: string) => <li key={l}>{l}</li>)}</ul>
        </Card>
      </div>
    </div>
  );
}

export function ConfidenceBar({ p }: { p?: number | null }) {
  if (p === null || p === undefined) return null;
  return (
    <div aria-label={`Perturbation agreement (obstructed): ${(p * 100).toFixed(0)}%`}>
      <div className="relative h-2.5 rounded-full bg-ink-700 overflow-hidden">
        <div className="absolute inset-y-0 left-[10%] right-[10%] bg-amber-400/25" />
        <div className="absolute inset-y-0 left-0 bg-gradient-to-r from-teal-400 to-coral-400" style={{ width: `${Math.max(2, p * 100)}%` }} />
      </div>
      <div className="flex justify-between text-[11px] text-fog-400 mt-1"><span>clear if ≤ 0.10</span><span className="text-amber-300">review band</span><span>obstructed if ≥ 0.90</span></div>
    </div>
  );
}
