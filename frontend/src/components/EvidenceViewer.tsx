import { useState } from "react";
import { ClassBadge, SourceBadge } from "./ui";

type Layer = "route" | "zone" | "mask" | "footprint" | "envelope" | "proposed";
const LAYER_LABEL: Record<Layer, string> = {
  route: "Marked route", zone: "Delivery zone", mask: "Object mask", footprint: "Ground footprint", envelope: "Uncertainty envelope", proposed: "Proposed placement",
};

const pts = (p: number[][] | null | undefined, w: number, h: number) => (p || []).map(([x, y]) => `${x * w},${y * h}`).join(" ");

export function EvidenceViewer({ obs, layers: initial, showProposed = false, compact = false, caption }: {
  obs: any; layers?: Layer[]; showProposed?: boolean; compact?: boolean; caption?: string;
}) {
  const a = obs?.analysis || {};
  const w = a.frame?.width || 960, h = a.frame?.height || 720;
  const [mode, setMode] = useState<"overlay" | "server">("overlay");
  const [on, setOn] = useState<Set<Layer>>(new Set(initial || ["route", "zone", "mask", "footprint", "envelope", ...(showProposed ? ["proposed" as Layer] : [])]));
  const gi = a.geometry_inputs || {};
  const toggle = (l: Layer) => setOn((s) => { const n = new Set(s); n.has(l) ? n.delete(l) : n.add(l); return n; });
  const cf = a.counterfactual;

  return (
    <figure className="m-0">
      <div className="relative rounded-xl overflow-hidden bg-black border border-ink-700" style={{ aspectRatio: `${w}/${h}` }}>
        {mode === "server" ? (
          <img src={obs.evidence_url} alt="Server-rendered annotated evidence frame" className="absolute inset-0 w-full h-full object-contain" />
        ) : (
          <>
            {/* Full frame is shown unmodified: provider watermarks are never cropped or covered */}
            <img src={obs.frame_url} alt={`Observed frame from ${a.provenance?.media_source}`} className="absolute inset-0 w-full h-full object-contain" />
            <svg viewBox={`0 0 ${w} ${h}`} className="absolute inset-0 w-full h-full" aria-hidden>
              <defs>
                <pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <line x1="0" y1="0" x2="0" y2="8" stroke="#F87171" strokeWidth="3" />
                </pattern>
              </defs>
              {on.has("route") && gi.route && <polygon points={pts(gi.route, w, h)} fill="#2DD4BF" fillOpacity={0.18} stroke="#2DD4BF" strokeWidth={2.5} />}
              {on.has("zone") && gi.drop_zone && <polygon points={pts(gi.drop_zone, w, h)} fill="#60A5FA" fillOpacity={0.08} stroke="#60A5FA" strokeWidth={2} strokeDasharray="10 7" />}
              {(a.objects || []).map((o: any) => (
                <g key={o.id}>
                  {on.has("envelope") && o.envelope_outer && <polygon points={pts(o.envelope_outer, w, h)} fill="none" stroke="#F5A524" strokeWidth={1.5} strokeDasharray="5 5" />}
                  {on.has("mask") && o.mask_outline?.length > 2 && <polygon points={pts(o.mask_outline, w, h)} fill="none" stroke="#FCD34D" strokeWidth={1.5} />}
                  {on.has("footprint") && o.footprint && (
                    <polygon points={pts(o.footprint, w, h)} fill={o.classification === "POTENTIAL_OBSTRUCTION" ? "url(#hatch)" : "#5EEAD4"}
                      fillOpacity={o.classification === "POTENTIAL_OBSTRUCTION" ? 0.85 : 0.35}
                      stroke={o.classification === "POTENTIAL_OBSTRUCTION" ? "#F87171" : "#5EEAD4"} strokeWidth={2.5} />
                  )}
                </g>
              ))}
              {on.has("proposed") && cf?.proposed_footprint && (
                <polygon points={pts(cf.proposed_footprint, w, h)} fill="#FFFFFF" fillOpacity={0.12} stroke="#FFFFFF" strokeWidth={2.5} strokeDasharray="8 6" />
              )}
            </svg>
          </>
        )}
        <div className="absolute left-2 bottom-2 flex flex-wrap gap-1.5 max-w-[95%]">
          <ClassBadge c={a.classification} />
          <SourceBadge s={a.provenance?.media_source} title={a.provenance?.media_source_note} compact />
        </div>
      </div>
      {!compact && (
        <div className="flex flex-wrap items-center gap-1.5 mt-3" role="group" aria-label="Overlay layers">
          <button className={`chip ${mode === "overlay" ? "border-teal-400/60 text-teal-300" : "border-ink-600 text-fog-300"}`} onClick={() => setMode(mode === "overlay" ? "server" : "overlay")}>
            {mode === "overlay" ? "Spatial overlay" : "Server-rendered evidence image"}
          </button>
          <span className="w-px h-5 bg-ink-600 mx-1" />
          {(Object.keys(LAYER_LABEL) as Layer[]).filter((l) => l !== "proposed" || cf?.proposed_footprint).map((l) => (
            <button key={l} aria-pressed={on.has(l)} onClick={() => toggle(l)} disabled={mode === "server"}
              className={`chip ${on.has(l) ? "border-fog-300/50 text-fog-100 bg-ink-700" : "border-ink-600 text-fog-400"}`}>{LAYER_LABEL[l]}</button>
          ))}
        </div>
      )}
      {caption && <figcaption className="text-[12.5px] text-fog-400 mt-2">{caption}</figcaption>}
    </figure>
  );
}
