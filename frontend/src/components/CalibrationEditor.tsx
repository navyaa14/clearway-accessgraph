import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Circle, Image as KImage, Layer, Line, Stage, Text } from "react-konva";
import { Eraser, Save, Upload } from "lucide-react";
import { api, hhPath } from "../api";
import { ErrorNote, Spinner } from "./ui";

type Tool = "route" | "drop_zone" | "floor";
const COLORS: Record<Tool, string> = { route: "#2DD4BF", drop_zone: "#60A5FA", floor: "#F5A524" };

function useImage(src?: string | null) {
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  useEffect(() => {
    if (!src) return setImg(null);
    const i = new window.Image();
    i.onload = () => setImg(i);
    i.src = src;
  }, [src]);
  return img;
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(800);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((e) => setW(Math.floor(e[0].contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export function CalibrationEditor({ camera }: { camera: any }) {
  const qc = useQueryClient();
  const cal = useQuery({ queryKey: ["calibration", camera.id], queryFn: () => api(hhPath(`/cameras/${camera.id}/calibration`)) });
  const [tool, setTool] = useState<Tool>("route");
  const [route, setRoute] = useState<number[][]>([]);
  const [zone, setZone] = useState<number[][]>([]);
  const [floor, setFloor] = useState<number[][]>([]);
  const [world, setWorld] = useState<number[][]>([]);
  const [clearance, setClearance] = useState<string>("");
  const [ref, width] = useWidth();
  const [stamp, setStamp] = useState(Date.now());
  const img = useImage(cal.data?.reference_url ? `${cal.data.reference_url}&v=${stamp}` : null);

  useEffect(() => {
    const p = cal.data?.profile;
    if (!p) return;
    setRoute(p.route || []);
    setZone(p.drop_zone || []);
    setFloor(p.floor_points_img || []);
    setWorld(p.floor_points_world || []);
    setClearance(p.clearance_cm ? String(p.clearance_cm) : "");
  }, [cal.data?.profile?.version]);

  const size = cal.data?.reference_size || [960, 720];
  const W = width, H = Math.round((width * size[1]) / size[0]);
  const setters: Record<Tool, [number[][], (v: number[][]) => void]> = { route: [route, setRoute], drop_zone: [zone, setZone], floor: [floor, setFloor] };

  const save = useMutation({
    mutationFn: () => api(hhPath(`/cameras/${camera.id}/calibration`), {
      method: "PUT",
      json: {
        route, drop_zone: zone.length >= 3 ? zone : null,
        floor_points_img: floor.length >= 4 ? floor : null,
        floor_points_world: floor.length >= 4 ? world.slice(0, floor.length) : null,
        clearance_cm: floor.length >= 4 && clearance ? Number(clearance) : null,
      },
    }),
    onSuccess: () => { qc.invalidateQueries(); },
  });
  const upload = useMutation({
    mutationFn: async (f: File) => {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("source", "INDEPENDENT_TEST_VIDEO");
      return api(hhPath(`/cameras/${camera.id}/reference`), { method: "POST", body: fd });
    },
    onSuccess: () => { setStamp(Date.now()); qc.invalidateQueries(); },
  });
  const ringRef = useMutation({
    mutationFn: () => api(hhPath(`/ring/${camera.id}/reference`), { method: "POST" }),
    onSuccess: () => { setStamp(Date.now()); qc.invalidateQueries(); },
  });

  const metricReady = floor.length >= 4 && world.length >= floor.length && world.slice(0, floor.length).every((p) => p.length === 2 && p.every((v) => Number.isFinite(v)));
  const issues = useMemo(() => {
    const out: string[] = [];
    if (route.length < 3) out.push("Mark at least 3 points for the essential route.");
    if (floor.length > 0 && floor.length < 4) out.push("Floor calibration needs 4 points (or none for apparent-overlap mode).");
    if (floor.length >= 4 && !metricReady) out.push("Enter real-world X/Y (cm) for every floor point.");
    return out;
  }, [route, floor, world, metricReady]);

  if (cal.isLoading) return <Spinner />;
  if (!cal.data?.reference_url) {
    return (
      <div className="space-y-4">
        <p className="text-fog-300">This camera needs a <strong>clear-scene reference frame</strong> (no parcels on the route) before a route can be marked.</p>
        <div className="flex flex-wrap gap-3">
          {camera.kind === "ring" && <button className="btn-primary" onClick={() => ringRef.mutate()} disabled={ringRef.isPending}>Capture reference from Ring</button>}
          <label className="btn-ghost cursor-pointer"><Upload size={16} /> Upload reference image/video<input type="file" accept="image/*,video/*" className="sr-only" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} /></label>
        </div>
        <ErrorNote error={ringRef.error || upload.error} />
      </div>
    );
  }

  const toNorm = (x: number, y: number) => [Math.min(1, Math.max(0, x / W)), Math.min(1, Math.max(0, y / H))];
  const onStageClick = (e: any) => {
    if (e.target?.attrs?.name === "vertex") return;
    const p = e.target.getStage().getPointerPosition();
    const [list, set] = setters[tool];
    if (tool === "floor" && list.length >= 6) return;
    set([...list, toNorm(p.x, p.y)]);
    if (tool === "floor") setWorld((w) => [...w.slice(0, floor.length), [NaN, NaN]]);
  };

  return (
    <div className="grid xl:grid-cols-[1fr_340px] gap-5">
      <div>
        <div className="flex flex-wrap items-center gap-2 mb-3" role="radiogroup" aria-label="Editing layer">
          {(["route", "drop_zone", "floor"] as Tool[]).map((t) => (
            <button key={t} role="radio" aria-checked={tool === t} onClick={() => setTool(t)}
              className={`chip !px-3 !py-1.5 ${tool === t ? "bg-ink-700 text-fog-100 border-fog-300/50" : "border-ink-600 text-fog-300"}`}>
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: COLORS[t] }} />
              {t === "route" ? "Essential route" : t === "drop_zone" ? "Delivery zone" : "Floor reference points"}
            </button>
          ))}
          <button className="btn-ghost !min-h-0 !py-1.5 ml-auto text-[13px]" onClick={() => { setters[tool][1]([]); if (tool === "floor") setWorld([]); }}><Eraser size={14} /> Clear layer</button>
        </div>
        <div ref={ref} className="rounded-xl overflow-hidden border border-ink-700 bg-black cursor-crosshair" aria-label="Calibration canvas: click to add a point to the selected layer, drag points to adjust, double-click a point to remove it">
          <Stage width={W} height={H} onClick={onStageClick} onTap={onStageClick}>
            <Layer>
              {img && <KImage image={img} width={W} height={H} />}
              {(["drop_zone", "route"] as Tool[]).map((t) => {
                const [list] = setters[t];
                return list.length > 1 && <Line key={t} points={list.flatMap(([x, y]) => [x * W, y * H])} closed={list.length > 2} stroke={COLORS[t]} strokeWidth={2.5} fill={COLORS[t] + (t === "route" ? "33" : "1a")} dash={t === "drop_zone" ? [10, 7] : undefined} listening={false} />;
              })}
              {(["route", "drop_zone", "floor"] as Tool[]).map((t) => setters[t][0].map(([x, y], i) => (
                <Circle key={`${t}${i}`} name="vertex" x={x * W} y={y * H} radius={tool === t ? 7 : 4.5} fill={COLORS[t]} stroke="#0B1220" strokeWidth={2}
                  draggable={tool === t} opacity={tool === t ? 1 : 0.55}
                  onDragMove={(e) => { const [list, set] = setters[t]; const n = list.slice(); n[i] = toNorm(e.target.x(), e.target.y()); set(n); }}
                  onDblClick={() => { const [list, set] = setters[t]; set(list.filter((_, j) => j !== i)); if (t === "floor") setWorld((w) => w.filter((_, j) => j !== i)); }} />
              )))}
              {floor.map(([x, y], i) => <Text key={`ft${i}`} x={x * W + 9} y={y * H - 18} text={`F${i + 1}`} fill="#F5A524" fontStyle="bold" fontSize={13} listening={false} />)}
            </Layer>
          </Stage>
        </div>
        <p className="text-[12.5px] text-fog-400 mt-2">Click to add points · drag to adjust · double-click a point to delete. Polygons are stored in normalised image coordinates, versioned per camera.</p>
      </div>

      <div className="space-y-5">
        <div>
          <div className="label mb-2">Floor reference (optional, enables metric mode)</div>
          {floor.length === 0 ? (
            <p className="text-[13px] text-fog-400">No floor points: <strong className="text-amber-300">apparent-overlap mode</strong>. Image-space overlap only; physical clear width cannot be established.</p>
          ) : (
            <table className="w-full text-[13px]">
              <thead><tr className="text-fog-400"><th className="text-left font-normal">Pt</th><th className="text-left font-normal">X (cm)</th><th className="text-left font-normal">Y (cm)</th></tr></thead>
              <tbody>
                {floor.map((_, i) => (
                  <tr key={i}>
                    <td className="text-amber-300 mono pr-2">F{i + 1}</td>
                    {[0, 1].map((k) => (
                      <td key={k} className="pr-2 py-1">
                        <input className="input w-full !min-h-[34px] !py-1" inputMode="decimal" aria-label={`F${i + 1} ${k ? "Y" : "X"} centimetres`}
                          value={Number.isFinite(world[i]?.[k]) ? world[i][k] : ""}
                          onChange={(e) => setWorld((w) => { const n = w.map((r) => r.slice()); while (n.length <= i) n.push([NaN, NaN]); n[i][k] = e.target.value === "" ? NaN : Number(e.target.value); return n; })} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-[12px] text-fog-400 mt-2">Click 4 floor landmarks (tile corners, path joints) and enter their measured positions on the floor plane.</p>
        </div>
        <div>
          <label className="label" htmlFor="clr">Minimum clear width (cm)</label>
          <input id="clr" className="input w-full mt-2" inputMode="decimal" placeholder={metricReady ? "e.g. 91 for a walker" : "requires floor calibration"} disabled={!metricReady} value={clearance} onChange={(e) => setClearance(e.target.value)} />
        </div>
        {cal.data?.homography && <div className="text-[13px] text-fog-300">Reprojection consistency: <span className="mono">{cal.data.homography.reprojection_error_cm} cm</span><div className="text-[12px] text-fog-400 mt-0.5">Measures agreement with the points you entered — not real-world accuracy. A small value does not prove centimetre-level clearance.</div></div>}
        {issues.length > 0 && <ul className="text-[13px] text-amber-300 list-disc pl-5 space-y-1">{issues.map((i) => <li key={i}>{i}</li>)}</ul>}
        <button className="btn-primary w-full" disabled={issues.length > 0 || save.isPending} onClick={() => save.mutate()}><Save size={16} /> Save new calibration version</button>
        {save.data && <div className="text-[13px] text-teal-300">Saved {save.data.version}</div>}
        <ErrorNote error={save.error} />
        <div className="pt-4 border-t border-ink-700/60 space-y-2">
          <div className="label">Clear-scene reference</div>
          <div className="text-[13px] text-fog-300">Source: <span className="mono">{cal.data.reference_source}</span></div>
          <div className="flex flex-wrap gap-2">
            {camera.kind === "ring" && <button className="btn-ghost !min-h-0 !py-1.5 text-[13px]" onClick={() => ringRef.mutate()} disabled={ringRef.isPending}>Recapture from Ring</button>}
            {camera.kind !== "synthetic" && <label className="btn-ghost !min-h-0 !py-1.5 text-[13px] cursor-pointer"><Upload size={14} /> Replace<input type="file" accept="image/*,video/*" className="sr-only" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} /></label>}
          </div>
          <ErrorNote error={ringRef.error || upload.error} />
          <p className="text-[12px] text-fog-400">Saving a new reference creates a new calibration version. If the camera later moves, ClearWay detects it and reports “unable to verify” until recalibrated.</p>
        </div>
      </div>
    </div>
  );
}
