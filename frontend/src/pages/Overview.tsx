import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Camera, ShieldCheck } from "lucide-react";
import { ago, api, hhPath, session } from "../api";
import { EvidenceViewer } from "../components/EvidenceViewer";
import { Card, ClassBadge, Empty, ErrorNote, SourceBadge, Spinner, StateBadge, ViewScope } from "../components/ui";
import type { PageId } from "../App";

export default function Overview({ go }: { go: (p: PageId, inc?: string) => void }) {
  const hh = useQuery({ queryKey: ["household", session()?.id], queryFn: () => api(hhPath()) });
  if (hh.isLoading) return <Spinner />;
  if (hh.error) return <ErrorNote error={hh.error} />;
  const h = hh.data;
  const latest = h.latest_observation;
  const cls = latest?.analysis?.classification;
  const open = h.open_incidents || [];

  return (
    <div className="space-y-6">
      <section className="card overflow-hidden fade-in">
        <div className="grid lg:grid-cols-[1.1fr_1fr]">
          <div className="p-6 lg:p-8 flex flex-col">
            <div className="label">Essential route · latest observation</div>
            <div className="mt-3"><ClassBadge c={cls} big /></div>
            <p className="text-fog-200 text-[17px] leading-relaxed mt-4 max-w-xl">
              {latest ? latest.analysis.explanation : "No observation yet. Trigger a Ring capture or a labelled test scenario from the Demo lab."}
            </p>
            {latest && <ViewScope c={cls} className="mt-2 max-w-xl" />}
            {latest && (
              <div className="flex flex-wrap items-center gap-2 mt-4 text-[13px] text-fog-400">
                <SourceBadge s={latest.media_source} title={latest.analysis.provenance?.media_source_note} />
                <span>captured {ago(latest.captured_at)}</span>
                <span>·</span>
                <span className="mono">{latest.analysis.mode} mode</span>
              </div>
            )}
            <div className="mt-6 p-4 rounded-xl bg-ink-850 border border-ink-700 text-[14px]">
              <div className="label mb-1">This household's need</div>
              <div className="text-fog-100">{h.need}</div>
            </div>
            <p className="text-[12.5px] text-fog-400 mt-auto pt-6">
              Camera-based visual assessment of the marked route only — not a certified accessibility or regulatory assessment.
            </p>
          </div>
          <div className="p-4 lg:p-5 bg-ink-850/60 border-t lg:border-t-0 lg:border-l border-ink-700/60">
            {latest ? <EvidenceViewer obs={latest} compact caption="Teal: marked route · dashed blue: delivery zone · hatched red: estimated ground footprint · dashed amber: uncertainty envelope" /> : <Empty title="No frames yet" />}
          </div>
        </div>
      </section>

      <div className="grid lg:grid-cols-3 gap-6">
        <Card title="Incidents requiring attention" className="lg:col-span-2" right={<button className="btn-ghost !min-h-0 !py-1.5 text-[13px]" onClick={() => go("incidents")}>All incidents <ArrowRight size={14} /></button>}>
          {open.length === 0 ? <Empty title="Nothing open">No unresolved route incidents for this household.</Empty> : (
            <ul className="divide-y divide-ink-700/70">
              {open.map((i: any) => (
                <li key={i.id}>
                  <button className="w-full flex items-center gap-4 py-3 text-left hover:bg-ink-800/60 rounded-lg px-2" onClick={() => go("incidents", i.id)}>
                    <StateBadge s={i.state} />
                    <span className="mono text-fog-300">{i.id}</span>
                    <span className="text-fog-400 text-[13px] ml-auto">opened {ago(i.opened_at)}</span>
                    <ArrowRight size={16} className="text-fog-400" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Cameras">
          <ul className="space-y-3">
            {h.cameras.map((c: any) => (
              <li key={c.id} className="flex gap-3">
                <Camera size={18} className={c.kind === "ring" ? "text-sky-400 mt-0.5" : "text-fog-400 mt-0.5"} aria-hidden />
                <div className="min-w-0">
                  <div className="text-[14px]">{c.name}</div>
                  <div className="text-[12.5px] text-fog-400 flex flex-wrap gap-x-2">
                    <span className={c.authorized ? "text-teal-300" : "text-amber-300"}>{c.authorized ? "access authorised" : "not authorised"}</span>
                    <span>·</span>
                    <span>{c.calibration ? `${c.calibration.mode} calibration ${c.calibration.version}` : "no route marked"}</span>
                  </div>
                </div>
              </li>
            ))}
          </ul>
          <div className="mt-4 pt-4 border-t border-ink-700/60 flex items-start gap-2 text-[12.5px] text-fog-400">
            <ShieldCheck size={16} className="text-teal-400 shrink-0" aria-hidden />
            Accessibility support, not surveillance: no face recognition or identity inference, no continuous video. ClearWay keeps one still frame and one annotated copy per observation for the configured retention period; footage is never shared automatically.
          </div>
        </Card>
      </div>
    </div>
  );
}
