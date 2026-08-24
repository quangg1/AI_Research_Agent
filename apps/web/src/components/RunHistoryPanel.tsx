import { useEffect, useMemo, useState } from "react";
import { endpoints } from "../lib/api";
import { loadPinnedClaims, unpinClaim, type PinnedClaim } from "../lib/pinnedClaims";

type RunRow = {
  id: string;
  query: string;
  title?: string | null;
  status: string;
  pinned?: boolean;
  created_at?: string | Date;
};

export function RunHistoryPanel({
  currentRunId,
  currentQuery,
  onOpenRun,
  compareLeft,
  compareRight,
}: {
  currentRunId?: string;
  currentQuery?: string;
  onOpenRun: (id: string) => void;
  compareLeft?: CompareBundle | null;
  compareRight?: CompareBundle | null;
}) {
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [pickA, setPickA] = useState("");
  const [pickB, setPickB] = useState("");
  const [diff, setDiff] = useState<string>("");
  const [pinned, setPinned] = useState<PinnedClaim[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setPinned(loadPinnedClaims());
    void endpoints
      .workspace({ limit: "24" })
      .then((data) => setRuns((data.runs || []) as RunRow[]))
      .catch(() => setRuns([]));
  }, [currentRunId]);

  const related = useMemo(() => {
    const q = (currentQuery || "").toLowerCase().slice(0, 48);
    if (!q) return runs.slice(0, 8);
    return runs
      .filter((r) => r.id !== currentRunId && (r.query || "").toLowerCase().includes(q.slice(0, 24)))
      .slice(0, 8);
  }, [runs, currentQuery, currentRunId]);

  async function runCompare() {
    if (!pickA || !pickB || pickA === pickB) return;
    setBusy(true);
    try {
      const [a, b] = await Promise.all([endpoints.getRun(pickA), endpoints.getRun(pickB)]);
      setDiff(formatCompare(asBundle(a), asBundle(b)));
    } catch (err: any) {
      setDiff(err.message || "Compare failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="panel run-history-panel">
      <h3>Run history</h3>
      <ul className="run-history-list">
        {runs.slice(0, 12).map((r) => (
          <li key={r.id} className={r.id === currentRunId ? "on" : ""}>
            <button type="button" onClick={() => onOpenRun(r.id)}>
              <span className="run-status">{r.status}</span>
              <span className="run-q">{(r.title || r.query || "").slice(0, 72)}</span>
            </button>
          </li>
        ))}
        {!runs.length && <li className="sub">No prior runs yet.</li>}
      </ul>

      {!!related.length && (
        <>
          <h4>Similar questions</h4>
          <ul className="run-history-list compact">
            {related.map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => onOpenRun(r.id)}>
                  {(r.title || r.query || "").slice(0, 64)}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      <h4>Compare runs</h4>
      <div className="compare-row">
        <select value={pickA} onChange={(e) => setPickA(e.target.value)} aria-label="Compare run A">
          <option value="">Run A</option>
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              {(r.title || r.query || r.id).slice(0, 40)}
            </option>
          ))}
        </select>
        <select value={pickB} onChange={(e) => setPickB(e.target.value)} aria-label="Compare run B">
          <option value="">Run B</option>
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              {(r.title || r.query || r.id).slice(0, 40)}
            </option>
          ))}
        </select>
        <button className="btn" type="button" disabled={busy || !pickA || !pickB} onClick={() => void runCompare()}>
          Diff
        </button>
      </div>
      {diff ? <pre className="compare-diff">{diff}</pre> : null}
      {compareLeft && compareRight ? (
        <pre className="compare-diff">{formatCompare(compareLeft, compareRight)}</pre>
      ) : null}

      <h4>Pinned claims</h4>
      <ul className="pinned-claim-list">
        {pinned.map((p) => (
          <li key={p.id}>
            <p>{p.text.slice(0, 140)}</p>
            <div className="btn-row">
              {p.runId ? (
                <button className="btn compact" type="button" onClick={() => onOpenRun(p.runId!)}>
                  Open run
                </button>
              ) : null}
              <button
                className="btn compact"
                type="button"
                onClick={() => setPinned(unpinClaim(p.id))}
              >
                Unpin
              </button>
            </div>
          </li>
        ))}
        {!pinned.length && <li className="sub">Pin claims from the evidence drawer.</li>}
      </ul>
    </aside>
  );
}

export type CompareBundle = {
  id: string;
  query: string;
  title?: string;
  version?: number;
  depth?: number;
  claims: string[];
  metrics: Record<string, unknown>;
};

function asBundle(run: any): CompareBundle {
  const report = run?.agent?.values?.report || {};
  const metrics = report.metrics || run?.metrics_json || {};
  return {
    id: run.id,
    query: run.query || "",
    title: report.title,
    version: metrics.knowledge_version,
    depth: metrics.depth_score,
    claims: (report.claims || []).map((c: any) => String(c.text || "").trim()).filter(Boolean),
    metrics,
  };
}

function formatCompare(a: CompareBundle, b: CompareBundle): string {
  const onlyA = a.claims.filter((t) => !b.claims.some((x) => similar(x, t)));
  const onlyB = b.claims.filter((t) => !a.claims.some((x) => similar(x, t)));
  const lines = [
    `A: ${(a.title || a.query).slice(0, 60)} (v${a.version ?? "?"}, conf ${a.depth ?? "—"})`,
    `B: ${(b.title || b.query).slice(0, 60)} (v${b.version ?? "?"}, conf ${b.depth ?? "—"})`,
    "",
    `Claims only in A (${onlyA.length}):`,
    ...onlyA.slice(0, 6).map((t) => `• ${t.slice(0, 100)}`),
    "",
    `Claims only in B (${onlyB.length}):`,
    ...onlyB.slice(0, 6).map((t) => `• ${t.slice(0, 100)}`),
    "",
    `Sources A/B: ${a.metrics.unique_sources ?? "—"} / ${b.metrics.unique_sources ?? "—"}`,
  ];
  return lines.join("\n");
}

function similar(a: string, b: string) {
  const x = a.toLowerCase().slice(0, 80);
  const y = b.toLowerCase().slice(0, 80);
  return x === y || x.includes(y.slice(0, 40)) || y.includes(x.slice(0, 40));
}
