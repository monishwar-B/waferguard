import { pct } from "../api.js";

// Friendly description of an ensemble member from its manifest entry.
function describe(m) {
  if (m.type === "sklearn") return { title: "Geometry features + gradient boosting", sub: "32 rotation-robust wafer-map measurements" };
  const w = m.params?.width;
  if (m.arch === "wafernet") return { title: `Residual CNN${w ? `, ${w} channels` : ""}`, sub: "Trained from scratch on wafer maps" };
  return { title: m.arch || m.name, sub: "Transfer-learned network" };
}

// Test accuracy of each model in the ensemble and of the ensemble itself.
// Bars share one axis; the axis starts above zero so the differences are visible, and says so.
export default function ModelScores({ model, compact = false }) {
  if (!model) return null;
  const members = model.members || [];
  const ens = model.test_metrics?.accuracy;
  const vals = members.flatMap((m) => [m.test_accuracy, m.test_accuracy_tta]).concat(ens).filter((v) => typeof v === "number");
  if (!vals.length) {
    return (
      <div className="panel">
        <h2>Model accuracy</h2>
        <p className="muted small">This model folder has no recorded test results. Retrain it or run scripts/evaluate.py to measure it.</p>
      </div>
    );
  }
  const lo = Math.max(0, Math.floor((Math.min(...vals) - 0.02) * 20) / 20);
  const hi = 1;
  const ticks = Array.from({ length: Math.round((hi - lo) / 0.05) + 1 }, (_, i) => lo + i * 0.05);
  const at = (v) => `${((v - lo) / (hi - lo)) * 100}%`;

  const Row = ({ title, sub, value, tta, cls }) => (
    <div className={`score ${cls || ""}`}>
      <div className="score-name">{title}<span>{sub}</span></div>
      <div className="score-track" role="img" aria-label={`${title}: ${pct(value)} test accuracy`}>
        {typeof value === "number" && <div className="score-fill" style={{ width: at(value) }} />}
        {typeof tta === "number" && <div className="score-tta" style={{ left: at(tta) }} title={`With test-time augmentation: ${pct(tta)}`} />}
      </div>
      <div className="score-val">{pct(value)}</div>
    </div>
  );

  return (
    <div className="panel">
      <div className="models-head">
        <h2 style={{ margin: 0 }}>Model accuracy</h2>
        <span className="small muted num">model {model.version}</span>
      </div>
      <div className="scores">
        {members.map((m) => {
          const d = describe(m);
          return <Row key={m.name} title={d.title} value={m.test_accuracy} tta={m.test_accuracy_tta}
            sub={`${d.sub}; ${Math.round(m.weight * 100)}% of the vote`} />;
        })}
        <Row cls="ensemble" title="Combined ensemble" value={ens}
          sub={`Weighted vote of all three${model.tta ? ", 8-view test-time augmentation" : ""}; this is what inspects your wafers`} />
        <div className="score-axis"><span /><div>{ticks.map((t) => <span key={t}>{pct(t, 0)}</span>)}</div><span /></div>
      </div>
      {!compact && (
        <p className="small muted" style={{ marginTop: 14, marginBottom: 0 }}>
          Measured on {model.test_wafers ? model.test_wafers.toLocaleString() : "held-out"} test wafers that were never used for
          training or tuning. The white tick shows a network's score with test-time augmentation. Macro-F1 of the ensemble: {pct(model.test_metrics?.macro_f1)}.
        </p>
      )}
    </div>
  );
}
