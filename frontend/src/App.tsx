import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, BookLock, FlaskConical, Home, Inbox, Radio, ScanLine, Shuffle, WifiOff } from "lucide-react";
import { api, ApiError, Identity, Session, setSession, useLiveUpdates } from "./api";
import { Spinner } from "./components/ui";
import Overview from "./pages/Overview";
import SpatialTwin from "./pages/SpatialTwin";
import Incidents from "./pages/Incidents";
import Counterfactual from "./pages/Counterfactual";
import Ledger from "./pages/Ledger";
import InboxPage from "./pages/Inbox";
import RingPage from "./pages/Ring";
import Lab from "./pages/Lab";

const NAV = [
  { id: "overview", label: "Household overview", icon: Home },
  { id: "twin", label: "Spatial twin", icon: ScanLine },
  { id: "incidents", label: "Incident resolution", icon: Activity },
  { id: "counterfactual", label: "Counterfactual planner", icon: Shuffle },
  { id: "ledger", label: "Proof ledger", icon: BookLock },
  { id: "inbox", label: "Helper inbox", icon: Inbox },
  { id: "ring", label: "Ring integration", icon: Radio },
  { id: "lab", label: "Demo lab & evaluation", icon: FlaskConical },
] as const;
export type PageId = (typeof NAV)[number]["id"];

export default function App() {
  const qc = useQueryClient();
  const [page, setPage] = useState<PageId>(() => (location.hash.slice(1) as PageId) || "overview");
  const [sess, setSess] = useState<Session | null>(null);
  const [focusIncident, setFocusIncident] = useState<string | null>(null);
  const [live, setLive] = useState<Session | null>(() => { try { return JSON.parse(sessionStorage.getItem("cw-live") || "null"); } catch { return null; } });
  const [signingIn, setSigningIn] = useState(false);
  // Demo households (public demo credentials) — absent when the server runs with demo mode off.
  const hhs = useQuery({
    queryKey: ["demo-households"], retry: false,
    queryFn: async () => {
      try {
        const d = await api<any[]>("/api/demo/households");
        return d.map((h): Session => ({ id: h.id, name: h.name, need: h.need, token: h.token, demo: true, identities: h.identities }));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return [] as Session[];
        throw e;
      }
    },
  });
  const sys = useQuery({ queryKey: ["system"], queryFn: () => api("/api/system"), refetchInterval: 30000 });
  const all = [...(hhs.data || []), ...(live ? [live] : [])];

  useEffect(() => {
    if (!hhs.data || sess) return;
    let saved: string | null = null;
    try { saved = localStorage.getItem("cw-hh"); } catch { /* storage unavailable */ }
    const pick = all.find((h) => h.id === saved) || all.find((h) => h.id === "hh_b") || all[0];
    if (pick) choose(pick);
  }, [hhs.data]);
  useEffect(() => { location.hash = page; }, [page]);

  function choose(s: Session) {
    setSession(s);
    setSess(s);
    setSigningIn(false);
    try { localStorage.setItem("cw-hh", s.id); } catch { /* storage unavailable */ }
    qc.invalidateQueries();
  }
  function onLive(s: Session) {
    setLive(s);
    try { sessionStorage.setItem("cw-live", JSON.stringify(s)); } catch { /* storage unavailable */ }
    choose(s);
  }
  function signOutLive() {
    setLive(null);
    try { sessionStorage.removeItem("cw-live"); } catch { /* storage unavailable */ }
    const next = hhs.data?.[0] || null;
    setSession(next); setSess(next);
    qc.invalidateQueries();
  }
  const go = (p: PageId, incident?: string) => { if (incident) setFocusIncident(incident); setPage(p); window.scrollTo({ top: 0 }); };

  return (
    <div className="min-h-full flex">
      <aside className="hidden lg:flex w-[268px] shrink-0 flex-col border-r border-ink-700/70 bg-ink-900/60 sticky top-0 h-screen">
        <div className="px-5 pt-6 pb-5">
          <div className="flex items-center gap-2.5">
            <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="8" fill="#141E31" /><path d="M9 26 L13 6 M23 26 L19 6" stroke="#2DD4BF" strokeWidth="3" strokeLinecap="round" /><path d="M14.5 17h3" stroke="#F5A524" strokeWidth="2.5" strokeLinecap="round" /></svg>
            <div>
              <div className="font-display font-bold text-[17px] leading-none tracking-tight">ClearWay</div>
              <div className="text-[11px] text-fog-400 tracking-[0.18em] uppercase mt-1">AccessGraph 2.0</div>
            </div>
          </div>
        </div>
        <nav className="px-3 flex-1 overflow-y-auto" aria-label="Main">
          {NAV.map((n) => {
            const I = n.icon;
            const active = page === n.id;
            return (
              <button key={n.id} onClick={() => go(n.id)} aria-current={active ? "page" : undefined}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-[14px] mb-0.5 transition-colors ${active ? "bg-ink-700 text-fog-100" : "text-fog-300 hover:bg-ink-800 hover:text-fog-100"}`}>
                <I size={17} className={active ? "text-teal-400" : ""} aria-hidden /> {n.label}
              </button>
            );
          })}
        </nav>
        <div className="p-4 border-t border-ink-700/70 space-y-2 text-[12px]">
          <SysLine ok={sys.data?.ring?.configured} label={sys.data?.ring?.configured ? `Ring API configured (${sys.data.ring.source_label})` : "Ring API not configured"} />
          <SysLine ok={sys.data?.aws?.enabled} label={sys.data?.aws?.enabled ? `Bedrock: ${sys.data.aws.model_id}` : "AWS Bedrock disabled"} />
          <SysLine ok label={`No face recognition · stills only, ${sys.data?.privacy?.retention_days ?? "–"}-day retention`} />
        </div>
      </aside>

      <main className="flex-1 min-w-0 overflow-x-hidden">
        <header className="sticky top-0 z-20 bg-ink-950/85 backdrop-blur border-b border-ink-700/60">
          <div className="max-w-[1400px] mx-auto px-4 lg:px-8 min-h-[64px] py-2 flex flex-wrap items-center gap-2 sm:gap-4">
            <select className="input lg:hidden flex-1 min-w-0" value={page} onChange={(e) => go(e.target.value as PageId)} aria-label="Page">
              {NAV.map((n) => <option key={n.id} value={n.id}>{n.label}</option>)}
            </select>
            <h1 className="hidden lg:block font-display text-[19px] font-semibold tracking-tight">{NAV.find((n) => n.id === page)?.label}</h1>
            <div className="lg:ml-auto flex items-center gap-3 flex-1 lg:flex-none min-w-0">
              {sys.isError && <span className="chip border-coral-500/40 text-coral-400"><WifiOff size={13} /> backend offline</span>}
              <label className="label hidden sm:block" htmlFor="hh">Household</label>
              <select id="hh" className="input w-full lg:w-auto lg:min-w-[220px] min-w-0" value={signingIn ? "__signin" : sess?.id || ""}
                onChange={(e) => { if (e.target.value === "__signin") { setSigningIn(true); return; } const s = all.find((h) => h.id === e.target.value); if (s) choose(s); }}>
                {all.map((h) => <option key={h.id} value={h.id}>{h.demo ? "Demo · " : "Live · "}{h.name}</option>)}
                <option value="__signin">Sign in to a live household…</option>
              </select>
              {live && sess?.id === live.id && <button className="btn-ghost !min-h-0 !py-1.5 text-[13px] shrink-0" onClick={signOutLive}>Sign out</button>}
            </div>
          </div>
        </header>
        <div className="max-w-[1400px] mx-auto px-4 lg:px-8 py-6">
          {hhs.isError ? (
            <div className="card card-pad text-coral-400">Cannot reach the ClearWay backend. Start it with <span className="mono">uvicorn app.main:app</span>.</div>
          ) : signingIn || (hhs.data && !all.length) ? (
            <SignIn onSignedIn={onLive} onCancel={all.length ? () => setSigningIn(false) : undefined} />
          ) : !sess ? <Spinner label="Connecting" /> : (
            <Live key={sess.id}>
              {page === "overview" && <Overview go={go} />}
              {page === "twin" && <SpatialTwin />}
              {page === "incidents" && <Incidents focus={focusIncident} setFocus={setFocusIncident} go={go} />}
              {page === "counterfactual" && <Counterfactual focus={focusIncident} setFocus={setFocusIncident} />}
              {page === "ledger" && <Ledger />}
              {page === "inbox" && <InboxPage />}
              {page === "ring" && <RingPage />}
              {page === "lab" && <Lab go={go} />}
            </Live>
          )}
        </div>
      </main>
    </div>
  );
}

function Live({ children }: { children: any }) {
  useLiveUpdates();
  return children;
}

function SysLine({ ok, label }: { ok?: boolean; label: string }) {
  return <div className="flex items-center gap-2 text-fog-300"><span className={`w-2 h-2 rounded-full ${ok ? "bg-teal-400" : "bg-fog-400"}`} aria-hidden />{label}</div>;
}


function SignIn({ onSignedIn, onCancel }: { onSignedIn: (s: Session) => void; onCancel?: () => void }) {
  const [id, setId] = useState("hh_live");
  const [token, setToken] = useState("");
  const [approver, setApprover] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const headers: Record<string, string> = { "X-Household-Token": token };
      if (approver) headers["X-Member-Token"] = approver;
      const r = await fetch(`/api/households/${encodeURIComponent(id)}`, { headers });
      if (!r.ok) throw new Error(r.status === 401 ? "Household ID or token not recognised." : `Sign-in failed (${r.status}).`);
      const h = await r.json();
      if (approver && !h.me) throw new Error("The approver credential is not a member of this household.");
      const identities: Identity[] = h.me ? [{ ...h.me, token: approver }] : [];
      onSignedIn({ id: h.id, name: h.name, need: h.need, token, demo: !!h.is_demo, identities });
    } catch (x: any) {
      setErr(x.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="card card-pad max-w-lg mx-auto mt-6 space-y-4 fade-in">
      <h2 className="font-display text-[19px] font-semibold">Sign in to a live household</h2>
      <p className="text-[13.5px] text-fog-300">Live households are provisioned by the server operator (CLEARWAY_LIVE_* settings) and are the only ones that can use a Ring camera. Credentials stay in this browser tab (session storage) and are sent only in request headers.</p>
      <div><label className="label block mb-1.5" htmlFor="si-id">Household ID</label><input id="si-id" className="input w-full" value={id} onChange={(e) => setId(e.target.value)} autoComplete="username" /></div>
      <div><label className="label block mb-1.5" htmlFor="si-tok">Household token</label><input id="si-tok" type="password" className="input w-full" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="current-password" required /></div>
      <div><label className="label block mb-1.5" htmlFor="si-app">Approver token (optional — needed to approve help or authorise the camera)</label><input id="si-app" type="password" className="input w-full" value={approver} onChange={(e) => setApprover(e.target.value)} autoComplete="off" /></div>
      {err && <div role="alert" className="rounded-xl border border-coral-500/40 bg-coral-900/40 text-coral-400 px-4 py-3 text-sm">{err}</div>}
      <div className="flex gap-3"><button className="btn-primary" disabled={busy || !token}>{busy ? "Checking…" : "Sign in"}</button>{onCancel && <button type="button" className="btn-ghost" onClick={onCancel}>Back to demo</button>}</div>
    </form>
  );
}
