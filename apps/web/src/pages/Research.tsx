import { FormEvent, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { endpoints } from "../lib/api";
import { byokReady, canSubmitResearch, isCreditsExhaustedMessage, loadByok, llmPayload, type ByokState } from "../lib/byok";
import { copyForNotion, downloadExportPack } from "../lib/exportMemo";
import { dismissFirstRun, firstRunDismissed, FIRST_RUN_TEMPLATES } from "../lib/firstRun";
import { notifyResearch, requestNotificationPermission } from "../lib/notifications";
import { ByokPanel } from "../components/ByokPanel";
import { ClaimCard, EvidenceGraphPanel } from "../components/EvidenceGraph";
import { DecisionCard } from "../components/DecisionCard";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { FirstRunBanner } from "../components/FirstRunBanner";
import { MemoMarkdown, prepMemoMarkdown } from "../components/MemoMarkdown";
import { loadReaderPrefs, MemoToc, ReaderToolbar, type ReaderPrefs } from "../components/MemoReadingChrome";
import { ResearchThread } from "../components/ResearchThread";
import { RunHistoryPanel } from "../components/RunHistoryPanel";
import { SourcesPanel } from "../components/SourcesPanel";
import { openAuthedEventStream } from "../lib/sse";

const PIPELINE = ["briefing", "planner", "plan_gate", "docs", "scholar", "search", "collector", "enrich", "retrieve", "extract", "critic", "hitl", "report", "memo_gate"];

const EXAMPLES = [
  { tag: "RAG", text: "Should we fine-tune a 8B model on weekly runbooks, or use RAG over the same docs?" },
  { tag: "Retrieve", text: "Compare a vector-only RAG stack vs BM25 plus a cross-encoder reranker for a 50k-chunk internal corpus." },
  { tag: "Serve", text: "Should we self-host an 8B FP8 model on one H100 or call a 70B API for 20M tokens/day?" },
  { tag: "Eval", text: "Is LLM-as-judge unbiased ground truth for shipping a research agent?" },
];

const STEP_COPY: Record<string, { title: string; hint: string; wait: string }> = {
  briefing: { title: "brief", hint: "Confirm research plan", wait: "Usually a few seconds" },
  planner: { title: "planner", hint: "Decompose the question", wait: "Waiting on your model — often 1–3 min" },
  plan_gate: { title: "plan", hint: "Review agent plan", wait: "Waiting on you" },
  docs: { title: "docs", hint: "Primary docs & frameworks", wait: "A few seconds" },
  scholar: { title: "scholar", hint: "Systems papers", wait: "A few seconds" },
  search: { title: "search", hint: "Current web sources", wait: "Web search can take 5–15s" },
  collector: { title: "collector", hint: "Merge evidence", wait: "Instant" },
  enrich: { title: "enrich", hint: "Fetch full documents", wait: "Fetching pages, 5–15s" },
  retrieve: { title: "retrieve", hint: "Rank by relevance", wait: "A few seconds" },
  extract: { title: "extract", hint: "Quote + citation ledger", wait: "A few seconds" },
  critic: { title: "critic", hint: "Conflict check", wait: "Waiting on your model — often 30s–2 min" },
  hitl: { title: "review", hint: "Human approval", wait: "Waiting on you" },
  report: { title: "report", hint: "Write the long memo", wait: "Compress notes then write — often 2–6 min" },
  memo_gate: { title: "publish", hint: "Review memo draft", wait: "Waiting on you" },
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
  error?: string | null;
  interrupt_payload?: any;
  evidence_graph?: any;
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
    next?: string[];
  };
  thread?: {
    parent?: { id: string; query: string; status: string } | null;
    children?: { id: string; query: string; status: string }[];
  };
};

function unwrapInterrupt(raw: any): any {
  let cur = raw;
  for (let i = 0; i < 6; i += 1) {
    if (!cur) return cur;
    if (Array.isArray(cur)) {
      cur = cur[0];
      continue;
    }
    if (typeof cur !== "object") return cur;
    if (cur.type) return cur;
    const inner = cur.value ?? cur.interrupt ?? (Array.isArray(cur.interrupts) ? cur.interrupts[0] : undefined);
    if (inner != null) {
      cur = inner;
      continue;
    }
    return cur;
  }
  return cur;
}

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
  const [planDraft, setPlanDraft] = useState<{ sub_queries: any[]; agents_to_run: string[] }>({
    sub_queries: [],
    agents_to_run: [],
  });
  const [draftNotes, setDraftNotes] = useState("");
  const [tick, setTick] = useState(Date.now());
  const [shareMsg, setShareMsg] = useState("");
  const [byok, setByok] = useState<ByokState>(() => loadByok());
  const [creditsForced, setCreditsForced] = useState(false);
  const [platformProviders, setPlatformProviders] = useState<Record<string, boolean>>({});
  const [byokRequired, setByokRequired] = useState(false);
  const [showFirstRun, setShowFirstRun] = useState(() => !firstRunDismissed());
  const [readerPrefs, setReaderPrefs] = useState<ReaderPrefs>(() => loadReaderPrefs());
  const [citeOpen, setCiteOpen] = useState<number | null>(null);
  const [pinToast, setPinToast] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const notifiedStatusRef = useRef<string>("");
  const [railCollapsed, setRailCollapsed] = useState(() => {
    try {
      return localStorage.getItem("kiln_rail_collapsed") === "1";
    } catch {
      return false;
    }
  });
  const drawerRef = useRef<HTMLElement | null>(null);
  const memoArticleRef = useRef<HTMLElement | null>(null);

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
    endpoints
      .status()
      .then((d) => {
        const providers = d.platform_providers;
        if (providers && typeof providers === "object") {
          setPlatformProviders(providers as Record<string, boolean>);
        }
        if (d.byok_required === true) setByokRequired(true);
      })
      .catch(() => undefined);
    void requestNotificationPermission();
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
        const failText = String(data.agent?.error || data.error || "");
        const typed = data as Run;
        const st = typed.status === "awaiting_brief" ? "awaiting_human" : (typed.status || typed.agent?.status);
        const intr =
          unwrapInterrupt(typed.agent?.interrupt) || unwrapInterrupt(typed.interrupt_payload);
        const waiting = st === "awaiting_human" || intr?.type === "plan_review" || intr?.type === "research_brief" || intr?.type === "memo_draft";
        const creditsPause =
          intr?.type === "credits_exhausted" || isCreditsExhaustedMessage(failText) || isCreditsExhaustedMessage(String(intr?.message || ""));
        if (creditsPause) {
          setCreditsForced(true);
          setError(String(intr?.message || failText || "Model credits exhausted."));
          setSearchOpen(true);
        } else {
          setError("");
        }
        if (intr?.type === "research_brief" && intr.brief) {
          setBriefDraft((prev) => (Object.keys(prev).length ? prev : { ...intr.brief }));
        }
        if (intr?.type === "plan_review") {
          setPlanDraft((prev) => {
            const incoming = intr.sub_queries || intr.plan?.sub_queries || [];
            if (!incoming.length) return prev;
            if (prev.sub_queries.length) return prev;
            return {
              sub_queries: incoming,
              agents_to_run: intr.agents_to_run || intr.plan?.agents_to_run || [],
            };
          });
        }
        // Keep the key panel open when credits forced this poll; otherwise collapse on brief/done.
        if (st === "completed" || st === "awaiting_human") {
          if (!creditsPause) setSearchOpen(false);
        }
        if (st === "completed" || st === "failed" || st === "cancelled" || st === "out_of_scope") {
          closeStream?.();
        }
        const notifyKey = `${runId}:${st}`;
        if (
          runId &&
          notifyKey !== notifiedStatusRef.current &&
          (st === "completed" || st === "awaiting_human" || st === "failed")
        ) {
          notifiedStatusRef.current = notifyKey;
          if (st === "completed") {
            notifyResearch("Research complete", "Your memo is ready to read.", runId);
          } else if (st === "awaiting_human") {
            notifyResearch("Review needed", "Kiln is waiting for your input on this run.", runId);
          } else if (st === "failed") {
            notifyResearch("Research failed", failText || "Open the run for details.", runId);
          }
        }
      } catch (err: any) {
        if (!stop) setError(err.message || "Failed to load run");
        if (isCreditsExhaustedMessage(String(err.message || ""))) setCreditsForced(true);
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
  // Row status wins while executing — except a parked HITL/brief gate must still
  // surface even if a heartbeat left the DB row as "running".
  const rowStatus = run?.status === "awaiting_brief" ? "awaiting_human" : run?.status || "";
  const gateInterrupt =
    unwrapInterrupt(run?.agent?.interrupt) || unwrapInterrupt(run?.interrupt_payload);
  const gateInterruptType = String(gateInterrupt?.type || "");
  const parkedGate =
    rowStatus === "awaiting_human" ||
    ["research_brief", "plan_review", "memo_draft", "approve_report", "credits_exhausted"].includes(
      gateInterruptType,
    );
  const executing = (rowStatus === "queued" || rowStatus === "running") && !parkedGate;
  const status = parkedGate
    ? "awaiting_human"
    : executing
      ? rowStatus
      : rowStatus === "awaiting_human" || run?.agent?.status === "awaiting_human"
        ? "awaiting_human"
        : rowStatus || run?.agent?.status || "";
  const rawInterrupt = executing ? undefined : gateInterrupt;
  const traces: any[] = values?.traces || [];
  const nextNodes: string[] = Array.isArray(run?.agent?.next) ? run.agent.next.map(String) : [];
  const writingReport =
    !parkedGate &&
    (String(values?.status || "") === "approved" ||
      nextNodes.includes("report") ||
      (rowStatus === "running" && traces.some((t) => t?.node === "hitl" && t?.action === "approve")));
  const researchTrace = run?.agent?.research_trace || run?.research_trace;
  const RESEARCH_NODES = ["search", "scholar", "docs", "collector", "enrich", "retrieve", "extract"];
  const activeResearch =
    researchTrace?.active_agent && PIPELINE.includes(String(researchTrace.active_agent))
      ? String(researchTrace.active_agent)
      : nextNodes.find((n) => RESEARCH_NODES.includes(n));
  const currentNode =
    (writingReport ? "report" : undefined) ||
    (executing && activeResearch ? activeResearch : undefined) ||
    run?.agent?.current_node ||
    (traces.at(-1)?.node as string | undefined);
  const waitingHuman = status === "awaiting_human";
  const gateType =
    rawInterrupt?.type ||
    (waitingHuman && currentNode === "briefing" ? "research_brief" : undefined) ||
    (waitingHuman && currentNode === "plan_gate" ? "plan_review" : undefined) ||
    (waitingHuman && currentNode === "memo_gate" ? "memo_draft" : undefined) ||
    (waitingHuman && (currentNode === "hitl" || nextNodes.includes("hitl")) ? "approve_report" : undefined) ||
    (waitingHuman && rawInterrupt?.brief ? "research_brief" : undefined) ||
    (waitingHuman ? "approve_report" : undefined);
  const interrupt = rawInterrupt || (gateType ? { type: gateType, ...(rawInterrupt || {}) } : undefined);
  const report = values?.report;
  const budget = values?.budget || interrupt?.budget;
  const awaitingBrief = waitingHuman && gateType === "research_brief";
  const awaitingPlan = waitingHuman && gateType === "plan_review";
  const awaitingMemo = waitingHuman && gateType === "approve_report";
  const awaitingDraft = waitingHuman && gateType === "memo_draft";
  const failedCredits =
    status === "failed" && isCreditsExhaustedMessage(String(run?.error || run?.agent?.error || error || ""));
  const awaitingCredits =
    (waitingHuman && gateType === "credits_exhausted") || failedCredits;
  // Dig further is allowed until hard caps; the agent grants +1 iteration / +8
  // tool calls on revise even if the first pass already hit the soft budget.
  const canRevise = !budget
    ? true
    : Number(budget.iterations || 0) < 8 && Number(budget.used_tool_calls || 0) < 40;
  const budgetExhaustedForDisplay = Boolean(
    budget &&
      (Number(budget.iterations || 0) >= Number(budget.max_iterations || 0) ||
        Number(budget.used_tool_calls || 0) >= Number(budget.max_tool_calls || 0)),
  );
  const done = status === "completed" && report;
  const running = Boolean(runId && status && !awaitingBrief && !awaitingPlan && !awaitingMemo && !awaitingDraft && !done && status !== "failed");
  const critic = (done ? values?.critic : interrupt?.critic) || values?.critic || {};
  const claims: any[] = done
    ? report.claims || []
    : !awaitingBrief && !awaitingPlan
      ? interrupt?.claims_preview || values?.claims || []
      : [];
  const evidence: any[] = done
    ? report.evidence || []
    : !awaitingBrief && !awaitingPlan
      ? interrupt?.evidence_preview || values?.retrieved || values?.evidence || []
      : [];
  const citations: any[] = done ? report.citations || [] : [];
  const bodyMd = done ? report.body_markdown || "" : "";
  const metrics = done ? report.metrics || {} : {};
  const evidenceGraph = (run as Run | null)?.evidence_graph || metrics.evidence_graph;
  const diagnosticsMd = done ? (metrics.diagnostics_markdown as string) || "" : "";
  type CoverageSlot = { id?: string; label?: string; status?: string };
  const coverageSlots: CoverageSlot[] = done
    ? (metrics.must_answer as CoverageSlot[]) || (critic.coverage?.slots as CoverageSlot[]) || []
    : [];
  const gateReason =
    interrupt?.gate_reason ||
    interrupt?.coverage_gate?.gate_reason ||
    interrupt?.critic?.gate_reason ||
    interrupt?.critic?.coverage_gate?.gate_reason ||
    critic?.gate_reason ||
    critic?.coverage_gate?.gate_reason ||
    metrics?.gate_reason;
  const gateMessage =
    interrupt?.gate_message ||
    interrupt?.coverage_gate?.message ||
    critic?.coverage_gate?.message ||
    metrics?.coverage_gate?.message ||
    "";
  const coverageSlotsAtGate: CoverageSlot[] = done
    ? coverageSlots
    : (interrupt?.coverage_slots as CoverageSlot[]) ||
      (interrupt?.critic?.coverage?.slots as CoverageSlot[]) ||
      (critic.coverage?.slots as CoverageSlot[]) ||
      [];
  const compact = Boolean(runId) && !searchOpen && !awaitingBrief;
  const startedAt = run?.agent?.started_at;
  const elapsed = startedAt ? Math.max(0, tick / 1000 - startedAt) : Number(run?.agent?.elapsed_s || 0);
  const progressPct = Math.round(
    Math.min(100, Math.max(2, (run?.agent?.progress ?? (PIPELINE.indexOf(currentNode || "planner") + 0.35) / PIPELINE.length) * 100)),
  );
  const stepMeta = STEP_COPY[currentNode || "planner"] || STEP_COPY.planner;
  const planSubQueries =
    planDraft.sub_queries.length > 0
      ? planDraft.sub_queries
      : interrupt?.sub_queries || interrupt?.plan?.sub_queries || [];
  const planAgents =
    planDraft.agents_to_run.length > 0
      ? planDraft.agents_to_run
      : interrupt?.agents_to_run || interrupt?.plan?.agents_to_run || [];

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

  const hostedAny = useMemo(
    () => Object.values(platformProviders).some(Boolean),
    [platformProviders],
  );
  const submitCheck = useMemo(
    () => canSubmitResearch(byok, { creditsForced, byokRequired, hostedAny }),
    [byok, creditsForced, byokRequired, hostedAny],
  );
  const submitReady = submitCheck.ok;
  const creditsReady = useMemo(
    () => byokReady(byok, true, { byokRequired: true, hostedAny }),
    [byok, hostedAny],
  );

  async function startResearch(q: string, fresh = false, parentRunId?: string) {
    const check = canSubmitResearch(byok, { creditsForced, byokRequired, hostedAny });
    if (!check.ok) {
      setError(check.reason || "Fix model settings before running.");
      setSearchOpen(true);
      return;
    }
    setError("");
    setBusy(true);
    setRun(null);
    setFilter("all");
    setBriefDraft({});
    notifiedStatusRef.current = "";
    try {
      await requestNotificationPermission();
      const data = await endpoints.startResearch(q, fresh, llmPayload(byok), parentRunId);
      setRunId(data.id);
      setSearchOpen(false);
      setShowFirstRun(false);
      dismissFirstRun();
      window.history.replaceState({}, "", `/?run=${data.id}`);
    } catch (err: any) {
      setError(err.message || String(err));
      if (isCreditsExhaustedMessage(String(err.message || ""))) setCreditsForced(true);
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    await startResearch(query);
  }

  async function resume(action: string, extra?: { brief?: Record<string, any>; plan?: Record<string, any> }) {
    if (!runId) return;
    setBusy(true);
    setError("");
    // Optimistic: leave the brief gate immediately so polling cannot flash it back
    // while the resume job is still clearing the LangGraph interrupt.
    if (action === "start" || action === "approve" || action === "revise" || action === "publish" || action === "revise_critic") {
      setRun((prev) =>
        prev
          ? {
              ...prev,
              status: "queued",
              interrupt_payload: undefined,
              agent: prev.agent
                ? { ...prev.agent, status: "queued", interrupt: undefined }
                : prev.agent,
            }
          : prev,
      );
    }
    try {
      await endpoints.resume(runId, {
        action,
        notes,
        extra_questions: notes ? [notes] : [],
        brief: extra?.brief || briefDraft,
        plan: extra?.plan || planDraft,
        llm: llmPayload(byok),
      });
    } catch (err: any) {
      setError(err.message || "Resume failed");
      if (isCreditsExhaustedMessage(String(err.message || ""))) setCreditsForced(true);
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
      const data = await endpoints.duplicate(runId, llmPayload(byok));
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
    downloadExportPack({
      title: report?.title || "Kiln research memo",
      query,
      bodyMarkdown: reportMarkdown(),
      decisionRule: report?.decision_rule,
      citations,
      claims,
      metrics,
      runId: runId || undefined,
    });
    void endpoints.track("report_exported", { kind: "md_pack", runId });
  }

  async function exportNotion() {
    await copyForNotion({
      title: report?.title || "Kiln research memo",
      query,
      bodyMarkdown: reportMarkdown(),
      decisionRule: report?.decision_rule,
      citations,
      claims,
      metrics,
      runId: runId || undefined,
    });
    setShareMsg("Copied for Notion — paste into a page");
    setTimeout(() => setShareMsg(""), 2500);
    void endpoints.track("report_exported", { kind: "notion_copy", runId });
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
    const text = followup.trim();
    if (!runId || text.length < 8) return;
    setBusy(true);
    setError("");
    try {
      await requestNotificationPermission();
      const data = await endpoints.startResearch(text, false, llmPayload(byok), runId);
      setRunId(data.id);
      setSearchOpen(false);
      notifiedStatusRef.current = "";
      window.history.replaceState({}, "", `/?run=${data.id}`);
      setFollowup("");
    } catch (err: any) {
      setError(err.message || "Follow-up failed");
    } finally {
      setBusy(false);
    }
  }

  function openThreadRun(id: string) {
    setRunId(id);
    window.history.replaceState({}, "", `/?run=${id}`);
    setSearchOpen(false);
    setHistoryOpen(false);
  }

  function openCite(n: number) {
    setCiteOpen(n);
    if (readerPrefs.readingMode) {
      /* drawer sits beside memo */
    }
  }

  function jumpToc(id: string) {
    const el = document.getElementById(id);
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const activeCitation = useMemo(
    () => (citeOpen != null ? citations.find((c: any) => Number(c.n) === citeOpen) : null),
    [citeOpen, citations],
  );
  const relatedClaimsForCite = useMemo(() => {
    if (citeOpen == null) return [];
    return claims.filter((c: any) => {
      const blob = `${c.text || ""} ${c.quote || ""} ${c.url || ""}`;
      return blob.includes(`[${citeOpen}]`) || (activeCitation?.url && c.url === activeCitation.url);
    }).slice(0, 4);
  }, [citeOpen, claims, activeCitation]);

  const tocSource = useMemo(
    () => (bodyMd ? prepMemoMarkdown(bodyMd, { stripTitle: report?.title || undefined }) : ""),
    [bodyMd, report?.title],
  );

  function stepState(step: any) {
    const ev = step.event;
    if (step.id === "critic" && (ev?.status === "contradicted" || critic.status === "contradicted")) return "warn";
    if (awaitingBrief && step.id === "briefing") return "now";
    if (awaitingPlan && step.id === "plan_gate") return "now";
    if (awaitingMemo && step.id === "hitl") return "now";
    if (awaitingDraft && step.id === "memo_gate") return "now";
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
          {!runId && showFirstRun && (
            <FirstRunBanner
              hostedAny={hostedAny}
              byokRequired={byokRequired}
              onPick={(text) => setQuery(text)}
              onDismiss={() => {
                dismissFirstRun();
                setShowFirstRun(false);
              }}
            />
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
            <ByokPanel
              value={byok}
              onChange={setByok}
              creditsForced={creditsForced}
              byokRequired={byokRequired}
              platformProviders={platformProviders}
              submitBlockedReason={!submitReady ? submitCheck.reason : undefined}
            />
            <div className="search-actions">
              {runId && (
                <button className="btn" type="button" onClick={() => setSearchOpen(false)}>
                  Collapse
                </button>
              )}
              <button
                className={`btn primary${busy ? " is-busy" : ""}`}
                type="submit"
                disabled={busy || query.trim().length < 8 || !submitReady}
                title={
                  busy
                    ? "Starting the run"
                    : query.trim().length < 8
                      ? "Type at least 8 characters, or pick an example below"
                      : !submitReady
                        ? submitCheck.reason || "Fix model settings before running"
                        : "Start research"
                }
              >
                {busy ? "Starting…" : "Research"}
              </button>
            </div>
          </form>
          {!runId && (
            <div className="suggest">
              {FIRST_RUN_TEMPLATES.map((ex) => (
                <button type="button" className="badge" key={ex.text} onClick={() => setQuery(ex.text)}>
                  {ex.tag}
                </button>
              ))}
              {examples
                .filter((ex) => !FIRST_RUN_TEMPLATES.some((t) => t.text === ex.text))
                .map((ex) => (
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
      {pinToast && (
        <p className="idle" aria-live="polite">
          {pinToast}
        </p>
      )}
      {shareMsg && (
        <p className="idle" aria-live="polite">
          {shareMsg}
        </p>
      )}

      {runId && (
        <div
          className={[
            "research-layout",
            railCollapsed || readerPrefs.readingMode ? "rail-collapsed" : "",
            readerPrefs.readingMode ? "reading-mode" : "",
            historyOpen ? "history-open" : "",
          ]
            .filter(Boolean)
            .join(" ")}
          style={
            {
              ["--memo-font-scale" as string]: String(readerPrefs.fontScale),
              ["--memo-measure" as string]:
                readerPrefs.measure === "narrow"
                  ? "44rem"
                  : readerPrefs.measure === "wide"
                    ? "min(88rem, 100%)"
                    : "min(68rem, 100%)",
            } as CSSProperties
          }
        >
          <aside className="agent-rail" aria-label="Agent pipeline">
            <div className="agent-rail-head">
              <button
                type="button"
                className="rail-toggle"
                aria-expanded={!railCollapsed && !readerPrefs.readingMode}
                aria-label={railCollapsed ? "Expand pipeline" : "Collapse pipeline"}
                title={railCollapsed ? "Expand pipeline" : "Collapse pipeline"}
                onClick={() => {
                  if (readerPrefs.readingMode) {
                    setReaderPrefs((p) => {
                      const next = { ...p, readingMode: false };
                      try {
                        localStorage.setItem("kiln_reader_prefs", JSON.stringify(next));
                      } catch {
                        /* ignore */
                      }
                      return next;
                    });
                    setRailCollapsed(false);
                    try {
                      localStorage.setItem("kiln_rail_collapsed", "0");
                    } catch {
                      /* ignore */
                    }
                    return;
                  }
                  setRailCollapsed((v) => {
                    const next = !v;
                    try {
                      localStorage.setItem("kiln_rail_collapsed", next ? "1" : "0");
                    } catch {
                      /* ignore */
                    }
                    return next;
                  });
                }}
              >
                <span className="rail-toggle-icon" aria-hidden>
                  {railCollapsed || readerPrefs.readingMode ? "»" : "«"}
                </span>
              </button>
              <h3 className="rail-title">Pipeline</h3>
            </div>
            <button
              className="btn compact history-toggle rail-only-expanded"
              type="button"
              onClick={() => setHistoryOpen((v) => !v)}
            >
              {historyOpen ? "Hide history" : "Run history"}
            </button>
            <div className="agent-rail-steps">
              {steps.map((step) => {
                const state = stepState(step);
                const ev = step.event;
                return (
                  <button
                    key={step.id}
                    className={`step ${state === "now" || state === "warn" ? "active" : ""}`}
                    type="button"
                    title={step.title}
                    onClick={() => setInspect(step)}
                  >
                    <span className={`ico ${state}`}>
                      {state === "done" ? "✓" : state === "skip" ? "–" : state === "warn" ? "!" : state === "now" ? "●" : "○"}
                    </span>
                    <span className="step-copy">
                      <div className="label">
                        {step.title}
                        {typeof ev?.n === "number" ? ` (${ev.n})` : ""}
                        {ev?.skipped ? " · skip" : ""}
                        {ev?.status === "contradicted" ? " · contradicted" : ""}
                      </div>
                      <div className="sub">{step.hint}</div>
                    </span>
                  </button>
                );
              })}
            </div>
            {budget && (
              <div className="metrics rail-only-expanded">
                <div>
                  {budget.iterations}/{budget.max_iterations} passes · {budget.used_tool_calls}/{budget.max_tool_calls}{" "}
                  calls
                  {budget.used_tokens ? ` · ${budget.used_tokens} tok` : ""}
                </div>
                {running && (
                  <div className="metrics-live">
                    {formatElapsed(elapsed)} elapsed · step {(PIPELINE.indexOf(currentNode || "planner") + 1) || 2}/
                    {PIPELINE.length}
                  </div>
                )}
              </div>
            )}
          </aside>

          {historyOpen && !readerPrefs.readingMode && (
            <RunHistoryPanel
              currentRunId={runId || undefined}
              currentQuery={query}
              onOpenRun={(id) => {
                setRunId(id);
                window.history.replaceState({}, "", `/?run=${id}`);
                setHistoryOpen(false);
                setSearchOpen(false);
              }}
            />
          )}

          <main className="research-stage">
            <ResearchThread
              parent={run?.thread?.parent}
              children={run?.thread?.children}
              currentRunId={runId || undefined}
              onOpen={openThreadRun}
            />
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
                  <button
                    className="btn primary"
                    type="button"
                    onClick={() => resume("start", { brief: briefDraft })}
                    disabled={busy || !submitReady}
                    title={
                      busy
                        ? "Starting…"
                        : !submitReady
                          ? creditsForced
                            ? "Paste a key above and tick the billing checkbox"
                            : "Fix provider keys above"
                          : "Start deep research"
                    }
                  >
                    Start research
                  </button>
                  <button className="btn" type="button" onClick={() => resume("cancel")} disabled={busy}>Cancel</button>
                </div>
                {!submitReady && (
                  <p className="err" role="alert">
                    Hosted credits ran out. Scroll up, paste at least one API key, tick the checkbox, then Start research.
                  </p>
                )}
                {submitReady &&
                  !byok.acknowledged &&
                  Object.values(byok.keys).some((k) => k.trim().length > 0) && (
                  <p className="idle" aria-live="polite">
                    You typed a key but did not tick the billing checkbox — Start will use hosted keys only. Tick the box above to use your key.
                  </p>
                )}
              </section>
            )}

            {awaitingPlan && (
              <section className="panel brief-panel plan-panel">
                <div className="summary-head">
                  <div>
                    <h2>{interrupt.title || "Agent plan"}</h2>
                    <p className="brief-sub">
                      {interrupt.subtitle || "Review sub-queries and agents before Kiln searches."}
                    </p>
                  </div>
                </div>
                <p className="sub">
                  Agents: {(planAgents || []).join(", ") || "search"}
                </p>
                <ol className="plan-subqueries">
                  {(planSubQueries || []).map((sq: any, i: number) => (
                    <li key={i}>
                      <span className="plan-agent">{sq.agent}</span>
                      <textarea
                        className="plan-subquery-input"
                        value={sq.question || ""}
                        rows={Math.min(8, Math.max(3, Math.ceil((sq.question || "").length / 90)))}
                        onChange={(e) => {
                          const base = planDraft.sub_queries.length ? planDraft.sub_queries : planSubQueries;
                          const next = [...base];
                          next[i] = { ...sq, question: e.target.value };
                          setPlanDraft({
                            sub_queries: next,
                            agents_to_run: planAgents,
                          });
                        }}
                      />
                    </li>
                  ))}
                </ol>
                <div className="btn-row brief-actions">
                  <button
                    className="btn primary"
                    type="button"
                    onClick={() => resume("start", { plan: planDraft })}
                    disabled={busy || !submitReady}
                  >
                    Start searching
                  </button>
                  <button className="btn" type="button" onClick={() => resume("cancel")} disabled={busy}>
                    Cancel
                  </button>
                </div>
              </section>
            )}

            {awaitingCredits && (
              <section className="panel review-box">
                <div className="hero-kicker">Credits pause</div>
                <h2>{interrupt?.title || "Model credits exhausted"}</h2>
                <p className="err" role="alert">
                  {interrupt?.message ||
                    run?.error ||
                    run?.agent?.error ||
                    error ||
                    "Every configured provider ran out of credits."}
                </p>
                <p className="sub">
                  {interrupt?.resume_hint ||
                    "Paste a key above (and tick billing), or top up hosted credits — then Continue. Research resumes from this step, not from scratch."}
                </p>
                {Array.isArray(interrupt?.tried) && interrupt.tried.length > 0 && (
                  <p className="idle">Tried: {interrupt.tried.join(", ")}</p>
                )}
                <div className="btn-row">
                  <button
                    className="btn primary"
                    type="button"
                    onClick={() => {
                      setCreditsForced(true);
                      setSearchOpen(true);
                      void resume("continue");
                    }}
                    disabled={busy || !creditsReady}
                    title={
                      !creditsReady
                        ? "Paste a key above and tick the billing checkbox"
                        : "Resume from the paused step"
                    }
                  >
                    Continue from this step
                  </button>
                  {failedCredits ? (
                    <button className="btn" type="button" onClick={retryRun} disabled={busy}>
                      Retry from scratch
                    </button>
                  ) : (
                    <button className="btn" type="button" onClick={() => resume("cancel")} disabled={busy}>
                      Cancel run
                    </button>
                  )}
                </div>
                {!creditsReady && (
                  <p className="err" role="alert">
                    Scroll up, paste at least one API key, tick the checkbox, then Continue.
                  </p>
                )}
              </section>
            )}

            {(done || awaitingMemo || (!awaitingBrief && !awaitingCredits && claims.length > 0)) && (
              <section className="panel memo-panel">
                {query.trim() && (
                  <div className="print-query">
                    <span className="print-query-label">Research question</span>
                    {query.trim()}
                  </div>
                )}
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
                        Export pack
                      </button>
                      <button className="btn" type="button" onClick={() => void exportNotion()}>
                        Copy Notion
                      </button>
                      <button className="btn" type="button" onClick={() => window.print()}>
                        Print PDF
                      </button>
                      <button className="btn" type="button" onClick={shareLink}>
                        Share link
                      </button>
                      <button className="btn" type="button" onClick={retryRun} disabled={busy}>
                        Duplicate
                      </button>
                      <button
                        className="btn"
                        type="button"
                        onClick={() => {
                          if (runId) void endpoints.pin(runId, true).then(() => {
                            setPinToast("Run pinned in Workspace");
                            setTimeout(() => setPinToast(""), 2000);
                          });
                        }}
                      >
                        Pin run
                      </button>
                    </div>
                  )}
                </div>
                {done && (
                  <ReaderToolbar
                    prefs={readerPrefs}
                    onChange={(next) => {
                      setReaderPrefs(next);
                      if (next.readingMode && !readerPrefs.readingMode) {
                        setRailCollapsed(true);
                        try {
                          localStorage.setItem("kiln_rail_collapsed", "1");
                        } catch {
                          /* ignore */
                        }
                      }
                    }}
                  />
                )}
                {done && (
                  <DecisionCard
                    decisionRule={report?.decision_rule}
                    atAGlance={report?.at_a_glance}
                    executiveSummary={
                      report?.executive_summary ||
                      (bodyMd.match(/^##\s+Executive summary\s*\n+([\s\S]*?)(?=\n##\s|\n#\s|$)/i)?.[1] || synthesized)
                    }
                    confidence={metrics.depth_score != null ? Number(metrics.depth_score) : null}
                    confidenceLabel={metrics.depth_label ? String(metrics.depth_label) : undefined}
                    confidenceBreakdown={metrics.confidence_breakdown}
                  />
                )}
                {awaitingMemo && (
                  <div className="review-box">
                    {(gateReason === "insufficient_budget" || gateReason === "insufficient_coverage") && (
                      <div className="coverage-gate-warn" role="alert">
                        <strong>Coverage gate: {gateReason.replace(/_/g, " ")}</strong>
                        <p>{gateMessage || "Open must-answer slots remain — review before approving."}</p>
                      </div>
                    )}
                    {coverageSlotsAtGate.length > 0 && (
                      <div className="coverage-grid coverage-grid-gate">
                        {coverageSlotsAtGate.map((s) => (
                          <span key={s.id || s.label} className={`coverage-chip ${s.status || "open"}`}>
                            {s.status === "covered" ? "✓" : s.status === "weak" ? "⚠" : "✗"} {s.label || s.id}
                          </span>
                        ))}
                      </div>
                    )}
                    <p className="sub">
                      This is the evidence gate (step 11/12), not the memo. Approving here only starts writing
                      the draft — you'll get a second, separate screen to publish or send back once the memo is
                      written. Dig further sends the agent back to search again.
                    </p>
                    <textarea placeholder="Optional follow-up for the next search loop" value={notes} onChange={(e) => setNotes(e.target.value)} />
                    <div className="btn-row">
                      <button className="btn primary" type="button" onClick={() => resume("approve")} disabled={busy || !submitReady}>Approve evidence — start writing</button>
                      <button className="btn" type="button" onClick={() => resume("revise")} disabled={busy || !canRevise || !submitReady}>Dig further</button>
                    </div>
                    {!canRevise && (
                      <p className="sub">
                        Hard research cap reached (8 passes / 40 tool calls). Approve to write the memo.
                      </p>
                    )}
                    {canRevise && budgetExhaustedForDisplay && (
                      <p className="sub">
                        First-pass budget is spent ({budget?.iterations}/{budget?.max_iterations} passes ·{" "}
                        {budget?.used_tool_calls}/{budget?.max_tool_calls} calls). Dig further still works —
                        it unlocks one more search loop.
                      </p>
                    )}
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
                  <div className={`memo-layout${done ? " has-sources" : ""}${citeOpen != null ? " has-drawer" : ""}`}>
                    {done && (
                      <MemoToc markdown={tocSource} onJump={jumpToc} />
                    )}
                    <article className="memo md" ref={memoArticleRef}>
                      <MemoMarkdown
                        citations={citations}
                        stripTitle={report?.title || undefined}
                        onCiteClick={done ? openCite : undefined}
                      >
                        {bodyMd}
                      </MemoMarkdown>
                    </article>
                    {done && !!citations.length && (
                      <SourcesPanel citations={citations} activeN={citeOpen} onSelect={openCite} />
                    )}
                    {done && citeOpen != null && (
                      <EvidenceDrawer
                        open
                        citeN={citeOpen}
                        citation={activeCitation}
                        relatedClaims={relatedClaimsForCite}
                        runId={runId || undefined}
                        query={query}
                        onClose={() => setCiteOpen(null)}
                        onPinned={() => {
                          setPinToast("Pinned — see Run history");
                          setTimeout(() => setPinToast(""), 2000);
                        }}
                      />
                    )}
                  </div>
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
                  <ClaimCard key={c.id || i} claim={c} extra={(evidenceGraph?.claims || []).find((g: any) => g.id === c.id)} />
                ))}
                {done && !!evidenceGraph?.claims?.length && (
                  <EvidenceGraphPanel graph={evidenceGraph} />
                )}
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
                        <MemoMarkdown className="md diagnostics-md">{diagnosticsMd}</MemoMarkdown>
                      </article>
                    ) : null}
                    {!!claims.length && (
                      <div className="diagnostics-claims">
                        <h4>Claim ledger</h4>
                        {claims.map((c, i) => (
                          <ClaimCard key={c.id || i} claim={c} extra={(evidenceGraph?.claims || []).find((g: any) => g.id === c.id)} />
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
                  <div className="review-box followup-box">
                    <p className="sub">Ask a follow-up in this thread — Kiln links it to the memo above and keeps context.</p>
                    <textarea placeholder="Ask a follow-up on this memo (at least 8 characters)" value={followup} onChange={(e) => setFollowup(e.target.value)} />
                    <button className="btn primary" type="button" onClick={askFollowup} disabled={busy || followup.trim().length < 8 || !submitReady}>
                      Ask follow-up
                    </button>
                  </div>
                )}
              </section>
            )}

            {!awaitingBrief && !awaitingPlan && !awaitingMemo && !awaitingCredits && !done && status && status !== "queued" && (
              <section className="panel progress-panel">
                <div className="progress-head">
                  <span className="pulse" />
                  <div>
                    <h2>{writingReport ? "report" : stepMeta.title}</h2>
                    <p>{writingReport ? "Writing the long memo" : run?.agent?.hint || stepMeta.hint}</p>
                  </div>
                  <time>{formatElapsed(elapsed)}</time>
                </div>
                <div className="progress-track" aria-label={`Progress ${progressPct} percent`}>
                  <div className="progress-fill" style={{ width: `${progressPct}%` }} />
                </div>
                <div className="progress-meta">
                  Step {Math.max(1, PIPELINE.indexOf(currentNode || "planner") + 1)}/{PIPELINE.length}
                  {" · "}
                  {writingReport ? "Usually 1–3 minutes" : stepMeta.wait}
                  {" · "}
                  {writingReport
                    ? "Approve already happened — Kiln is writing the memo now"
                    : "whole run is often several minutes after you confirm the brief"}
                </div>
                {status === "failed" ? (
                  <div>
                    <p className="err">{run?.error || run?.agent?.error || "This run failed."}</p>
                    <div className="btn-row">
                      {isCreditsExhaustedMessage(String(run?.error || run?.agent?.error || "")) && (
                        <button
                          className="btn primary"
                          type="button"
                          onClick={() => {
                            setCreditsForced(true);
                            setSearchOpen(true);
                            void resume("continue");
                          }}
                          disabled={busy || !creditsReady}
                          title={
                            !creditsReady
                              ? "Paste a key above and tick the billing checkbox first"
                              : "Resume from the paused checkpoint"
                          }
                        >
                          Continue from this step
                        </button>
                      )}
                      <button className="btn" type="button" onClick={retryRun} disabled={busy}>
                        Retry from scratch
                      </button>
                    </div>
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
                {researchTrace?.active_agent && (
                  <div className="research-trace" aria-live="polite">
                    <div className="research-trace-head">Live trace</div>
                    <p>
                      <strong>{researchTrace.active_agent}</strong>
                      {researchTrace.source_tier ? ` · ${researchTrace.source_tier}` : ""}
                    </p>
                    {researchTrace.active_sub_query && (
                      <p className="sub trace-query">{researchTrace.active_sub_query}</p>
                    )}
                  </div>
                )}
              </section>
            )}
            {awaitingDraft && (
              <section className="panel review-box draft-panel">
                <div className="hero-kicker">Final step — memo draft</div>
                <h2>{interrupt?.title || "Review before publishing"}</h2>
                {(gateReason === "insufficient_budget" ||
                  interrupt?.synthesis_status === "terminal_fallback") && (
                  <div className="coverage-gate-warn" role="alert">
                    <strong>Budget-limited memo</strong>
                    <p>
                      {gateMessage ||
                        "Some must-answer dimensions were not fully verified before the research budget was exhausted."}
                    </p>
                  </div>
                )}
                {coverageSlotsAtGate.length > 0 && (
                  <div className="coverage-grid coverage-grid-gate">
                    {coverageSlotsAtGate.map((s) => (
                      <span key={s.id || s.label} className={`coverage-chip ${s.status || "open"}`}>
                        {s.status === "covered" ? "✓" : s.status === "weak" ? "⚠" : "✗"} {s.label || s.id}
                      </span>
                    ))}
                  </div>
                )}
                <p className="sub">
                  {interrupt?.subtitle || "Publish as-is or send back to the critic for another evidence pass."}
                </p>
                {(interrupt?.report?.executive_summary || values?.report?.executive_summary) && (
                  <article className="memo md draft-preview">
                    <MemoMarkdown citations={[]}>
                      {interrupt?.report?.body_markdown?.slice(0, 4000) ||
                        values?.report?.body_markdown?.slice(0, 4000) ||
                        interrupt?.report?.executive_summary ||
                        values?.report?.executive_summary}
                    </MemoMarkdown>
                  </article>
                )}
                <textarea
                  placeholder="Optional notes if sending back to critic"
                  value={draftNotes}
                  onChange={(e) => setDraftNotes(e.target.value)}
                />
                <div className="btn-row">
                  <button className="btn primary" type="button" onClick={() => resume("publish")} disabled={busy}>
                    Publish memo
                  </button>
                  <button
                    className="btn"
                    type="button"
                    onClick={() => {
                      setNotes(draftNotes);
                      void resume("revise_critic");
                    }}
                    disabled={busy}
                  >
                    Send back to critic
                  </button>
                </div>
              </section>
            )}
            {status === "queued" && (
              <p className="idle"><span className="pulse" /> Queued — starting the agent…</p>
            )}

            {!!evidence.length && !awaitingBrief && !awaitingPlan && !awaitingCredits && !done && (
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
                    <MemoMarkdown>{e.title || ""}</MemoMarkdown>
                    {e.snippet && (
                      <MemoMarkdown>{String(e.snippet).slice(0, 420)}</MemoMarkdown>
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
