import { useEffect, useRef, useState } from "react";
import { endpoints } from "../lib/api";

const TIER_LABEL: Record<string, string> = {
  official_regulation: "Primary docs",
  intergovernmental: "Eval lab",
  standard_body: "Framework",
  peer_reviewed: "Peer reviewed",
  specialist_research: "Specialist",
};

export function CorpusPage() {
  const [data, setData] = useState<any>({
    documents: 0,
    org_documents: 0,
    global_documents: 0,
    items: [],
    hosts: {},
    tiers: {},
    uploads: [],
  });
  const [busy, setBusy] = useState(false);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    try {
      setData(await endpoints.corpus());
      setError("");
    } catch (err: any) {
      setError(err.message || "Corpus unavailable");
    }
  }

  useEffect(() => {
    load();
  }, []);

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

  async function upload(file: File) {
    setUploadBusy(true);
    setError("");
    try {
      await endpoints.uploadCorpus(file);
      await load();
    } catch (err: any) {
      setError(err.message || "Upload failed");
    } finally {
      setUploadBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const items = data.items || [];
  const uploads = data.uploads || [];

  return (
    <>
      <section className="hero">
        <div className="hero-kicker">Source watch</div>
        <h1>Your workspace corpus.</h1>
        <p>
          Shared Kiln baseline plus organization uploads. The docs agent retrieves here before the open web — richer
          corpus enables sharper questions.
        </p>
      </section>
      <div className="stat-row">
        <div className="stat">
          <strong>{data.documents || items.length}</strong>
          <span>total chunks</span>
        </div>
        <div className="stat">
          <strong>{data.org_documents || 0}</strong>
          <span>org uploads</span>
        </div>
        <div className="stat">
          <strong>{data.global_documents || 0}</strong>
          <span>shared baseline</span>
        </div>
        <button className="btn primary" type="button" onClick={refresh} disabled={busy || uploadBusy}>
          {busy ? "Refreshing…" : "Re-index org corpus"}
        </button>
      </div>
      <form
        className="panel scenario-form"
        onSubmit={(e) => {
          e.preventDefault();
          const file = fileRef.current?.files?.[0];
          if (file) void upload(file);
        }}
      >
        <label>
          Upload markdown (.md, .txt, max 5 MB)
          <input
            ref={fileRef}
            type="file"
            accept=".md,.txt,.markdown,text/markdown,text/plain"
            aria-label="Corpus upload"
          />
        </label>
        <button className="btn primary" type="submit" disabled={uploadBusy || busy}>
          {uploadBusy ? "Uploading…" : "Upload & index"}
        </button>
      </form>
      {error && <p className="err">{error}</p>}
      {uploads.length > 0 && (
        <section className="panel">
          <h2>Recent uploads</h2>
          <ul className="plain-list">
            {uploads.map((u: any) => (
              <li key={u.id}>
                {u.original_name} · {(Number(u.size_bytes || 0) / 1024).toFixed(1)} KB · {u.status}
              </li>
            ))}
          </ul>
        </section>
      )}
      <div className="card-grid">
        {items.map((d: any) => (
          <article className="glow-card" key={d.id}>
            <div className="source-top">
              <span className="tag">{d.org_id ? "Org" : "Shared"}</span>
              <span className="tag">{TIER_LABEL[d.tier] || d.tier}</span>
              <span className="conf">{Number(d.credibility || 0).toFixed(2)}</span>
            </div>
            <h3>{d.title}</h3>
            <p>{d.snippet}</p>
            {d.url ? (
              <a href={d.url} target="_blank" rel="noreferrer">
                {d.url.replace(/^https?:\/\//, "").slice(0, 48)}
              </a>
            ) : null}
          </article>
        ))}
      </div>
    </>
  );
}
