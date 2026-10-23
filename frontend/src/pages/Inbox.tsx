import { useQuery } from "@tanstack/react-query";
import { Mail } from "lucide-react";
import { api, fmtTime, hhPath, session } from "../api";
import { Card, Empty, Spinner } from "../components/ui";

export default function InboxPage() {
  const q = useQuery({ queryKey: ["inbox", session()?.id], queryFn: () => api(hhPath("/inbox")) });
  return (
    <Card title="Helper inbox" right={<span className="chip border-amber-400/40 text-amber-300">SIMULATED LOCAL CHANNEL</span>}>
      <p className="text-[13px] text-fog-400 mb-4">No external messaging service is configured, so approved requests are delivered to this built-in local inbox. Delivery here is real (stored and ledgered); it is not SMS or email.</p>
      {q.isLoading ? <Spinner /> : !q.data?.length ? <Empty title="No messages">Approved assistance requests appear here.</Empty> : (
        <ul className="space-y-3">
          {q.data.map((m: any) => (
            <li key={m.id} className="rounded-xl border border-ink-700 bg-ink-850 p-4">
              <div className="flex flex-wrap items-center gap-2 text-[13px]"><Mail size={15} className="text-teal-400" /><span className="font-medium text-fog-100">{m.subject}</span><span className="text-fog-400 ml-auto">{fmtTime(m.delivered_at)}</span></div>
              <div className="text-[12.5px] text-fog-400 mt-1">To {m.member_name} · incident <span className="mono">{m.incident_id}</span> · <span className="mono">{m.channel}</span></div>
              <p className="mt-3 text-[14px] leading-relaxed text-fog-200">{m.body}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
