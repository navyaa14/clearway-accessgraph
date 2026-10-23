import { AlertTriangle, CheckCircle2, CircleHelp, EyeOff, Loader2, Radio, Film, FlaskConical, Box, Upload } from "lucide-react";
import type { ReactNode } from "react";

export const CLASS_META: Record<string, { label: string; tone: string; icon: any; short: string }> = {
  POTENTIAL_OBSTRUCTION: { label: "Potential obstruction", short: "Obstructed?", tone: "text-coral-400 border-coral-500/40 bg-coral-900/60", icon: AlertTriangle },
  NO_OBSTRUCTION_OBSERVED: { label: "No obstruction observed", short: "Clear (observed)", tone: "text-teal-300 border-teal-400/40 bg-teal-900/50", icon: CheckCircle2 },
  REVIEW_REQUIRED: { label: "Review required", short: "Review", tone: "text-amber-300 border-amber-400/40 bg-amber-900/50", icon: CircleHelp },
  UNABLE_TO_VERIFY: { label: "Unable to verify", short: "Can't verify", tone: "text-fog-200 border-fog-400/40 bg-ink-700", icon: EyeOff },
};

export function ClassBadge({ c, big }: { c?: string; big?: boolean }) {
  const m = CLASS_META[c || ""] || { label: c || "—", tone: "text-fog-300 border-ink-600 bg-ink-800", icon: CircleHelp };
  const I = m.icon;
  return (
    <span className={`chip ${m.tone} ${big ? "text-[14px] px-3 py-1.5" : ""}`}>
      <I size={big ? 16 : 13} aria-hidden /> {m.label}
    </span>
  );
}

const STATE_TONE: Record<string, string> = {
  RESOLVED: "text-teal-300 border-teal-400/40 bg-teal-900/50",
  PARTIALLY_RESOLVED: "text-amber-300 border-amber-400/40 bg-amber-900/50",
  REVIEW_REQUIRED: "text-amber-300 border-amber-400/40 bg-amber-900/50",
  UNABLE_TO_VERIFY: "text-fog-200 border-fog-400/40 bg-ink-700",
  AWAITING_NEW_EVIDENCE: "text-sky-400 border-sky-400/40 bg-ink-800",
  ASSISTANCE_REQUESTED: "text-sky-400 border-sky-400/40 bg-ink-800",
  ASSESSED: "text-coral-400 border-coral-500/40 bg-coral-900/60",
};

export function StateBadge({ s }: { s: string }) {
  return <span className={`chip ${STATE_TONE[s] || "text-fog-300 border-ink-600 bg-ink-800"}`}>{s.replace(/_/g, " ").toLowerCase()}</span>;
}

const SOURCE_META: Record<string, { label: string; icon: any; tone: string }> = {
  RING_PLAYGROUND: { label: "Ring Playground", icon: Radio, tone: "text-sky-400 border-sky-400/50 bg-ink-800" },
  RING_LIVE: { label: "Ring live", icon: Radio, tone: "text-sky-400 border-sky-400/50 bg-ink-800" },
  RING_API_EMULATOR: { label: "Ring API emulator — not Ring footage", icon: Radio, tone: "text-violet-300 border-violet-400/50 bg-ink-800" },
  INDEPENDENT_TEST_VIDEO: { label: "Independent test video — not Ring", icon: Upload, tone: "text-amber-300 border-amber-400/40 bg-amber-900/40" },
  RECORDED_REPLAY: { label: "Recorded replay", icon: Film, tone: "text-fog-200 border-fog-400/40 bg-ink-800" },
  SYNTHETIC_RENDER: { label: "Synthetic test render — not camera footage", icon: Box, tone: "text-fog-200 border-fog-400/40 bg-ink-800" },
  CUSTOM_EVENT_SIMULATOR: { label: "Custom event simulator — not a Ring event", icon: FlaskConical, tone: "text-fog-200 border-fog-400/40 bg-ink-800" },
};

export function SourceBadge({ s, title, compact }: { s?: string | null; title?: string; compact?: boolean }) {
  if (!s) return null;
  const m = SOURCE_META[s] || { label: s, icon: Box, tone: "text-fog-300 border-ink-600 bg-ink-800" };
  const I = m.icon;
  return <span title={title || m.label} className={`chip ${m.tone}`}><I size={13} aria-hidden /> <span className="mono">{s}</span>{!compact && <span className="hidden xl:inline text-fog-300 font-normal">· {m.label}</span>}</span>;
}

export function Card({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card fade-in ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 px-5 pt-4 pb-3 border-b border-ink-700/60">
          <h2 className="font-display text-[15px] font-semibold tracking-tight text-fog-100">{title}</h2>
          <div className="flex items-center gap-2">{right}</div>
        </header>
      )}
      <div className="card-pad">{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone = "" }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="label">{label}</div>
      <div className={`font-display text-[22px] font-semibold mt-1 tabular-nums ${tone}`}>{value}</div>
      {sub && <div className="text-[12.5px] text-fog-400 mt-0.5">{sub}</div>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <div className="flex items-center gap-2 text-fog-300 text-sm py-6 justify-center" role="status"><Loader2 className="animate-spin" size={18} /> {label}…</div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="text-center py-10 px-6 border border-dashed border-ink-600 rounded-xl">
      <div className="font-display text-fog-200 font-medium">{title}</div>
      {children && <div className="text-fog-400 text-sm mt-1.5 max-w-md mx-auto">{children}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: any }) {
  if (!error) return null;
  return <div role="alert" className="rounded-xl border border-coral-500/40 bg-coral-900/40 text-coral-400 px-4 py-3 text-sm">{String(error.message || error)}</div>;
}

export function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(120px,auto)_1fr] gap-x-4 gap-y-1.5 text-[13.5px]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-fog-400">{k}</dt>
          <dd className="text-fog-100 min-w-0 break-words">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export const pct = (x?: number | null, d = 0) => (x === null || x === undefined ? "—" : `${(x * 100).toFixed(d)}%`);
export const num = (x?: number | null, d = 1, u = "") => (x === null || x === undefined ? "—" : `${x.toFixed(d)}${u}`);

/** What evidence is missing and a safe way to get it. Never dispatches anyone, never declares the route safe. */
export function Guidance({ items }: { items?: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="rounded-xl border border-amber-400/30 bg-amber-900/25 p-3">
      <div className="label !text-amber-300 mb-1.5">To get a reliable answer</div>
      <ul className="text-[13.5px] text-fog-100 list-disc pl-5 space-y-1">{items.map((g) => <li key={g}>{g}</li>)}</ul>
    </div>
  );
}

/** Evidence trust: LIVE = frame captured by the server from Ring; DEMO = upload / replay / synthetic / simulator. */
export function TrustBadge({ trust, chronology, source }: { trust?: string; chronology?: string; source?: string }) {
  if (!trust) return null;
  const live = trust === "LIVE";
  if (live && source === "RING_API_EMULATOR") {
    return (
      <span title="Captured by the ClearWay server over the Ring API protocol (WHEP) from the local Ring API emulator. Follows the live evidence rules; not Ring footage."
        className="chip text-violet-300 border-violet-400/50 bg-ink-800">
        LIVE path · emulator{chronology && <span className="mono text-fog-400 font-normal">· {chronology.toLowerCase().replace(/_/g, " ")}</span>}
      </span>
    );
  }
  return (
    <span title={live ? "Captured by the ClearWay server from the Ring API; capture time is the server clock." : "Test or replay evidence. It can only drive demo incidents and can never verify a live Ring incident."}
      className={`chip ${live ? "text-sky-400 border-sky-400/50 bg-ink-800" : "text-fog-200 border-fog-400/40 bg-ink-800"}`}>
      {live ? "LIVE evidence" : "DEMO evidence"}{chronology && <span className="mono text-fog-400 font-normal">· {chronology.toLowerCase().replace(/_/g, " ")}</span>}
    </span>
  );
}

export function IdentitySelect({ id, value, onChange, identities, demo, label }: {
  id: string; value: string; onChange: (v: string) => void; identities: { id: string; name: string; can_approve: number; token: string }[]; demo: boolean; label: string;
}) {
  return (
    <div>
      <label className="label block mb-1.5" htmlFor={id}>{label}{demo && <span className="normal-case tracking-normal text-amber-300"> · demo identities (simulated sign-in)</span>}</label>
      <select id={id} className="input w-full" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose who is acting…</option>
        {identities.map((m) => <option key={m.id} value={m.token}>{m.name}{m.can_approve ? "" : " (not an approver)"}</option>)}
      </select>
    </div>
  );
}

/** Every camera-based conclusion is scoped to what the camera can see. */
export function ViewScope({ c, className = "" }: { c?: string; className?: string }) {
  if (c !== "NO_OBSTRUCTION_OBSERVED" && c !== "POTENTIAL_OBSTRUCTION") return null;
  return (
    <p className={`text-[12.5px] text-fog-400 leading-snug ${className}`}>
      {c === "NO_OBSTRUCTION_OBSERVED"
        ? "No obstruction observed in this camera view. This does not confirm the route is physically safe or accessible — areas outside or hidden from the camera are not assessed."
        : "Potential obstruction in this camera view (single-camera estimate). A person should confirm before relying on it."}
    </p>
  );
}

/** Source chip plus its plain-language description on its own line (avoids cramped wrapping in narrow columns). */
export function SourceLine({ s, note }: { s?: string | null; note?: string }) {
  if (!s) return null;
  return <div className="space-y-1"><SourceBadge s={s} compact title={note} />{note && <div className="text-[12px] text-fog-400 leading-snug">{note}</div>}</div>;
}
