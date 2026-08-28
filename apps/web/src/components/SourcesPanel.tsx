const TIER_LABEL: Record<string, string> = {
  official_regulation: "Primary docs",
  intergovernmental: "Eval lab",
  standard_body: "Framework",
  peer_reviewed: "Peer reviewed",
  specialist_research: "Specialist",
  vendor_or_consultancy: "Vendor",
  news_analysis: "Analysis",
};

function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function SourcesPanel({
  citations,
  activeN,
  onSelect,
  title = "Sources",
}: {
  citations: any[];
  activeN?: number | null;
  onSelect: (n: number) => void;
  title?: string;
}) {
  if (!citations.length) return null;
  return (
    <aside className="sources-panel" aria-label={title}>
      <div className="sources-panel-head">
        <h3>{title}</h3>
        <span className="sources-count">{citations.length}</span>
      </div>
      <ol className="sources-list">
        {citations.map((c: any) => {
          const n = Number(c.n);
          if (!Number.isFinite(n)) return null;
          return (
            <li key={c.n || c.evidence_id || n}>
              <button
                type="button"
                className={`sources-item${activeN === n ? " on" : ""}`}
                onClick={() => onSelect(n)}
              >
                <span className="sources-n">[{n}]</span>
                <span className="sources-body">
                  <span className="tag">{TIER_LABEL[c.tier] || (c.tier || "source").replaceAll("_", " ")}</span>
                  <span className="sources-title">{c.title || host(c.url || "") || "Untitled"}</span>
                  {c.url ? <span className="source-meta">{host(c.url)}</span> : null}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}
