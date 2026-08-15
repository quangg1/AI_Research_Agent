import { useEffect, useState } from "react";
import { endpoints } from "../lib/api";

const TIER_LABEL: Record<string, string> = {
  official_regulation: "Primary docs",
  intergovernmental: "Eval lab",
  standard_body: "Framework",
  peer_reviewed: "Peer reviewed",
  specialist_research: "Specialist",
};

export function CorpusPage() {
  const [data, setData] = useState<any>({ documents: 0, items: [], hosts: {}, tiers: {} });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    try {
      setData(await endpoints.corpus());
    } catch (err: any) {
      setError(err.message || "Corpus unavailable");
    }
  }

  useEffect(() => { load(); }, []);

  async function refresh() {
    setBusy(true);
    setError("");
    try {
      await endpoints.refreshCorpus();
      await load();
    } catch (err: any) {
      setError(err.message || "Refresh failed");
    } finally {
      setBusy(false);
    }
  }

  const items = data.items || [];

  return (
    <>
      <section className="hero">
        <div className="hero-kicker">Source watch</div>
        <h1>Curated LLM-systems corpus.</h1>
        <p>Primary docs, eval labs, and papers the docs agent retrieves before the open web.</p>
      </section>
      <div className="stat-row">
        <div className="stat"><strong>{data.documents || items.length}</strong><span>chunks</span></div>
        <div className="stat"><strong>{Object.keys(data.hosts || {}).length}</strong><span>hosts</span></div>
        <div className="stat"><strong>{Object.keys(data.tiers || {}).length}</strong><span>tiers</span></div>
        <button className="btn primary" type="button" onClick={refresh} disabled={busy}>
          {busy ? "Refreshing…" : "Refresh index"}
        </button>
      </div>
      {error && <p className="err">{error}</p>}
      <div className="card-grid">
        {items.map((d: any) => (
          <article className="glow-card" key={d.id}>
            <div className="source-top">
              <span className="tag">{TIER_LABEL[d.tier] || d.tier}</span>
              <span className="conf">{Number(d.credibility || 0).toFixed(2)}</span>
            </div>
            <h3>{d.title}</h3>
            <p>{d.snippet}</p>
            {d.url ? <a href={d.url} target="_blank" rel="noreferrer">{d.url.replace(/^https?:\/\//, "").slice(0, 48)}</a> : null}
          </article>
        ))}
      </div>
    </>
  );
}
