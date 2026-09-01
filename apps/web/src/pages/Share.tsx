import { useEffect, useMemo, useState } from "react";
import { endpoints } from "../lib/api";
import { DecisionCard } from "../components/DecisionCard";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { MemoMarkdown, prepMemoMarkdown } from "../components/MemoMarkdown";
import { MemoToc } from "../components/MemoReadingChrome";
import { SourcesPanel } from "../components/SourcesPanel";

function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function SharedMemoView({ run }: { run: any }) {
  const report = run?.agent?.values?.report;
  const bodyMd = report?.body_markdown || "";
  const citations = report?.citations || run?.agent?.values?.citations || [];
  const claims = report?.claims || [];
  const metrics = report?.metrics || {};
  const title = run?.title || report?.title || run?.query || "Shared memo";
  const [citeOpen, setCiteOpen] = useState<number | null>(null);

  const tocSource = useMemo(
    () => (bodyMd ? prepMemoMarkdown(bodyMd, { stripTitle: title || undefined }) : ""),
    [bodyMd, title],
  );

  const activeCitation = useMemo(
    () => (citeOpen != null ? citations.find((c: any) => Number(c.n) === citeOpen) : null),
    [citeOpen, citations],
  );

  const relatedClaimsForCite = useMemo(() => {
    if (citeOpen == null) return [];
    return claims
      .filter((c: any) => {
        const blob = `${c.text || ""} ${c.quote || ""} ${c.url || ""}`;
        return blob.includes(`[${citeOpen}]`) || (activeCitation?.url && c.url === activeCitation.url);
      })
      .slice(0, 4);
  }, [citeOpen, claims, activeCitation]);

  function jumpToc(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  if (!bodyMd) {
    return <p className="idle">Status: {run.status}. Full memo not available yet.</p>;
  }

  return (
    <>
      <DecisionCard
        decisionRule={report?.decision_rule}
        atAGlance={report?.at_a_glance}
        executiveSummary={
          report?.executive_summary ||
          (bodyMd.match(/^##\s+Executive summary\s*\n+([\s\S]*?)(?=\n##\s|\n#\s|$)/i)?.[1] || "")
        }
        confidence={metrics.depth_score != null ? Number(metrics.depth_score) : null}
        confidenceLabel={metrics.depth_label ? String(metrics.depth_label) : undefined}
        confidenceBreakdown={metrics.confidence_breakdown}
      />
      <div className={`memo-layout share-memo has-sources${citeOpen != null ? " has-drawer" : ""}`}>
        <MemoToc markdown={tocSource} onJump={jumpToc} />
        <article className="memo md">
          <MemoMarkdown citations={citations} stripTitle={title || undefined} onCiteClick={(n) => setCiteOpen(n)}>
            {bodyMd}
          </MemoMarkdown>
        </article>
        <SourcesPanel citations={citations} activeN={citeOpen} onSelect={setCiteOpen} />
        {citeOpen != null && (
          <EvidenceDrawer
            open
            citeN={citeOpen}
            citation={activeCitation}
            relatedClaims={relatedClaimsForCite}
            runId={run?.id}
            query={run?.query}
            onClose={() => setCiteOpen(null)}
          />
        )}
      </div>
      {!!citations.length && (
        <details className="diagnostics-panel">
          <summary>Full bibliography ({citations.length})</summary>
          <ol className="footnotes">
            {citations.map((c: any) => (
              <li key={c.n || c.evidence_id}>
                <span className="tag">{(c.tier || "source").replaceAll("_", " ")}</span>{" "}
                {c.url ? (
                  <a href={c.url} target="_blank" rel="noreferrer">
                    {c.title || host(c.url)}
                  </a>
                ) : (
                  <span>{c.title}</span>
                )}
              </li>
            ))}
          </ol>
        </details>
      )}
    </>
  );
}

export function SharePage({ token }: { token: string }) {
  const [run, setRun] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    endpoints
      .getShared(token)
      .then(setRun)
      .catch((err: any) => setError(err.message || "Share unavailable"));
  }, [token]);

  const title = run?.title || run?.agent?.values?.report?.title || run?.query || "";

  return (
    <>
      <section className="hero">
        <div className="hero-kicker">Shared research</div>
        <h1>{title || "Shared memo"}</h1>
        <p>Read-only link with citations and decision summary. Sign in to start your own research.</p>
      </section>
      {error && <p className="err">{error}</p>}
      {run && (
        <section className="panel memo-panel share-page">
          <SharedMemoView run={run} />
        </section>
      )}
    </>
  );
}
