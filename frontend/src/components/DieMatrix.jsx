import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";

const P = 10, G = 1.2; // cell pitch and gap, in viewBox units
const PATTERN_MIN = 4; // a failing cluster of at least this many dies counts as a pattern

// Connected failing clusters (8-neighbourhood), wafer bounds and helpers, computed once per map.
function analyse({ rows, cols, grid }) {
  const comp = new Int32Array(rows * cols).fill(-1);
  const sizes = [];
  let minR = rows, maxR = 0, minC = cols, maxC = 0;
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    if (grid[r][c] === "0") continue;
    minR = Math.min(minR, r); maxR = Math.max(maxR, r); minC = Math.min(minC, c); maxC = Math.max(maxC, c);
    if (grid[r][c] !== "2" || comp[r * cols + c] >= 0) continue;
    const id = sizes.length; let n = 0; const stack = [[r, c]]; comp[r * cols + c] = id;
    while (stack.length) {
      const [y, x] = stack.pop(); n++;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const yy = y + dy, xx = x + dx;
        if (yy < 0 || yy >= rows || xx < 0 || xx >= cols || comp[yy * cols + xx] >= 0 || grid[yy][xx] !== "2") continue;
        comp[yy * cols + xx] = id; stack.push([yy, xx]);
      }
    }
    sizes.push(n);
  }
  return { comp, sizes, cy: (minR + maxR) / 2, cx: (minC + maxC) / 2 };
}

// Share of failing dies within two cells of (r, c), among the dies that are on the wafer.
function failDensity({ rows, cols, grid }, r, c) {
  let wafer = 0, fail = 0;
  for (let y = Math.max(0, r - 2); y <= Math.min(rows - 1, r + 2); y++) for (let x = Math.max(0, c - 2); x <= Math.min(cols - 1, c + 2); x++) {
    if (grid[y][x] === "0") continue;
    wafer++; if (grid[y][x] === "2") fail++;
  }
  return wafer ? fail / wafer : 0;
}

// Die matrix: one square per die. Grey = pass, red = fail, empty = off the wafer.
// Hover (mouse) or tap (touch) a die to open the Die Inspector; arrow keys move it, Esc closes it.
export default function DieMatrix({ id, label }) {
  const [map, setMap] = useState(null);
  const [state, setState] = useState("idle"); // idle | loading | ready | missing
  const [sel, setSel] = useState(null);       // { r, c }
  const [pinned, setPinned] = useState(false);
  const svgRef = useRef(null);

  useEffect(() => {
    setSel(null); setPinned(false);
    if (!id) { setMap(null); setState("idle"); return undefined; }
    let live = true;
    setState("loading");
    api(`/api/v1/inspections/${id}/diemap?size=48`)
      .then((d) => { if (live) { setMap(d); setState("ready"); } })
      .catch(() => { if (live) { setMap(null); setState("missing"); } });
    return () => { live = false; };
  }, [id]);

  const info = useMemo(() => (map ? analyse(map) : null), [map]);

  if (state === "idle") return <p className="empty">The die matrix appears here after a scan. Tap a die to inspect it; failing dies are red.</p>;
  if (state === "loading") return <div className="loading-dots" aria-label="Loading die matrix"><i /><i /><i /></div>;
  if (state === "missing" || !map) return <p className="empty">A die matrix isn't available for this input (camera photos and raw sensor files have no die grid).</p>;

  const { rows, cols, grid } = map;
  const unit = map.die_rows === rows && map.die_cols === cols ? "dies" : "cells";

  const cellAt = (e) => {
    const b = svgRef.current.getBoundingClientRect();
    const scale = Math.min(b.width / (cols * P), b.height / (rows * P));
    const ox = (b.width - cols * P * scale) / 2, oy = (b.height - rows * P * scale) / 2;
    const c = Math.floor((e.clientX - b.left - ox) / scale / P), r = Math.floor((e.clientY - b.top - oy) / scale / P);
    return r >= 0 && r < rows && c >= 0 && c < cols && grid[r][c] !== "0" ? { r, c } : null;
  };
  const close = () => { setSel(null); setPinned(false); };
  const onKey = (e) => {
    if (e.key === "Escape") return close();
    const d = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[e.key];
    if (!d) return;
    e.preventDefault();
    let { r, c } = sel || { r: Math.round(info.cy), c: Math.round(info.cx) };
    if (sel) { r += d[0]; c += d[1]; }
    for (let i = 0; i < Math.max(rows, cols) && (r < 0 || r >= rows || c < 0 || c >= cols || grid[r][c] === "0"); i++) {
      if (r < 0 || r >= rows || c < 0 || c >= cols) return;  // ran off the grid
      r += d[0]; c += d[1];                                   // skip over off-wafer cells
    }
    if (r >= 0 && r < rows && c >= 0 && c < cols && grid[r][c] !== "0") { setSel({ r, c }); setPinned(true); }
  };

  const cells = [];
  const selComp = sel && grid[sel.r][sel.c] === "2" ? info.comp[sel.r * cols + sel.c] : -1;
  grid.forEach((line, r) => [...line].forEach((ch, c) => {
    if (ch === "0") return;
    const inCluster = selComp >= 0 && info.comp[r * cols + c] === selComp;
    cells.push(<rect key={`${r}-${c}`} x={c * P + G / 2} y={r * P + G / 2} width={P - G} height={P - G} rx="1.6"
      className={`${ch === "2" ? "die die-fail" : "die die-pass"}${inCluster ? " die-cluster" : ""}`} />);
  }));

  let card = null;
  if (sel) {
    const { r, c } = sel, fail = grid[r][c] === "2";
    const size = fail ? info.sizes[info.comp[r * cols + c]] : 0;
    const inPattern = fail && size >= PATTERN_MIN;
    card = (
      <aside className={`inspector ${c < cols / 2 ? "right" : "left"}`} aria-live="polite">
        <button className="inspector-close" onClick={close} aria-label="Close die inspector">×</button>
        <span className="label">Die inspector</span>
        <h4>Die {r + 1}, {c + 1}</h4>
        <dl>
          <dt>State</dt><dd className={fail ? "bad" : ""}>{fail ? "Fail" : "Pass"}</dd>
          <dt>In pattern</dt><dd className={inPattern ? "bad" : ""}>{inPattern ? `Yes · ${label || "pattern"}` : fail ? "No · isolated fail" : "No"}</dd>
          <dt>Cluster</dt><dd>{fail ? `${size} ${unit}` : "–"}</dd>
          <dt>Position</dt><dd>{(c - info.cx).toFixed(1)}, {(info.cy - r).toFixed(1)}</dd>
          <dt title="Share of failing dies within two cells of this one">Fail density</dt><dd>{failDensity(map, r, c).toFixed(2)}</dd>
        </dl>
      </aside>
    );
  }

  return (
    <figure className="diemap">
      <svg ref={svgRef} viewBox={`0 0 ${cols * P} ${rows * P}`} role="application" tabIndex={0}
        aria-label={`Die matrix, ${map.fail} failing of ${map.pass + map.fail} cells. Use arrow keys to inspect dies.`}
        onPointerMove={(e) => { if (e.pointerType === "mouse" && !pinned) setSel(cellAt(e)); }}
        onPointerLeave={(e) => { if (e.pointerType === "mouse" && !pinned) setSel(null); }}
        onPointerDown={(e) => { const hit = cellAt(e); setSel(hit); setPinned(!!hit); }}
        onKeyDown={onKey}>
        {cells}
        {sel && <rect className="die-sel" x={sel.c * P - 0.4} y={sel.r * P - 0.4} width={P + 0.8} height={P + 0.8} rx="2" />}
      </svg>
      {card}
      <figcaption>
        <span className="legend"><i className="die-pass" />Pass <b>{map.pass}</b></span>
        <span className="legend"><i className="die-fail" />Fail <b>{map.fail}</b></span>
        <span className="legend muted">{cols} × {rows} grid</span>
      </figcaption>
    </figure>
  );
}
