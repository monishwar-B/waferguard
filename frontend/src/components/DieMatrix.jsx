import { useEffect, useState } from "react";
import { api } from "../api.js";

// Die matrix: one square per die. Grey = pass, red = fail, empty = off the wafer.
export default function DieMatrix({ id }) {
  const [map, setMap] = useState(null);
  const [state, setState] = useState("idle"); // idle | loading | ready | missing

  useEffect(() => {
    if (!id) { setMap(null); setState("idle"); return undefined; }
    let live = true;
    setState("loading");
    api(`/api/v1/inspections/${id}/diemap?size=48`)
      .then((d) => { if (live) { setMap(d); setState("ready"); } })
      .catch(() => { if (live) { setMap(null); setState("missing"); } });
    return () => { live = false; };
  }, [id]);

  if (state === "idle") return <p className="empty">The die matrix appears here after a scan: one square per die, failing dies in red.</p>;
  if (state === "loading") return <div className="loading-dots" aria-label="Loading die matrix"><i /><i /><i /></div>;
  if (state === "missing" || !map) return <p className="empty">A die matrix isn't available for this input (camera photos and raw sensor files have no die grid).</p>;

  const { rows, cols, grid } = map;
  const P = 10, G = 1.2; // pitch and gap in viewBox units
  const cells = [];
  grid.forEach((line, r) => [...line].forEach((ch, c) => {
    if (ch === "0") return;
    cells.push(<rect key={`${r}-${c}`} x={c * P + G / 2} y={r * P + G / 2} width={P - G} height={P - G} rx="1.6"
      className={ch === "2" ? "die die-fail" : "die die-pass"}><title>{`Row ${r + 1}, column ${c + 1}: ${ch === "2" ? "fail" : "pass"}`}</title></rect>);
  }));
  return (
    <figure className="diemap">
      <svg viewBox={`0 0 ${cols * P} ${rows * P}`} role="img" aria-label={`Die matrix, ${map.fail} failing cells of ${map.pass + map.fail}`}>{cells}</svg>
      <figcaption>
        <span className="legend"><i className="die-pass" />Pass <b>{map.pass}</b></span>
        <span className="legend"><i className="die-fail" />Fail <b>{map.fail}</b></span>
        <span className="legend muted">{cols} × {rows} grid</span>
      </figcaption>
    </figure>
  );
}
