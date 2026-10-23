import { Fragment, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link2, ShieldCheck, ShieldX } from "lucide-react";
import { api, fmtTime, hhPath, session } from "../api";
import { Card, Spinner } from "../components/ui";

const KIND_TONE: Record<string, string> = {
  STATE_TRANSITION: "text-teal-300", AUTHORIZATION_DENIED: "text-coral-400", EVIDENCE_REJECTED: "text-amber-300",
  OBSERVATION_ANALYZED: "text-sky-400", ASSISTANCE_SENT: "text-fog-100", DUPLICATE_EVENT_IGNORED: "text-amber-300",
};

export default function Ledger() {
  const led = useQuery({ queryKey: ["ledger", session()?.id], queryFn: () => api(hhPath("/ledger?limit=300")) });
  const graph = useQuery({ queryKey: ["graph", session()?.id], queryFn: () => api(hhPath("/graph")) });
  const verify = useMutation({ mutationFn: () => api(hhPath("/ledger/verify")) });
  const [open, setOpen] = useState<number | null>(null);
  const [filter, setFilter] = useState("");
  const rows = (led.data || []).filter((r: any) => !filter || r.kind.includes(filter));
  const kinds = Array.from(new Set((led.data || []).map((r: any) => r.kind))) as string[];

  return (
    <div className="grid 2xl:grid-cols-[1.4fr_1fr] gap-5">
      <Card title="Append-only evidence ledger" right={
        <>
          <select className="input !min-h-0 !py-1 text-[13px]" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by kind">
            <option value="">All entries</option>{kinds.map((k) => <option key={k}>{k}</option>)}
          </select>
          <button className="btn-ghost !min-h-0 !py-1.5 text-[13px]" onClick={() => verify.mutate()}>Verify hash chain</button>
        </>}>
        {verify.data && (
          <div className={`mb-4 rounded-xl px-4 py-3 text-[13.5px] flex items-center gap-2 ${verify.data.intact ? "bg-teal-900/50 text-teal-300 border border-teal-400/30" : "bg-coral-900/50 text-coral-400 border border-coral-500/40"}`}>
            {verify.data.intact ? <ShieldCheck size={16} /> : <ShieldX size={16} />}
            {verify.data.intact ? `Chain intact · ${verify.data.entries} entries recomputed · head ${verify.data.head.slice(0, 16)}…` : `Chain broken at entry ${verify.data.broken_at_seq}`}
          </div>
        )}
        <p className="text-[12.5px] text-fog-400 mb-3">Each entry stores SHA-256(previous hash + canonical entry). SQLite triggers reject UPDATE and DELETE on this table, so accidental edits are detectable and blocked.</p>
        {led.isLoading ? <Spinner /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead className="text-fog-400 text-left"><tr><th className="font-normal py-2 pr-3">#</th><th className="font-normal pr-3">Time</th><th className="font-normal pr-3">Kind</th><th className="font-normal pr-3">Incident</th><th className="font-normal">Hash</th></tr></thead>
              <tbody>
                {rows.map((r: any) => (
                  <Fragment key={r.seq}>
                    <tr className="border-t border-ink-700/60 hover:bg-ink-800/50 cursor-pointer" onClick={() => setOpen(open === r.seq ? null : r.seq)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpen(open === r.seq ? null : r.seq)}>
                      <td className="py-2 pr-3 mono text-fog-400">{r.seq}</td>
                      <td className="pr-3 whitespace-nowrap text-fog-300">{fmtTime(r.ts)}</td>
                      <td className={`pr-3 mono ${KIND_TONE[r.kind] || "text-fog-200"}`}>{r.kind}{r.kind === "STATE_TRANSITION" && <span className="text-fog-300"> {r.payload.from}→{r.payload.to}</span>}</td>
                      <td className="pr-3 mono text-fog-400">{r.incident_id || "—"}</td>
                      <td className="mono text-fog-400">{r.entry_hash.slice(0, 12)}</td>
                    </tr>
                    {open === r.seq && (
                      <tr><td colSpan={5} className="pb-3">
                        <pre className="mono text-[11.5px] bg-ink-950 border border-ink-700 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap text-fog-200">{JSON.stringify(r.payload, null, 2)}{"\n\nprev: " + r.prev_hash + "\nhash: " + r.entry_hash}</pre>
                      </td></tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title={<span className="flex items-center gap-2"><Link2 size={16} className="text-teal-400" /> Temporal scene graph</span>}>
        <p className="text-[12.5px] text-fog-400 mb-3">Relationships derived from observations, actions and verifications (latest first).</p>
        <ul className="space-y-2 text-[12.5px]">
          {(graph.data || []).slice(0, 80).map((e: any) => (
            <li key={e.id} className="flex flex-wrap items-center gap-1.5 bg-ink-850 border border-ink-700/60 rounded-lg px-3 py-2">
              <span className="mono text-fog-300">{e.src_type}:{e.src_id.slice(-14)}</span>
              <span className={`mono font-medium ${e.rel.includes("CONTRADICTS") || e.rel.includes("DROP_ZONE") ? "text-teal-300" : e.rel.includes("OVERLAPS") || e.rel.includes("SUPPORTS") ? "text-coral-400" : "text-sky-400"}`}>{e.rel}</span>
              <span className="mono text-fog-300">{e.dst_type}:{String(e.dst_id).slice(-14)}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
