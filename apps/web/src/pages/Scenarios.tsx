import { FormEvent, useState } from "react";
import { endpoints } from "../lib/api";

export function ScenariosPage({ go }: { go: (to: string, query?: string) => void }) {
  const [tab, setTab] = useState<"serving" | "rag">("serving");
  const [serving, setServing] = useState({ input_tokens_per_day: 8_000_000, output_tokens_per_day: 2_000_000, days: 30 });
  const [rag, setRag] = useState({ queries_per_month: 50_000, corpus_tokens: 2_000_000, refresh_jobs_per_month: 4, long_context_tokens: 32_000 });
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = tab === "serving" ? await endpoints.serving(serving) : await endpoints.rag(rag);
      setResult(data);
    } catch (err: any) {
      setError(err.message || "Scenario failed");
    } finally {
      setBusy(false);
    }
  }

  const rows = result?.rows || [];

  return (
    <>
      <section className="hero">
        <div className="hero-kicker">Deterministic engine</div>
        <h1>Price the architecture before you prompt it.</h1>
        <p>Token and GPU math first. Then hand the same question to the research agent.</p>
      </section>
      <div className="filters">
        <button className={`filter ${tab === "serving" ? "on" : ""}`} type="button" onClick={() => { setTab("serving"); setResult(null); }}>Serving cost</button>
        <button className={`filter ${tab === "rag" ? "on" : ""}`} type="button" onClick={() => { setTab("rag"); setResult(null); }}>RAG vs fine-tune</button>
      </div>
      <form className="panel scenario-form" onSubmit={onSubmit}>
        {tab === "serving" ? (
          <div className="form-grid">
            <label>Input tokens / day<input type="number" value={serving.input_tokens_per_day} onChange={(e) => setServing({ ...serving, input_tokens_per_day: Number(e.target.value) })} /></label>
            <label>Output tokens / day<input type="number" value={serving.output_tokens_per_day} onChange={(e) => setServing({ ...serving, output_tokens_per_day: Number(e.target.value) })} /></label>
            <label>Days<input type="number" value={serving.days} onChange={(e) => setServing({ ...serving, days: Number(e.target.value) })} /></label>
          </div>
        ) : (
          <div className="form-grid">
            <label>Queries / month<input type="number" value={rag.queries_per_month} onChange={(e) => setRag({ ...rag, queries_per_month: Number(e.target.value) })} /></label>
            <label>Corpus tokens<input type="number" value={rag.corpus_tokens} onChange={(e) => setRag({ ...rag, corpus_tokens: Number(e.target.value) })} /></label>
            <label>Fine-tune jobs / month<input type="number" value={rag.refresh_jobs_per_month} onChange={(e) => setRag({ ...rag, refresh_jobs_per_month: Number(e.target.value) })} /></label>
            <label>Long-context tokens / query<input type="number" value={rag.long_context_tokens} onChange={(e) => setRag({ ...rag, long_context_tokens: Number(e.target.value) })} /></label>
          </div>
        )}
        <div className="btn-row">
          <button className="btn primary" type="submit" disabled={busy}>{busy ? "Computing…" : "Run scenario"}</button>
        </div>
      </form>
      {error && <p className="err">{error}</p>}
      {rows.length > 0 && (
        <section className="panel">
          <h2 className="summary-head">Comparison</h2>
          <div className="card-grid">
            {rows.map((row: any) => (
              <article className={`glow-card ${result.cheapest === (row.model || row.id) ? "winner" : ""}`} key={row.model || row.id}>
                <div className="source-top">
                  <span className="tag">{row.kind || row.id}</span>
                  {result.cheapest === (row.model || row.id) ? <span className="conf">lowest</span> : null}
                </div>
                <h3>{row.label}</h3>
                <p className="price">${Number(row.monthly_usd).toLocaleString()}<small> / month</small></p>
                <p>{(row.notes && row.notes[0]) || row.notes}</p>
              </article>
            ))}
          </div>
          {result.research_query && (
            <div className="btn-row brief-actions">
              <button className="btn primary" type="button" onClick={() => go("/", result.research_query)}>Research why</button>
            </div>
          )}
        </section>
      )}
    </>
  );
}
