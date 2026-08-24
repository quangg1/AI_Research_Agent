import { useEffect, useState } from "react";
import { endpoints } from "../lib/api";
import { loadPinnedClaims, unpinClaim, type PinnedClaim } from "../lib/pinnedClaims";

export function WorkspacePage({ go }: { go: (to: string) => void }) {
  const [runs, setRuns] = useState<any[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [pinnedClaims, setPinnedClaims] = useState<PinnedClaim[]>([]);

  async function load(reset = true) {
    setLoading(true);
    try {
      const data = await endpoints.workspace({
        q: q || undefined,
        status: status || undefined,
        cursor: reset ? undefined : cursor || undefined,
        limit: "30",
      });
      setRuns(reset ? data.runs || [] : [...runs, ...(data.runs || [])]);
      setNextCursor(data.next_cursor || null);
      setPinnedClaims(loadPinnedClaims());
      setError("");
    } catch (err: any) {
      setError(err.message || "Workspace unavailable");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(true);
  }, []);

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
    await load(true);
  }

  return (
    <>
      <section className="hero">
        <div className="hero-kicker">Decision workspace</div>
        <h1>Every run leaves a trail.</h1>
        <p>Search, filter, pin, archive, and reopen org-scoped research history.</p>
      </section>
      <form
        className="panel scenario-form"
        onSubmit={(e) => {
          e.preventDefault();
          setCursor(null);
          load(true);
        }}
      >
        <div className="form-grid">
          <label>
            Search
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Query or title"
              aria-label="Search runs"
            />
          </label>
          <label>
            Status
            <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
              <option value="">All</option>
              <option value="queued">queued</option>
              <option value="running">running</option>
              <option value="awaiting_human">awaiting_human</option>
              <option value="completed">completed</option>
              <option value="failed">failed</option>
              <option value="cancelled">cancelled</option>
            </select>
          </label>
        </div>
        <div className="btn-row">
          <button className="btn primary" type="submit">
            Apply filters
          </button>
        </div>
      </form>
      {error && <p className="err">{error}</p>}
      {loading && runs.length === 0 && <p className="idle" aria-live="polite">Loading runs…</p>}
      <div className="workspace">
        <aside className="panel">
          <h3>Runs</h3>
          {!loading && runs.length === 0 && (
            <p className="idle">No runs yet. Start a research question.</p>
          )}
          {runs.map((r) => (
            <button
              key={r.id}
              className={`step ${selected === r.id ? "active" : ""}`}
              type="button"
              onClick={() => openTimeline(r.id)}
            >
              <span
                className={`ico ${
                  r.status === "completed" ? "done" : r.status === "failed" ? "warn" : "now"
                }`}
              >
                ●
              </span>
              <span>
                <div className="label">{(r.title || r.query || "").slice(0, 72) || r.id}</div>
                <div className="sub">
                  {r.status} · {r.created_at ? new Date(r.created_at).toLocaleString() : ""}
                  {r.pinned ? " · pinned" : ""}
                </div>
              </span>
            </button>
          ))}
          {nextCursor && (
            <button
              className="btn"
              type="button"
              onClick={() => {
                setCursor(nextCursor);
                setTimeout(() => load(false), 0);
              }}
            >
              Load more
            </button>
          )}
        </aside>
        <main>
          {selected ? (
            <section className="panel">
              <div className="summary-head">
                <h2>Timeline</h2>
                <div className="btn-row">
                  <button className="btn" type="button" onClick={() => pin(selected, true)}>
                    Pin
                  </button>
                  <button className="btn" type="button" onClick={() => pin(selected, false)}>
                    Unpin
                  </button>
                  <button
                    className="btn"
                    type="button"
                    onClick={async () => {
                      const title = window.prompt("Rename run");
                      if (title) {
                        await endpoints.patchRun(selected, { title });
                        await load(true);
                      }
                    }}
                  >
                    Rename
                  </button>
                  <button
                    className="btn"
                    type="button"
                    onClick={async () => {
                      await endpoints.patchRun(selected, { archived: true });
                      await load(true);
                      setSelected(null);
                    }}
                  >
                    Archive
                  </button>
                  <button
                    className="btn"
                    type="button"
                    onClick={async () => {
                      if (!window.confirm("Delete this run?")) return;
                      await endpoints.deleteRun(selected);
                      await load(true);
                      setSelected(null);
                    }}
                  >
                    Delete
                  </button>
                  <button className="btn primary" type="button" onClick={() => go(`/?run=${selected}`)}>
                    Open run
                  </button>
                </div>
              </div>
              <ol className="timeline">
                {events.length === 0 && <p className="idle">No persisted events yet for this run.</p>}
                {events.map((ev) => (
                  <li key={ev.id}>
                    <span className="tag">{ev.event_type}</span>
                    <time>{new Date(ev.created_at).toLocaleTimeString()}</time>
                    <div>{ev.summary || ""}</div>
                  </li>
                ))}
              </ol>
            </section>
          ) : (
            <p className="idle">Select a run to inspect its event trail.</p>
          )}
          <section className="panel" style={{ marginTop: 16 }}>
            <h2 style={{ marginTop: 0, fontSize: 16 }}>Pinned claims</h2>
            <p className="sub">Saved from evidence drawer on Research. Reuse in later questions.</p>
            <ul className="pinned-claim-list">
              {pinnedClaims.map((p) => (
                <li key={p.id}>
                  <p>{p.text}</p>
                  <div className="btn-row">
                    {p.runId ? (
                      <button className="btn compact" type="button" onClick={() => go(`/?run=${p.runId}`)}>
                        Open run
                      </button>
                    ) : null}
                    <button
                      className="btn compact"
                      type="button"
                      onClick={() => setPinnedClaims(unpinClaim(p.id))}
                    >
                      Unpin
                    </button>
                  </div>
                </li>
              ))}
              {!pinnedClaims.length && <li className="sub">No pinned claims yet.</li>}
            </ul>
          </section>
        </main>
      </div>
    </>
  );
}
