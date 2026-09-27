// "How it happens and what to do" for one inspection result.
export default function Guidance({ g, severity }) {
  if (!g) return null;
  if (g.label === "none") {
    return (
      <div className="panel guide">
        <h2>No defect pattern</h2>
        <p className="muted" style={{ margin: 0 }}>{g.summary} No action needed; the wafer continues as normal.</p>
      </div>
    );
  }
  const steps = [
    { key: "immediate", title: "Do now", items: g.actions.immediate, now: true },
    { key: "investigate", title: "Find the cause", items: g.actions.investigate },
    { key: "prevent", title: "Stop it coming back", items: g.actions.prevent },
  ].filter((s) => s.items?.length);
  return (
    <div className="panel guide">
      <div className="guide-grid">
        <section>
          <h2>How this defect forms</h2>
          <p className="guide-summary">{g.summary}</p>
          <p className="guide-mech">{g.mechanism}</p>
          {g.recurrence_notes?.map((n) => <div key={n} className="guide-recur">{n}</div>)}
          {g.causes?.length > 0 && (
            <>
              <h3 className="guide-sub">Likely causes, most common first</h3>
              <ul className="causes">
                {g.causes.map((c) => (
                  <li key={c.cause}>
                    <div className="cause-head"><b>{c.cause}</b><span className="area">{c.area}</span></div>
                    <div className="cause-check"><span>Check:</span> {c.check}</div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
        <section>
          <h2>What to do</h2>
          <ol className="plan">
            {steps.map((s) => (
              <li key={s.key} className={s.now ? `now sev-edge-${severity}` : ""}>
                <div className="plan-title">{s.title}</div>
                <ul>{s.items.map((t) => <li key={t}>{t}</li>)}</ul>
              </li>
            ))}
          </ol>
          <p className="small muted guide-note">{g.disclaimer}</p>
        </section>
      </div>
    </div>
  );
}
