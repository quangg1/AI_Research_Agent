const VERIFY_LABEL: Record<string, string> = {
  verified: "Verified",
  unsupported: "Unsupported",
  wrong_number: "Number mismatch",
  source_missing: "Source missing",
  inferred: "Inference",
  pending: "Pending",
};

const KIND_LABEL: Record<string, string> = {
  paper_says: "Direct",
  direct: "Direct",
  derived: "Derived",
  inferred: "Inferred",
  recommendation: "Recommendation",
  speculative: "Speculative",
};

function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function verifyClass(status?: string) {
  if (status === "verified") return "ok";
  if (status === "inferred") return "info";
  if (status === "wrong_number" || status === "unsupported") return "warn";
  return "muted";
}

export function ClaimCard({
  claim,
  extra,
}: {
  claim: any;
  extra?: any;
}) {
  const status = extra?.verification_status || claim.verification_status || "";
  const locator = extra?.locator || claim.locator || "";
  const note = extra?.verification_note || claim.verification_note || "";
  const quote = extra?.quote || claim.quote || "";
  const url = extra?.url || claim.url || "";
  return (
    <div className={`claim-block has-hover ${verifyClass(status)}`}>
      <div className="claim-meta">
        <span className={`verify-pill ${verifyClass(status)}`}>{VERIFY_LABEL[status] || "Claim"}</span>
        {claim.kind ? <span className="locator-chip">{KIND_LABEL[claim.kind] || claim.kind}</span> : null}
        {locator ? <span className="locator-chip">{locator}</span> : null}
        <span className={Number(claim.confidence) < 0.6 ? "score low" : "score"}>
          {Math.round(Number(claim.confidence || 0) * 100)}%
        </span>
      </div>
      <p className="claim-text">{claim.text}</p>
      {(quote || url || note) && (
        <div className="claim-pop" role="tooltip">
          {locator ? <div className="locator-line">{locator}</div> : null}
          {quote ? <div className="quote">{quote}</div> : null}
          {note ? <p className="sub">{note}</p> : null}
          {url ? (
            <a href={url} target="_blank" rel="noreferrer">
              {host(url)}
            </a>
          ) : null}
        </div>
      )}
    </div>
  );
}

export function EvidenceGraphPanel({ graph }: { graph: any }) {
  const claims: any[] = graph?.claims || [];
  const edges: any[] = graph?.edges || [];
  const sources: any[] = graph?.sources || [];
  if (!claims.length) return null;
  const byClaim: Record<string, any[]> = {};
  for (const edge of edges) {
    const key = String(edge.claim_id || "");
    (byClaim[key] ||= []).push(edge);
  }
  const sourceById: Record<string, any> = {};
  for (const src of sources) sourceById[String(src.id || src.url)] = src;
  return (
    <section className="panel evidence-graph">
      <div className="summary-head">
        <div>
          <div className="hero-kicker">Evidence graph</div>
          <h2>Claims against sources</h2>
          <p className="sub">Hover a claim for the locator. + supports, − contradicts. Verification is quote-match, not an extra model call.</p>
        </div>
      </div>
      {claims.map((claim) => (
        <article key={claim.id || claim.text} className="graph-claim">
          <ClaimCard claim={claim} extra={claim} />
          <ul className="graph-edges">
            {(byClaim[String(claim.id || "")] || []).map((edge, i) => {
              const src = sourceById[String(edge.source_id || "")] || {};
              const loc = edge.locator?.label || edge.locator_label || claim.locator;
              const minus = edge.relation === "contradicts";
              return (
                <li key={`${claim.id}-${i}`} className={minus ? "minus" : "plus"}>
                  <span>{minus ? "−" : "+"}</span>{" "}
                  {src.title || src.url || edge.source_url || "source"}
                  {loc ? <em> · {loc}</em> : null}
                </li>
              );
            })}
          </ul>
        </article>
      ))}
    </section>
  );
}
