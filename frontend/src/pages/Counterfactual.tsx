import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Info } from "lucide-react";
import { api, fmtTime, hhPath, session } from "../api";
import { EvidenceViewer } from "../components/EvidenceViewer";
import { Card, ClassBadge, Empty, num, Spinner } from "../components/ui";

export default function Counterfactual({ focus, setFocus }: { focus: string | null; setFocus: (s: string) => void }) {
  const list = useQuery({ queryKey: ["incidents", session()?.id], queryFn: () => api(hhPath("/incidents")) });
  useEffect(() => { if (!focus && list.data?.length) setFocus(list.data[0].id); }, [list.data]);
  const inc = useQuery({ queryKey: ["incident", focus], queryFn: () => api(hhPath(`/incidents/${focus}`)), enabled: !!focus });
  if (list.isLoading) return <Spinner />;
  if (!list.data?.length) return <Empty title="No incidents to plan for">The planner runs when an observation indicates a potential obstruction.</Empty>;
  const d = inc.data;
  const withCf = d?.observations?.filter((o: any) => o.analysis.counterfactual) || [];
  const base = withCf[0];
  const cf = base?.analysis.counterfactual;
  const fresh = d?.observations?.filter((o: any) => base && o.captured_at > base.captured_at) || [];
  const latest = fresh[fresh.length - 1];
  const metricOf = (o: any) => {
    const ob = o?.analysis.objects?.find((x: any) => x.id === o.analysis.primary_object);
    const cw = ob?.nominal?.clear_width;
    return cw ? `${num(cw.min_clear_width, 0)} cm clear` : ob ? `${(ob.nominal.fraction_of_footprint * 100).toFixed(0)}% footprint in route` : "no object";
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <label className="label" htmlFor="inc">Incident</label>
        <select id="inc" className="input" value={focus || ""} onChange={(e) => setFocus(e.target.value)}>
          {list.data.map((i: any) => <option key={i.id} value={i.id}>{i.id} · {i.state.toLowerCase()}</option>)}
        </select>
      </div>
      {!d ? <Spinner /> : !cf ? <Empty title="No counterfactual for this incident">The planner runs on potential-obstruction or review-required observations.</Empty> : (
        <>
          <div className="grid lg:grid-cols-[1fr_auto_1fr_auto_1fr] items-start gap-3">
            <Pane title="1 · Current placement" sub={`${fmtTime(base.captured_at)} · ${metricOf(base)}`}>
              <EvidenceViewer obs={base} layers={["route", "zone", "footprint"]} compact />
              <div className="mt-2"><ClassBadge c={base.classification} /></div>
            </Pane>
            <ArrowRight className="hidden lg:block text-fog-400 mt-40" aria-hidden />
            <Pane title="2 · Proposed placement (simulation)" sub={cf.resolves ? `move ≈ ${cf.move_distance} ${cf.units} → ${cf.target === "drop_zone" ? "delivery zone" : "beside route"}` : "no clearing placement found"}>
              <EvidenceViewer obs={base} layers={["route", "zone", "proposed"]} showProposed compact />
              <div className="mt-2"><span className={`chip ${cf.resolves ? "text-teal-300 border-teal-400/40" : "text-amber-300 border-amber-400/40"}`}>{cf.resolves ? "Would remove apparent overlap" : "Would not resolve"}</span></div>
            </Pane>
            <ArrowRight className="hidden lg:block text-fog-400 mt-40" aria-hidden />
            <Pane title="3 · Freshly observed placement" sub={latest ? `${fmtTime(latest.captured_at)} · ${metricOf(latest)}` : "awaiting new evidence"}>
              {latest ? (<><EvidenceViewer obs={latest} layers={["route", "zone", "footprint"]} compact /><div className="mt-2"><ClassBadge c={latest.classification} /></div></>)
                : <div className="aspect-[4/3] rounded-xl border border-dashed border-ink-600 grid place-items-center text-fog-400 text-sm p-6 text-center">No observation after the plan yet. The proposal is not evidence that anything moved.</div>}
            </Pane>
          </div>
          <Card title={<span className="flex items-center gap-2"><Info size={16} className="text-amber-400" /> How to read this</span>}>
            <p className="text-[14px] text-fog-200">{cf.message}</p>
            <ul className="mt-3 text-[13.5px] text-fog-300 list-disc pl-5 space-y-1">{cf.caveats.map((c: string) => <li key={c}>{c}</li>)}<li>Safety margin kept from the route: {cf.safety_margin} {cf.units}. Footprint fits delivery zone: {cf.fits_drop_zone ? "yes" : "no"}.</li></ul>
            {fresh.length > 1 && (
              <div className="mt-4">
                <div className="label mb-2">All observations after the plan</div>
                <div className="flex flex-wrap gap-2">{fresh.map((o: any) => <span key={o.id} className="chip border-ink-600 text-fog-300"><span className="mono">{o.id}</span> {metricOf(o)}</span>)}</div>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}

function Pane({ title, sub, children }: any) {
  return (
    <section className="card p-4 min-w-0 fade-in">
      <h3 className="font-display font-semibold text-[14.5px]">{title}</h3>
      <div className="text-[12.5px] text-fog-400 mb-3 mt-0.5">{sub}</div>
      {children}
    </section>
  );
}
