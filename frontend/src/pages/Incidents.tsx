import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronRight, HelpCircle, Send, ShieldAlert, Shuffle } from "lucide-react";
import { ago, api, fmtTime, hhPath, resolutionText, session } from "../api";
import { EvidenceViewer } from "../components/EvidenceViewer";
import { Card, ClassBadge, Empty, ErrorNote, Guidance, IdentitySelect, SourceBadge, Spinner, StateBadge, TrustBadge, ViewScope } from "../components/ui";
import type { PageId } from "../App";

const STEPS = ["Observed", "Assessed", "Help approved", "Recheck", "Result"];

function progress(inc: any) {
  const kinds = inc.ledger.map((e: any) => (e.kind === "STATE_TRANSITION" ? e.payload.to : e.kind));
  const done = [true, kinds.includes("ASSESSED"), kinds.includes("ASSISTANCE_REQUESTED"), kinds.includes("REASSESSED"), inc.state === "RESOLVED"];
  return done;
}

export default function Incidents({ focus, setFocus, go }: { focus: string | null; setFocus: (s: string) => void; go: (p: PageId, i?: string) => void }) {
  const list = useQuery({ queryKey: ["incidents", session()?.id], queryFn: () => api(hhPath("/incidents")) });
  useEffect(() => { if (!focus && list.data?.length) setFocus(list.data[0].id); }, [list.data]);
  if (list.isLoading) return <Spinner />;
  if (!list.data?.length) return <Empty title="No incidents yet">Incidents open automatically when a fresh observation indicates the marked route may be obstructed or needs review.</Empty>;
  return (
    <div className="grid xl:grid-cols-[300px_1fr] gap-5">
      <Card title="Incidents">
        <ul className="space-y-1.5 -mx-2">
          {list.data.map((i: any) => (
            <li key={i.id}>
              <button onClick={() => setFocus(i.id)} aria-current={focus === i.id}
                className={`w-full text-left px-3 py-2.5 rounded-xl border ${focus === i.id ? "bg-ink-800 border-teal-400/50" : "border-transparent hover:bg-ink-800/60"}`}>
                <div className="flex items-center justify-between gap-2"><span className="mono text-fog-200">{i.id}</span><ChevronRight size={15} className="text-fog-400" /></div>
                <div className="mt-1.5 flex items-center gap-2"><StateBadge s={i.state} /><span className="text-[12px] text-fog-400">{ago(i.updated_at)}</span></div>
              </button>
            </li>
          ))}
        </ul>
      </Card>
      {focus && <Detail id={focus} go={go} />}
    </div>
  );
}

function Detail({ id, go }: { id: string; go: (p: PageId, i?: string) => void }) {
  const qc = useQueryClient();
  const inc = useQuery({ queryKey: ["incident", id], queryFn: () => api(hhPath(`/incidents/${id}`)) });
  const hh = useQuery({ queryKey: ["household", session()?.id], queryFn: () => api(hhPath()) });
  const [approver, setApprover] = useState("");
  const [reason, setReason] = useState("");
  const [overrider, setOverrider] = useState("");
  const inv = () => qc.invalidateQueries();
  const draft = useMutation({ mutationFn: () => api(hhPath(`/incidents/${id}/assistance/draft`), { method: "POST" }), onSuccess: inv });
  const approveSend = useMutation({
    mutationFn: async (aid: string) => {
      await api(hhPath(`/actions/${aid}/approve`), { method: "POST", member: approver });
      return api(hhPath(`/actions/${aid}/send`), { method: "POST" });
    }, onSettled: inv,
  });
  const emit = useMutation({ mutationFn: (scene: string) => api(hhPath("/simulator/emit"), { method: "POST", json: { scene } }), onSettled: inv });
  const capture = useMutation({ mutationFn: (cam: string) => api(hhPath(`/ring/${cam}/capture`), { method: "POST" }), onSettled: inv });
  const override = useMutation({ mutationFn: () => api(hhPath(`/incidents/${id}/override`), { method: "POST", member: overrider, json: { reason } }), onSettled: inv });
  const sess = session()!;

  if (inc.isLoading || !inc.data) return <Spinner />;
  const d = inc.data;
  const members = hh.data?.members || [];
  const latest = d.observations[d.observations.length - 1];
  const origin = d.observations[0];
  const pending = d.actions.find((a: any) => a.status === "DRAFT");
  const sent = [...d.actions].reverse().find((a: any) => a.status === "SENT");
  const done = progress(d);
  const camera = hh.data?.cameras?.find((c: any) => c.id === d.camera_id);
  const open = d.state !== "RESOLVED";
  const obsById = Object.fromEntries(d.observations.map((o: any) => [o.id, o]));

  return (
    <div className="space-y-5 min-w-0">
      <Card title={<span className="flex items-center gap-3">Incident <span className="mono text-fog-300">{d.id}</span></span>} right={<><TrustBadge trust={d.namespace} source={d.observations?.[0]?.media_source} /><StateBadge s={d.state} /></>}>
        <ol className="grid grid-cols-5 gap-2" aria-label="Resolution progress">
          {STEPS.map((s, i) => (
            <li key={s} className="min-w-0">
              <div className={`h-1.5 rounded-full ${done[i] ? (i === 4 ? "bg-teal-400" : "bg-teal-500/80") : i === 4 && d.state === "PARTIALLY_RESOLVED" ? "bg-amber-400" : "bg-ink-700"}`} />
              <div className={`mt-2 text-[12.5px] ${done[i] ? "text-fog-100" : "text-fog-400"}`}>{done[i] && <Check size={12} className="inline mr-1 text-teal-400" />}{s}</div>
            </li>
          ))}
        </ol>
        <p className="mt-4 text-[13px] text-fog-400">
          {d.namespace === "LIVE"
            ? (d.observations?.[0]?.media_source === "RING_API_EMULATOR"
              ? "Live-path incident on the Ring API emulator: frames are captured by ClearWay over WHEP from the emulator (not Ring footage); the live evidence rules apply in full."
              : "Live incident: only frames ClearWay captures itself from the Ring camera can verify it, and closure needs two independent clear observations.")
            : "Demo incident: driven by labelled test evidence (simulator, synthetic renders, uploads or replays). It never affects a live Ring incident."}
        </p>
        {d.resolution && <div className={`mt-2 text-[13.5px] ${d.resolution.startsWith("MANUAL") ? "text-amber-300" : "text-teal-300"}`}>{resolutionText(d)} · {fmtTime(d.closed_at)}</div>}
      </Card>

      <div className="grid 2xl:grid-cols-2 gap-5">
        <Card title="Opening evidence" right={<span className="text-[12px] text-fog-400">{fmtTime(origin?.captured_at)}</span>}>{origin && <EvidenceViewer obs={origin} compact />}</Card>
        <Card title="Latest evidence" right={<span className="text-[12px] text-fog-400">{fmtTime(latest?.captured_at)}</span>}>
          {latest && <EvidenceViewer obs={latest} compact />}
          {latest && <p className="text-[13.5px] text-fog-300 mt-3">{latest.analysis.explanation}</p>}
          {latest && <ViewScope c={latest.classification} className="mt-1.5" />}
          {latest?.analysis?.verification_guidance?.length > 0 && <div className="mt-3"><Guidance items={latest.analysis.verification_guidance} /></div>}
        </Card>
      </div>

      <div className="grid 2xl:grid-cols-[1.2fr_1fr] gap-5">
        <Card title={<span className="flex items-center gap-2"><HelpCircle size={16} className="text-teal-400" /> Why this decision?</span>}>
          <ol className="relative border-l border-ink-600 ml-2 space-y-5">
            {d.why.map((w: any, i: number) => {
              const o = w.observation_id ? obsById[w.observation_id] : null;
              const deny = w.title.startsWith("Authorization") || w.title.startsWith("Evidence");
              return (
                <li key={i} className="pl-5 relative">
                  <span className={`absolute -left-[7px] top-1 w-3.5 h-3.5 rounded-full border-2 border-ink-900 ${deny ? "bg-amber-400" : w.title.endsWith("RESOLVED") ? "bg-teal-400" : "bg-fog-400"}`} />
                  <div className="flex flex-wrap items-center gap-2"><span className="font-medium text-fog-100">{w.title}</span><span className="text-[12px] text-fog-400">{fmtTime(w.ts)}</span></div>
                  <ul className="mt-1 text-[13.5px] text-fog-300 space-y-0.5">{(w.reasons || []).filter(Boolean).map((r: string, k: number) => <li key={k}>{r}</li>)}</ul>
                  {w.metric != null && w.previous_metric != null && <div className="text-[12.5px] mono text-fog-400 mt-1">metric {Number(w.previous_metric).toFixed(1)} → {Number(w.metric).toFixed(1)}</div>}
                  {o && (
                    <div className="mt-2 flex items-center gap-3">
                      <img src={o.evidence_url} alt="" className="w-28 rounded-lg border border-ink-700" />
                      <div className="space-y-1"><ClassBadge c={o.classification} /><div><SourceBadge s={o.media_source} /></div><div className="mono text-[11px] text-fog-400">{o.id}</div></div>
                    </div>
                  )}
                  <div className="mono text-[10.5px] text-fog-400 mt-1" title="ledger entry hash">#{w.entry_hash.slice(0, 16)}</div>
                </li>
              );
            })}
          </ol>
        </Card>

        <div className="space-y-5">
          <Card title="Authorised assistance">
            {!open ? <p className="text-fog-300 text-[14px]">Incident closed.</p> : pending ? (
              <div className="space-y-3">
                <div className="label">Draft message to {members.find((m: any) => m.id === pending.recipient_member_id)?.name}</div>
                <blockquote className="text-[14px] leading-relaxed bg-ink-850 border border-ink-700 rounded-xl p-3 text-fog-200">{pending.message}</blockquote>
                <IdentitySelect id="appr" label="Approve as" value={approver} onChange={setApprover} identities={sess.identities} demo={sess.demo} />
                <button className="btn-primary w-full" disabled={!approver || approveSend.isPending} onClick={() => approveSend.mutate(pending.id)}><Send size={16} /> Approve & send via local inbox</button>
                <ErrorNote error={approveSend.error} />
                <p className="text-[12px] text-fog-400">Nothing is sent without an approval from an authenticated approving member. The approver's identity comes from their credential, not from this form. Channel: simulated local inbox (no external messaging configured).</p>
              </div>
            ) : (
              <div className="space-y-3">
                {sent && <div className="text-[13.5px] text-fog-300">Last request delivered {ago(sent.sent_at)} to <span className="text-fog-100">{members.find((m: any) => m.id === sent.recipient_member_id)?.name}</span>. Sending help does not close the incident.</div>}
                <button className="btn-ghost w-full" onClick={() => draft.mutate()} disabled={draft.isPending}>{sent ? "Draft another request" : "Draft assistance request"}</button>
                <ErrorNote error={draft.error} />
              </div>
            )}
          </Card>

          {open && (
            <Card title="Request new evidence">
              <p className="text-[13.5px] text-fog-300 mb-3">Closure requires a new, distinct observation; the geometry is recomputed from scratch.</p>
              <div className="flex flex-col gap-2">
                {camera?.kind === "ring" && <button className="btn-primary" onClick={() => capture.mutate(camera.id)} disabled={capture.isPending}>Capture from Ring now</button>}
                {camera?.kind === "synthetic" && (
                  <>
                    <div className="label mt-1">Labelled test scenes (signed simulator events)</div>
                    <button className="btn-ghost justify-start" onClick={() => emit.mutate("partial_move")} disabled={emit.isPending}>Correction attempt: parcel nudged aside</button>
                    <button className="btn-ghost justify-start" onClick={() => emit.mutate("moved_to_zone")} disabled={emit.isPending}>Correction attempt: parcel moved to delivery zone</button>
                    <button className="btn-ghost justify-start" onClick={() => emit.mutate("obscured")} disabled={emit.isPending}>Camera view obscured</button>
                  </>
                )}
                <button className="btn-ghost justify-start" onClick={() => go("counterfactual", d.id)}><Shuffle size={16} /> Open counterfactual planner</button>
              </div>
              <ErrorNote error={emit.error || capture.error} />
            </Card>
          )}

          {open && (
            <Card title={<span className="flex items-center gap-2"><ShieldAlert size={16} className="text-amber-400" /> Manual override</span>}>
              <div className="space-y-2">
                <IdentitySelect id="ovr" label="Closing as" value={overrider} onChange={setOverrider} identities={sess.identities} demo={sess.demo} />
                <p className="text-[12px] text-fog-400">A manual close is recorded as “not camera-verified” and kept separate from camera-verified closures.</p>
                <textarea className="input w-full min-h-[70px]" placeholder="Reason (recorded in the ledger)" value={reason} onChange={(e) => setReason(e.target.value)} />
                <button className="btn-danger w-full" disabled={!overrider || reason.trim().length < 4 || override.isPending} onClick={() => override.mutate()}>Close with override</button>
                <ErrorNote error={override.error} />
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
