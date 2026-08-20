import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { endpoints } from "../lib/api";
import { openAuthedEventStream } from "../lib/sse";

const PIPELINE = ["briefing", "planner", "docs", "scholar", "search", "collector", "enrich", "retrieve", "extract", "critic", "hitl", "report"];

const EXAMPLES = [
  { tag: "RAG", text: "Should we fine-tune a 8B model on weekly runbooks, or use RAG over the same docs?" },
  { tag: "Retrieve", text: "Compare a vector-only RAG stack vs BM25 plus a cross-encoder reranker for a 50k-chunk internal corpus." },
  { tag: "Serve", text: "Should we self-host an 8B FP8 model on one H100 or call a 70B API for 20M tokens/day?" },
  { tag: "Eval", text: "Is LLM-as-judge unbiased ground truth for shipping a research agent?" },
];

const STEP_COPY: Record<string, { title: string; hint: string; wait: string }> = {
  briefing: { title: "brief", hint: "Confirm research plan", wait: "Usually a few seconds" },
  planner: { title: "planner", hint: "Decompose the question", wait: "Waiting on Gemini — often 1–3 min" },
  docs: { title: "docs", hint: "Primary docs & frameworks", wait: "A few seconds" },
  scholar: { title: "scholar", hint: "Systems papers", wait: "A few seconds" },
  search: { title: "search", hint: "Current web sources", wait: "Web search can take 5–15s" },
  collector: { title: "collector", hint: "Merge evidence", wait: "Instant" },
  enrich: { title: "enrich", hint: "Fetch full documents", wait: "Fetching pages, 5–15s" },
  retrieve: { title: "retrieve", hint: "Rank by relevance", wait: "A few seconds" },
  extract: { title: "extract", hint: "Quote + citation ledger", wait: "A few seconds" },
  critic: { title: "critic", hint: "Conflict check", wait: "Waiting on Gemini — often 30s–2 min" },
  hitl: { title: "review", hint: "Human approval", wait: "Waiting on you" },
  report: { title: "report", hint: "Write the memo", wait: "Longest Gemini call — often 1–4 min" },
};

const TIER_LABEL: Record<string, string> = {
  official_regulation: "Primary docs",
  intergovernmental: "Eval lab",
  standard_body: "Framework",
  peer_reviewed: "Peer reviewed",
  specialist_research: "Specialist",
  vendor_or_consultancy: "Vendor",
  news_analysis: "Analysis",
};

/** UI view of a validated research run response. Nested graph values stay dynamic. */
type Run = {
  id?: string;
  status?: string;
  query?: string;
  agent?: {
    status?: string;
    interrupt?: any;
    values?: any;
    error?: string | null;
    current_node?: string;
    elapsed_s?: number;
    progress?: number;
    hint?: string;
    eta_s?: number;
    started_at?: number;
  };
};

function formatElapsed(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function toMd(text: string) {
  return (text || "").trim();
}

export function ResearchPage({ go, initialQuery = "" }: { go: (to: string) => void; initialQuery?: string }) {
  const [query, setQuery] = useState(initialQuery);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");
  const [examples, setExamples] = useState(EXAMPLES);
  const [searchOpen, setSearchOpen] = useState(true);
  const [filter, setFilter] = useState("all");
  const [inspect, setInspect] = useState<any | null>(null);
  const [followup, setFollowup] = useState("");
  const [briefDraft, setBriefDraft] = useState<Record<string, any>>({});
  const [tick, setTick] = useState(Date.now());
  const [shareMsg, setShareMsg] = useState("");
  const drawerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const existing = params.get("run");
    if (existing) {
      setRunId(existing);
      setSearchOpen(false);
    }
    endpoints.examples().then((d) => {
      if (d.queries) setExamples(d.queries.map((text: string, i: number) => ({ tag: EXAMPLES[i]?.tag || "Ask", text })));
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (initialQuery) setQuery(initialQuery);
  }, [initialQuery]);

  useEffect(() => {
    if (!runId) return;
    let stop = false;
    let closeStream: (() => void) | null = null;
    const poll = async () => {
      try {
        const data = await endpoints.getRun(runId);
        if (stop) return;
        setRun(data as Run);
        setError("");
        const st = data.agent?.status || data.status;
        const intr = data.agent?.interrupt;
        if (intr?.type === "research_brief" && intr.brief) {
          setBriefDraft((prev) => (Object.keys(prev).length ? prev : { ...intr.brief }));
        }
        if (st === "completed" || st === "awaiting_human") setSearchOpen(false);
        if (st === "completed" || st === "failed" || st === "cancelled" || st === "out_of_scope") {
          closeStream?.();
        }
      } catch (err: any) {
        if (!stop) setError(err.message || "Failed to load run");
      }
    };
    poll();
    void (async () => {
      const { getAuthHeaders } = await import("../lib/api");
      const headers = await getAuthHeaders();
      if (stop) return;
      closeStream = await openAuthedEventStream(
        `${endpoints.api}/v1/research/${runId}/events`,
        headers,
        () => {
          poll();
        },
      );
    })();
    const t = setInterval(poll, 2000);
    return () => {
      stop = true;
      closeStream?.();
      clearInterval(t);
    };
  }, [runId]);

  const values = run?.agent?.values;
  const interrupt = run?.agent?.interrupt;
  const report = values?.report;
  const budget = values?.budget || interrupt?.budget;
  const traces: any[] = values?.traces || [];
  const status = run?.agent?.status || run?.status || "";
  const currentNode = run?.agent?.current_node || (traces.at(-1)?.node as string | undefined);
  const awaiting = status === "awaiting_human" && interrupt;
  const awaitingBrief = awaiting && interrupt?.type === "research_brief";
  const awaitingMemo = awaiting && interrupt?.type === "approve_report";
  const done = status === "completed" && report;
  const running = Boolean(runId && status && !awaitingBrief && !awaitingMemo && !done && status !== "failed");
  const critic = (done ? values?.critic : interrupt?.critic) || {};
  const claims: any[] = done ? report.claims || [] : (!awaitingBrief ? interrupt?.claims_preview || [] : []);
  const evidence: any[] = done ? report.evidence || [] : (!awaitingBrief ? interrupt?.evidence_preview || [] : []);
  const citations: any[] = done ? report.citations || [] : [];
  const bodyMd = done ? report.body_markdown || "" : "";
  const metrics = done ? report.metrics || {} : {};
  const diagnosticsMd = done ? (metrics.diagnostics_markdown as string) || "" : "";
  type CoverageSlot = { id?: string; label?: string; status?: string };
  const coverageSlots: CoverageSlot[] = done
    ? (metrics.must_answer as CoverageSlot[]) || (critic.coverage?.slots as CoverageSlot[]) || []
    : [];
  const compact = Boolean(runId) && !searchOpen && !awaitingBrief;
  const startedAt = run?.agent?.started_at;
  const elapsed = startedAt ? Math.max(0, tick / 1000 - startedAt) : Number(run?.agent?.elapsed_s || 0);
  const progressPct = Math.round(
    Math.min(100, Math.max(2, (run?.agent?.progress ?? (PIPELINE.indexOf(currentNode || "planner") + 0.35) / PIPELINE.length) * 100)),
  );
  const stepMeta = STEP_COPY[currentNode || "planner"] || STEP_COPY.planner;

  useEffect(() => {
    if (!running && status !== "queued") return;
    const t = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running, status]);

  const steps = useMemo(() => {
    const last: Record<string, any> = {};
    for (const t of traces) last[t.node] = t;
    return PIPELINE.map((id) => ({ id, ...STEP_COPY[id], event: last[id] }));
  }, [traces]);

  const cited = useMemo(() => {
    const map = new Map<string, { n: number; ev: any }>();
    let n = 1;
    for (const c of claims) {
      const hit = evidence.find((e) => (c.support_ids || []).includes(e.id)) || evidence.find((e) => e.url === c.url);
      const key = hit?.id || c.url || c.id;
      if (key && !map.has(key)) map.set(key, { n: n++, ev: hit || c });
    }
    return map;
  }, [claims, evidence]);

  const synthesized = useMemo(() => {
    if (!claims.length) return "";
    return claims
      .map((c) => {
        const hit = evidence.find((e) => (c.support_ids || []).includes(e.id)) || evidence.find((e) => e.url === c.url);
        const key = hit?.id || c.url || c.id;
        const num = cited.get(key)?.n;
        const text = (c.text || "").replace(/^#+\s*/, "");
        return `${text}${num ? ` [${num}]` : ""}`;
      })
      .join(" ");
  }, [claims, evidence, cited]);

  const filtered = useMemo(() => {
    return evidence.filter((e) => {
      if (filter === "all") return true;
      if (filter === "high") return Number(e.credibility || e.retrieval_score || 0) >= 0.9 || Number(e.credibility) >= 0.9;
      return e.tier === filter;
    });
  }, [evidence, filter]);

  const counts = useMemo(() => ({
    all: evidence.length,
    official_regulation: evidence.filter((e) => e.tier === "official_regulation").length,
    intergovernmental: evidence.filter((e) => e.tier === "intergovernmental").length,
    high: evidence.filter((e) => Number(e.credibility || 0) >= 0.9).length,
  }), [evidence]);

  async function startResearch(q: string, fresh = false) {
    setError("");
    setBusy(true);
    setRun(null);
    setFilter("all");
    setBriefDraft({});
    try {
      const data = await endpoints.startResearch(q, fresh);
      setRunId(data.id);
      window.history.replaceState({}, "", `/?run=${data.id}`);
    } catch (err: any) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    await startResearch(query);
  }

  async function resume(action: string, extra?: { brief?: Record<string, any> }) {
    if (!runId) return;
    setBusy(true);
    setError("");
    try {
      await endpoints.resume(runId, {
        action,
        notes,
        extra_questions: notes ? [notes] : [],
        brief: extra?.brief || briefDraft,
      });
    } catch (err: any) {
      setError(err.message || "Resume failed");
    } finally {
      setBusy(false);
    }
  }

  async function cancelRun() {
    if (!runId) return;
    setBusy(true);
    try {
      await endpoints.cancel(runId);
      const data = await endpoints.getRun(runId);
      setRun(data as Run);
    } catch (err: any) {
      setError(err.message || "Cancel failed");
    } finally {
      setBusy(false);
    }
  }

  async function retryRun() {
    if (!runId) return;
    setBusy(true);
    try {
      const data = await endpoints.duplicate(runId);
      setRunId(data.id);
      window.history.replaceState({}, "", `/?run=${data.id}`);
    } catch (err: any) {
      setError(err.message || "Retry failed");
    } finally {
      setBusy(false);
    }
  }

  function updateBriefField(key: string, value: any) {
    setBriefDraft((prev) => ({ ...prev, [key]: value }));
  }

  function listToText(value: any) {
    if (Array.isArray(value)) return value.join("\n");
    return String(value || "");
  }

  function textToList(value: string) {
    return value.split("\n").map((x) => x.trim()).filter(Boolean);
  }

  function reportMarkdown() {
    if (bodyMd) return bodyMd;
    const lines = [`# ${report?.title || "Kiln research memo"}`, "", synthesized, "", "## Claims", ""];
    claims.forEach((c, i) => {
      lines.push(`### ${i + 1}. ${c.text}`, "", c.quote ? `> ${c.quote}` : "", c.url ? `[${host(c.url)}](${c.url})` : "", "");
    });
    lines.push("## Sources", "");
    evidence.forEach((e, i) => lines.push(`${i + 1}. [${e.title || e.url}](${e.url || ""}) — ${e.tier || ""}`));
    return lines.join("\n");
  }

  async function copyReport() {
    await navigator.clipboard.writeText(reportMarkdown());
  }

  function exportMd() {
    const blob = new Blob([reportMarkdown()], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "kiln-memo.md";
    a.click();
  }

  async function shareLink() {
    if (!runId) return;
    try {
      const share = await endpoints.createShare(runId);
      const url = `${window.location.origin}${share.url_path || `/share/${share.token}`}`;
      await navigator.clipboard.writeText(String(url));
      setShareMsg("Secure share link copied");
      await endpoints.track("report_exported", { kind: "share", runId });
      setTimeout(() => setShareMsg(""), 2500);
    } catch (err: any) {
      setError(err.message || "Share failed");
    }
  }

  async function askFollowup() {
    if (!followup.trim()) return;
    const next = `Follow-up on: ${query}\n\n${followup}\n\nPrior findings:\n${synthesized.slice(0, 1200)}`;
    setQuery(next);
    setSearchOpen(true);
    await startResearch(next);
    setFollowup("");
  }

  function stepState(step: any) {
    const ev = step.event;
    if (step.id === "critic" && (ev?.status === "contradicted" || critic.status === "contradicted")) return "warn";
    if (awaitingBrief && step.id === "briefing") return "now";
    if (awaitingMemo && step.id === "hitl") return "now";
    if (currentNode === step.id && !done) return ev?.skipped ? "skip" : "now";
    if (ev?.skipped) return "skip";
    if (ev || (done && step.id !== "report")) return "done";
    if (step.id === "report" && done) return "done";
    return "todo";
  }

  function inspectText(step: any) {
    if (step.id === "critic") {
      const reasons = critic.reasons || [];
      return reasons.length
        ? reasons.join("\n\n")
        : "The critic compared primary docs against papers and vendor blogs, and flagged contradictions — usually RAG vs vectors, or LLM-as-judge vs human labels.";
    }
    const ev = step.event || {};
    if (ev.skipped) return `${step.title} was skipped for this question (adaptive routing / budget).`;
    if (typeof ev.n === "number") return `${step.title} returned ${ev.n} items.`;
    return step.hint;
  }

  return (
    <>
      {compact ? (
        <button className="compact-query" type="button" onClick={() => setSearchOpen(true)}>
          {query || "Edit question"}
        </button>
      ) : (
        <>
          {!runId && (
            <section className="hero">
              <div className="hero-kicker">LLM systems research</div>
              <h1>Decide with evidence, not vendor decks.</h1>
              <p>Cited answers for serving, RAG, agents, and eval — with the agent loop visible.</p>
              <div className="stat-row">
                <div className="stat"><strong>11</strong><span>graph nodes</span></div>
                <div className="stat"><strong>3</strong><span>adaptive agents</span></div>
                <div className="stat"><strong>HITL</strong><span>brief + memo gates</span></div>
                <button className="stat linkish" type="button" onClick={() => go("/scenarios")}>
                  <strong>Cost</strong><span>open scenario engine</span>
                </button>
              </div>
            </section>
          )}
          <form className="search-card" onSubmit={onSubmit}>
            <label className="sr-only" htmlFor="research-query">
              Research question
            </label>
            <textarea
              id="research-query"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ask a serving, RAG, agent, or eval question"
            />
            <div className="search-actions">
              {runId && (
                <button className="btn" type="button" onClick={() => setSearchOpen(false)}>
                  Collapse
                </button>
              )}
              <button className="btn primary" type="submit" disabled={busy || query.trim().length < 8}>
                {busy ? "Starting…" : "Research"}
              </button>
            </div>
          </form>
          {!runId && (
            <div className="suggest">
              {examples.map((ex) => (
                <button type="button" className="badge" key={ex.text} onClick={() => setQuery(ex.text)}>
                  {ex.tag}: {ex.text.length > 72 ? `${ex.text.slice(0, 72)}…` : ex.text}
                </button>
              ))}
            </div>
          )}
        </>
      )}
      {error && (
        <p className="err" role="alert">
          {error}
        </p>
      )}
      {shareMsg && (
        <p className="idle" aria-live="polite">
          {shareMsg}
        </p>
      )}

      {runId && (
        <div className="workspace">
          <aside className="panel pipeline">
            <h3>Agent pipeline</h3>
            {steps.map((step) => {
              const state = stepState(step);
              const ev = step.event;
              return (
                <button key={step.id} className={`step ${state === "now" || state === "warn" ? "active" : ""}`} type="button" onClick={() => setInspect(step)}>
                  <span className={`ico ${state}`}>
                    {state === "done" ? "✓" : state === "skip" ? "–" : state === "warn" ? "!" : state === "now" ? "●" : "○"}
                  </span>
                  <span>
                    <div className="label">{step.title}{typeof ev?.n === "number" ? ` (${ev.n})` : ""}{ev?.skipped ? " · skip" : ""}{ev?.status === "contradicted" ? " · contradicted" : ""}</div>
                    <div className="sub">{step.hint}</div>
                  </span>
                </button>
              );
            })}
            {budget && (
              <div className="metrics">
                <div>{budget.iterations}/{budget.max_iterations} passes · {budget.used_tool_calls}/{budget.max_tool_calls} calls{budget.used_tokens ? ` · ${budget.used_tokens} tok` : ""}</div>
                {running && <div className="metrics-live">{formatElapsed(elapsed)} elapsed · step {(PIPELINE.indexOf(currentNode || "planner") + 1) || 2}/{PIPELINE.length}</div>}
              </div>
            )}
          </aside>

          <main>
            {awaitingBrief && (
              <section className="panel brief-panel">
                <div className="summary-head">
                  <div>
                    <h2>{interrupt.title || "Research plan"}</h2>
                    <p className="brief-sub">{interrupt.subtitle || "Edit the brief, then start deep research."}</p>
                  </div>
                </div>
                <table className="brief-table">
                  <tbody>
                    {(interrupt.rows || []).map((row: any) => {
                      const key = row.key;
                      const value = briefDraft[key] ?? row.value;
                      const kind = row.kind || (Array.isArray(value) ? "list" : "text");
                      return (
                        <tr key={key}>
                          <th>{row.label || key}</th>
                          <td>
                            {kind === "select" ? (
                              <select value={String(value || "")} onChange={(e) => updateBriefField(key, e.target.value)}>
                                {(row.options || []).map((opt: string) => (
                                  <option key={opt} value={opt}>{opt}</option>
                                ))}
                              </select>
                            ) : kind === "list" ? (
                              <textarea value={listToText(value)} onChange={(e) => updateBriefField(key, textToList(e.target.value))} rows={3} />
                            ) : (
                              <textarea value={String(value || "")} onChange={(e) => updateBriefField(key, e.target.value)} rows={key === "goal" || key === "deliverable" ? 2 : 1} />
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <div className="btn-row brief-actions">
                  <button className="btn primary" type="button" onClick={() => resume("start", { brief: briefDraft })} disabled={busy}>Start research</button>
                  <button className="btn" type="button" onClick={() => resume("cancel")} disabled={busy}>Cancel</button>
                </div>
              </section>
            )}

            {(done || awaitingMemo || (!awaitingBrief && claims.length > 0)) && (
              <section className="panel memo-panel">
                <div className="summary-head">
                  <div>
                    <div className="hero-kicker">Deep research memo</div>
                    <h2>{report?.title || "Executive summary"}</h2>
                  </div>
                  {done && (
                    <div className="btn-row report-actions">
                      <button className="btn" type="button" onClick={copyReport}>
                        Copy
                      </button>
                      <button className="btn" type="button" onClick={exportMd}>
                        Export MD
                      </button>
                      <button className="btn" type="button" onClick={() => window.print()}>
                        Export PDF
                      </button>
                      <button className="btn" type="button" onClick={shareLink}>
                        Share link
                      </button>
                      <button className="btn" type="button" onClick={retryRun} disabled={busy}>
                        Duplicate
                      </button>
                    </div>
                  )}
                </div>
                {awaitingMemo && (
                  <div className="review-box">
                    <p className="sub">Review the memo and sources, then approve or send the agent back.</p>
                    <textarea placeholder="Optional follow-up for the next loop" value={notes} onChange={(e) => setNotes(e.target.value)} />
                    <div className="btn-row">
                      <button className="btn primary" type="button" onClick={() => resume("approve")} disabled={busy}>Approve</button>
                      <button className="btn" type="button" onClick={() => resume("revise")} disabled={busy}>Dig further</button>
                    </div>
                  </div>
                )}
                {metrics.reuse_mode === "cached" && (
                  <div className="reuse-note">
                    <p>
                      Answered from a previously researched question ({Math.round(Number(metrics.reuse_similarity || 0) * 100)}% match,
                      memo v{metrics.knowledge_version ?? 1}
                      {metrics.knowledge_age_days != null ? `, ${Math.round(Number(metrics.knowledge_age_days))} days old` : ""}).
                      No searches or model calls were spent on it.
                    </p>
                    <button className="btn" type="button" disabled={busy} onClick={() => startResearch(query, true)}>
                      Research again from scratch
                    </button>
                  </div>
                )}
                {bodyMd ? (
                  <article className="memo md">
                    <Markdown remarkPlugins={[remarkGfm]}>{toMd(bodyMd)}</Markdown>
                  </article>
                ) : synthesized ? (
                  <p className="synth">
                    {synthesized.split(/(\[\d+\])/).map((part, i) =>
                      /^\[\d+\]$/.test(part) ? <sup className="cite" key={i}>{part}</sup> : <span key={i}>{part}</span>,
                    )}
                  </p>
                ) : null}
                {!done && !!citations.length && (
                  <ol className="footnotes">
                    {citations.map((c: any) => (
                      <li key={c.n || c.evidence_id}>
                        <span className="tag">{TIER_LABEL[c.tier] || c.tier}</span>{" "}
                        {c.url ? <a href={c.url} target="_blank" rel="noreferrer">{c.title || host(c.url)}</a> : <span>{c.title}</span>}
                        {c.url ? <div className="source-meta">{c.url}</div> : null}
                        {c.quote ? <div className="quote">{c.quote}</div> : null}
                      </li>
                    ))}
                  </ol>
                )}
                {!done && claims.map((c, i) => (
                  <div className="claim-block" key={c.id || i}>
                    <div className={Number(c.confidence) < 0.6 ? "score low" : "score"}>
                      {Math.round(Number(c.confidence || 0) * 100)}% · {TIER_LABEL[c.tier] || (c.tier || "").replaceAll("_", " ") || "claim"}
                    </div>
                    <div className="md"><Markdown remarkPlugins={[remarkGfm]}>{toMd(c.text)}</Markdown></div>
                    {c.quote && (
                      <div className="quote md"><Markdown remarkPlugins={[remarkGfm]}>{toMd(c.quote)}</Markdown></div>
                    )}
                  </div>
                ))}
                {done && (
                  <details className="diagnostics-panel">
                    <summary>Research diagnostics</summary>
                    <div className="metrics memo-metrics">
                      {metrics.synthesis_status ? `${metrics.synthesis_status} · ` : ""}
                      {metrics.version ? `${metrics.version} · ` : ""}
                      {metrics.depth_score != null ? `confidence ${metrics.depth_score}/100 (${metrics.depth_label || "n/a"}) · ` : ""}
                      {metrics.must_answer_fraction || metrics.coverage_ratio != null
                        ? `must-answer ${metrics.must_answer_fraction || `${Math.round(Number(metrics.coverage_ratio) * 100)}%`} · `
                        : ""}
                      {metrics.critical_fraction ? `critical ${metrics.critical_fraction} · ` : ""}
                      {metrics.has_implementation === false ? "official-impl ✗ · " : metrics.has_implementation ? "official-impl ✓ · " : ""}
                      {metrics.unique_sources != null ? `${metrics.unique_sources} unique · ` : ""}
                      {metrics.iterations != null ? `${metrics.iterations} iter · ` : ""}
                      {metrics.tool_calls != null ? `${metrics.tool_calls} tools · ` : ""}
                      critic {critic.status || "n/a"}
                      {metrics.knowledge_version != null ? ` · memo v${metrics.knowledge_version}` : ""}
                      {metrics.reuse_mode
                        ? ` · reuse ${metrics.reuse_mode}${
                            metrics.reuse_similarity != null
                              ? ` (${Math.round(Number(metrics.reuse_similarity) * 100)}% match)`
                              : ""
                          }`
                        : ""}
                      {metrics.llm_error ? ` · llm: ${String(metrics.llm_error).slice(0, 80)}` : ""}
                    </div>
                    {coverageSlots.length > 0 && (
                      <div className="coverage-grid">
                        {coverageSlots.map((s) => (
                          <span key={s.id || s.label} className={`coverage-chip ${s.status || "open"}`}>
                            {s.status === "covered" ? "✓" : s.status === "weak" ? "⚠" : "✗"} {s.label || s.id}
                          </span>
                        ))}
                      </div>
                    )}
                    {diagnosticsMd ? (
                      <article className="memo md diagnostics-md">
                        <Markdown remarkPlugins={[remarkGfm]}>{toMd(diagnosticsMd)}</Markdown>
                      </article>
                    ) : null}
                    {!!claims.length && (
                      <div className="diagnostics-claims">
                        <h4>Claim ledger</h4>
                        {claims.map((c, i) => (
                          <div className="claim-block" key={c.id || i}>
                            <div className={Number(c.confidence) < 0.6 ? "score low" : "score"}>
                              {Math.round(Number(c.confidence || 0) * 100)}% · {TIER_LABEL[c.tier] || (c.tier || "").replaceAll("_", " ") || "claim"}
                            </div>
                            <div className="md"><Markdown remarkPlugins={[remarkGfm]}>{toMd(c.text)}</Markdown></div>
                            {c.quote && (
                              <div className="quote md"><Markdown remarkPlugins={[remarkGfm]}>{toMd(c.quote)}</Markdown></div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                    {!!citations.length && (
                      <ol className="footnotes">
                        {citations.map((c: any) => (
                          <li key={c.n || c.evidence_id}>
                            <span className="tag">{TIER_LABEL[c.tier] || c.tier}</span>{" "}
                            {c.url ? <a href={c.url} target="_blank" rel="noreferrer">{c.title || host(c.url)}</a> : <span>{c.title}</span>}
                            {c.url ? <div className="source-meta">{c.url}</div> : null}
                            {c.quote ? <div className="quote">{c.quote}</div> : null}
                          </li>
                        ))}
                      </ol>
                    )}
                  </details>
                )}
                {done && (
                  <div className="review-box">
                    <textarea placeholder="Ask a follow-up on this memo" value={followup} onChange={(e) => setFollowup(e.target.value)} />
                    <button className="btn primary" type="button" onClick={askFollowup} disabled={busy || followup.trim().length < 4}>Ask follow-up</button>
                  </div>
                )}
              </section>
            )}

            {!awaitingBrief && !awaitingMemo && !done && status && status !== "queued" && (
              <section className="panel progress-panel">
                <div className="progress-head">
                  <span className="pulse" />
                  <div>
                    <h2>{stepMeta.title}</h2>
                    <p>{run?.agent?.hint || stepMeta.hint}</p>
                  </div>
                  <time>{formatElapsed(elapsed)}</time>
                </div>
                <div className="progress-track" aria-label={`Progress ${progressPct} percent`}>
                  <div className="progress-fill" style={{ width: `${progressPct}%` }} />
                </div>
                <div className="progress-meta">
                  Step {Math.max(1, PIPELINE.indexOf(currentNode || "planner") + 1)}/{PIPELINE.length}
                  {" · "}
                  {stepMeta.wait}
                  {" · "}
                  whole run is often several minutes after you confirm the brief
                </div>
                {status === "failed" ? (
                  <div>
                    <p className="err">{run?.agent?.error || "This run failed."}</p>
                    <button className="btn primary" type="button" onClick={retryRun} disabled={busy}>
                      Retry from scratch
                    </button>
                  </div>
                ) : elapsed > 50 ? (
                  <p className="progress-note">
                    Still on this step after {formatElapsed(elapsed)}. We will notify you when review is needed — you can
                    leave this tab open.
                  </p>
                ) : (
                  <p className="progress-note">
                    You can leave this tab open — the pipeline keeps running. Alerts appear when human review is needed.
                  </p>
                )}
                {running && (
                  <div className="btn-row" style={{ marginTop: 12 }}>
                    <button className="btn" type="button" onClick={cancelRun} disabled={busy}>
                      Cancel run
                    </button>
                  </div>
                )}
              </section>
            )}
            {status === "queued" && (
              <p className="idle"><span className="pulse" /> Queued — starting the agent…</p>
            )}

            {!!evidence.length && !awaitingBrief && !done && (
              <section>
                <div className="filters">
                  {[
                    ["all", `All (${counts.all})`],
                    ["official_regulation", `Primary docs (${counts.official_regulation})`],
                    ["intergovernmental", `Eval labs (${counts.intergovernmental})`],
                    ["high", `High relevance (${counts.high})`],
                  ].map(([id, label]) => (
                    <button key={id} className={`filter ${filter === id ? "on" : ""}`} type="button" onClick={() => setFilter(id)}>{label}</button>
                  ))}
                </div>
                {filtered.map((e: any, i: number) => (
                  <article className="source-card" key={e.id || e.url || i}>
                    <div className="source-top">
                      <span className="tag">{TIER_LABEL[e.tier] || (e.tier || "source").replaceAll("_", " ")}</span>
                      <span className="conf">{Number(e.credibility || 0).toFixed(2)}</span>
                    </div>
                    <div className="md"><Markdown remarkPlugins={[remarkGfm]}>{toMd(e.title || "")}</Markdown></div>
                    {e.snippet && (
                      <div className="md"><Markdown remarkPlugins={[remarkGfm]}>{toMd(String(e.snippet).slice(0, 420))}</Markdown></div>
                    )}
                    <div className="source-meta">
                      {e.url ? <a href={e.url} target="_blank" rel="noreferrer">{host(e.url)} ↗</a> : null}
                      {e.published ? ` · ${e.published}` : ""}
                    </div>
                  </article>
                ))}
              </section>
            )}
          </main>
        </div>
      )}

      {inspect && (
        <div
          className="drawer-bg"
          onClick={() => setInspect(null)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setInspect(null);
          }}
        >
          <aside
            className="drawer"
            ref={drawerRef}
            role="dialog"
            aria-modal="true"
            aria-label={inspect.title}
            onClick={(e) => e.stopPropagation()}
          >
            <h3>{inspect.title}</h3>
            <p>{inspectText(inspect)}</p>
            {inspect.id === "critic" && (critic.followup_queries || []).length > 0 && (
              <p>Next queries: {(critic.followup_queries || []).map((q: any) => q.question || q).join(" · ")}</p>
            )}
            <button className="btn" type="button" onClick={() => setInspect(null)}>
              Close
            </button>
          </aside>
        </div>
      )}
    </>
  );
}
