import { useEffect, useRef, useState } from "react";

const api = (path, opts) => fetch(`/api${path}`, opts).then((r) => r.json());

const pct = (x) => `${Math.round(x * 100)}%`;
const ACTIONS = { auto_pr_allowed: "Auto-PR allowed", rca_only: "RCA only — review before fixing", needs_human: "Needs a human" };

function Confidence({ b }) {
  return (
    <div className="conf">
      <div className="bar"><div style={{ width: pct(b.final) }} /></div>
      <strong>{pct(b.final)}</strong> <span className="muted">· {ACTIONS[b.action]}</span>
      <ul className="muted small">
        {Object.entries(b.agents).map(([a, v]) => <li key={a}>{a}: {pct(v)} (track record {pct(b.reliability[a])})</li>)}
        <li>Evidence factor ×{b.evidence_factor}</li>
        <li>Memory: {b.similar_incidents} similar past incident(s), adjustment {b.memory_delta >= 0 ? "+" : ""}{pct(b.memory_delta)}</li>
      </ul>
    </div>
  );
}

function Feedback({ run }) {
  const [sent, setSent] = useState(null);
  const send = async (accepted) => {
    const r = await fetch(`/api/runs/${run}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ accepted }) });
    if (r.ok) setSent(accepted ? "accepted" : "rejected");
  };
  if (sent) return <p className="muted">Marked {sent}. BugSmith will remember this.</p>;
  return <div className="row"><button onClick={() => send(true)}>👍 Correct</button><button className="alt" onClick={() => send(false)}>👎 Wrong</button></div>;
}

function Rca({ rca, run }) {
  return (
    <section className="card">
      <h3>Root cause</h3>
      {rca.confidence_breakdown && <Confidence b={rca.confidence_breakdown} />}
      <p><strong>{rca.summary}</strong></p>
      <p className="muted">{rca.faulty_file}:{rca.faulty_lines}</p>
      <p>{rca.hypothesis}</p>
      <h4>Evidence</h4>
      <ul>{rca.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
      {rca.missing_info?.length > 0 && (<><h4>Missing info</h4><ul>{rca.missing_info.map((e, i) => <li key={i}>{e}</li>)}</ul></>)}
      <h4>Suggested fix</h4>
      <p>{rca.suggested_fix}</p>
      <Feedback run={run} />
    </section>
  );
}

function RunView({ run }) {
  const [steps, setSteps] = useState([]);
  const [result, setResult] = useState(null);

  useEffect(() => {
    setSteps([]);
    setResult(null);
    const es = new EventSource(`/api/runs/${run}/stream`);
    es.addEventListener("step", (e) => setSteps((s) => [...s, JSON.parse(e.data)]));
    es.addEventListener("done", (e) => { setResult(JSON.parse(e.data)); es.close(); });
    es.onerror = () => es.close();
    return () => es.close();
  }, [run]);

  return (
    <div>
      <section className="card">
        <h3>Timeline</h3>
        <ol className="timeline">
          {steps.map((s, i) => (
            <li key={i}>
              <span className="agent">{s.agent}</span>{" "}
              {s.type === "tool_call" && <>called <code>{s.name}</code> <span className="muted">{JSON.stringify(s.args)}</span></>}
              {s.type === "tool_result" && <>got result from <code>{s.name}</code></>}
              {s.type === "confidence" && <>final confidence <strong>{pct(s.final)}</strong></>}
              {s.type === "message" && <span className="muted">{s.text.slice(0, 200)}</span>}
            </li>
          ))}
          {!result && <li className="muted">working…</li>}
        </ol>
      </section>
      {result?.error && <section className="card error">Run failed: {result.error}</section>}
      {result?.rca && <Rca rca={result.rca} run={run} />}
    </div>
  );
}

export default function App() {
  const [incidents, setIncidents] = useState([]);
  const [selected, setSelected] = useState(null);
  const [run, setRun] = useState(null);
  const [form, setForm] = useState({ issue_id: "DEMO-1", title: "TypeError: NoneType in cart_total", service: "demo-shop" });
  const timer = useRef();

  const refresh = () => api("/incidents").then(setIncidents);
  useEffect(() => { refresh(); timer.current = setInterval(refresh, 4000); return () => clearInterval(timer.current); }, []);

  const create = async (e) => {
    e.preventDefault();
    const inc = await api("/incidents", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
    await refresh();
    setSelected(inc.id);
  };
  const investigate = async (id) => {
    const r = await api(`/incidents/${id}/runs`, { method: "POST" });
    setSelected(id);
    setRun(r.id);
    refresh();
  };

  return (
    <main>
      <h1>BugSmith</h1>
      <p className="muted">Production crash → root cause → validated PR. (Milestone 2: crash → RCA)</p>
      <form className="card row" onSubmit={create}>
        <input value={form.issue_id} onChange={(e) => setForm({ ...form, issue_id: e.target.value })} placeholder="Issue id" />
        <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Title" />
        <button>Add incident</button>
      </form>
      <div className="layout">
        <section className="card">
          <h3>Incidents</h3>
          {incidents.length === 0 && <p className="muted">None yet.</p>}
          {incidents.map((i) => (
            <div key={i.id} className={`incident ${selected === i.id ? "sel" : ""}`}>
              <div><strong>{i.issue_id}</strong> <span className={`badge ${i.status}`}>{i.status}</span></div>
              <div className="muted">{i.title}</div>
              <button onClick={() => investigate(i.id)}>Investigate</button>
            </div>
          ))}
        </section>
        {run ? <RunView run={run} /> : <section className="card muted">Pick an incident and click Investigate.</section>}
      </div>
    </main>
  );
}
