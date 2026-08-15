import { useEffect, useState } from "react";
import { endpoints } from "../lib/api";

export function WorkspacePage({ go }: { go: (to: string) => void }) {
  const [runs, setRuns] = useState<any[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [error, setError] = useState("");

  async function load() {
    try {
      const data = await endpoints.workspace();
      setRuns(data.runs || []);
    } catch (err: any) {
      setError(err.message || "Workspace unavailable");
    }
  }

  useEffect(() => { load(); }, []);

  async function openTimeline(id: string) {
    setSelected(id);
    try {
      const data = await endpoints.timeline(id);
      setEvents(data.events || []);
    } catch {
      setEvents([]);
    }
  }

  async function pin(id: string, pinned: boolean) {
    await endpoints.pin(id, pinned);
    await load();
  }

  return (
    <>
      <section className="hero">
        <div className="hero-kicker">Decision workspace</div>
        <h1>Every run leaves a trail.</h1>
        <p>History, pins, and the event timeline persisted in Postgres — not just a share URL.</p>
      </section>
      {error && <p className="err">{error}</p>}
      <div className="workspace">
        <aside className="panel">
          <h3>Runs</h3>
          {runs.length === 0 && <p className="idle">No runs yet. Start a research question.</p>}
          {runs.map((r) => (
            <button key={r.id} className={`step ${selected === r.id ? "active" : ""}`} type="button" onClick={() => openTimeline(r.id)}>
              <span className={`ico ${r.status === "completed" ? "done" : r.status === "failed" ? "warn" : "now"}`}>●</span>
              <span>
                <div className="label">{(r.query || "").slice(0, 72) || r.id}</div>
                <div className="sub">{r.status} · {new Date(r.created_at).toLocaleString()}</div>
              </span>
            </button>
          ))}
        </aside>
        <main>
          {selected ? (
            <section className="panel">
              <div className="summary-head">
                <h2>Timeline</h2>
                <div className="btn-row">
                  <button className="btn" type="button" onClick={() => pin(selected, true)}>Pin</button>
                  <button className="btn primary" type="button" onClick={() => go(`/?run=${selected}`)}>Open run</button>
                </div>
              </div>
              <ol className="timeline">
                {events.length === 0 && <p className="idle">No persisted events yet for this run.</p>}
                {events.map((ev) => (
                  <li key={ev.id}>
                    <span className="tag">{ev.event_type}</span>
                    <time>{new Date(ev.created_at).toLocaleTimeString()}</time>
                    <pre>{JSON.stringify(ev.payload, null, 0).slice(0, 280)}</pre>
                  </li>
                ))}
              </ol>
            </section>
          ) : (
            <p className="idle">Select a run to inspect its event trail.</p>
          )}
        </main>
      </div>
    </>
  );
}
