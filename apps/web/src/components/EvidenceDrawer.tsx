import { pinClaim } from "../lib/pinnedClaims";

export type DrawerCite = {
  n?: number;
  title?: string;
  url?: string;
  quote?: string;
  tier?: string;
  host?: string;
};

export type DrawerClaim = {
  id?: string;
  text?: string;
  quote?: string;
  url?: string;
  confidence?: number;
};

export function EvidenceDrawer({
  open,
  citeN,
  citation,
  relatedClaims,
  runId,
  query,
  onClose,
  onPinned,
}: {
  open: boolean;
  citeN: number | null;
  citation?: DrawerCite | null;
  relatedClaims?: DrawerClaim[];
  runId?: string;
  query?: string;
  onClose: () => void;
  onPinned?: () => void;
}) {
  if (!open || citeN == null) return null;
  const host =
    citation?.host ||
    (citation?.url ? (() => {
      try {
        return new URL(citation.url).host;
      } catch {
        return citation.url;
      }
    })() : "");

  return (
    <aside className="evidence-drawer" aria-label={`Source ${citeN}`}>
      <div className="evidence-drawer-head">
        <h3>Source [{citeN}]</h3>
        <button className="btn compact" type="button" onClick={onClose} aria-label="Close">
          Close
        </button>
      </div>
      {citation ? (
        <>
          <p className="evidence-title">{citation.title || host || "Untitled source"}</p>
          {host ? <p className="source-meta">{host}</p> : null}
          {citation.url ? (
            <a className="btn primary" href={citation.url} target="_blank" rel="noreferrer">
              Open source ↗
            </a>
          ) : (
            <p className="sub">No URL on this citation.</p>
          )}
          {citation.quote ? (
            <blockquote className="memo-quote evidence-quote">{citation.quote}</blockquote>
          ) : (
            <p className="sub">No excerpt stored for this citation.</p>
          )}
          {citation.tier ? <span className="tag">{citation.tier.replaceAll("_", " ")}</span> : null}
        </>
      ) : (
        <p className="sub">Citation [{citeN}] is not in the ledger.</p>
      )}
      {!!relatedClaims?.length && (
        <div className="evidence-related">
          <h4>Related claims</h4>
          {relatedClaims.map((c, i) => (
            <div className="evidence-claim" key={c.id || i}>
              <p>{c.text}</p>
              <button
                className="btn compact"
                type="button"
                onClick={() => {
                  pinClaim({
                    id: c.id,
                    text: c.text || "",
                    url: c.url || citation?.url,
                    quote: c.quote || citation?.quote,
                    confidence: c.confidence,
                    runId,
                    query,
                    n: citeN,
                  });
                  onPinned?.();
                }}
              >
                Pin claim
              </button>
            </div>
          ))}
        </div>
      )}
      {citation && (
        <button
          className="btn"
          type="button"
          onClick={() => {
            pinClaim({
              text: citation.title || citation.quote || `Source [${citeN}]`,
              url: citation.url,
              quote: citation.quote,
              runId,
              query,
              n: citeN,
            });
            onPinned?.();
          }}
        >
          Pin this source
        </button>
      )}
    </aside>
  );
}
