type RunRow = {
  status?: string;
  result_json?: Record<string, unknown> | null;
  interrupt_payload?: unknown;
  [key: string]: unknown;
};

export function presentRun<T extends RunRow>(row: T, extra: Record<string, unknown> = {}) {
  const snapshot =
    row.result_json && typeof row.result_json === "object" ? { ...row.result_json } : {};
  const waiting = row.status === "awaiting_human" || row.status === "awaiting_brief";
  const persisted = row.interrupt_payload;
  const interruptPeek = (persisted ?? snapshot.interrupt) as { type?: string } | null | undefined;
  const GATE_TYPES = new Set(["research_brief", "plan_review", "memo_draft", "approve_report", "credits_exhausted"]);
  const GATE_NODES = new Set(["hitl", "briefing", "plan_gate", "memo_gate"]);
  const gateNode = GATE_NODES.has(String(snapshot.current_node || ""));
  const gateType = GATE_TYPES.has(String(interruptPeek?.type || ""));
  const creditsGate = interruptPeek?.type === "credits_exhausted";
  // Resurrect parked gates when heartbeat left status=running but interrupt_payload persists.
  const showWaiting =
    waiting || (row.status === "running" && Boolean(persisted) && (gateNode || gateType || creditsGate));
  if (showWaiting) {
    const interrupt = persisted ?? snapshot.interrupt;
    if (interrupt) snapshot.interrupt = interrupt;
    snapshot.status = "awaiting_human";
  } else {
    delete snapshot.interrupt;
    if (row.status) snapshot.status = row.status;
  }
  if (row.error && !snapshot.error) snapshot.error = row.error;
  const graph = evidenceGraphFrom(snapshot, row);
  return {
    ...row,
    ...extra,
    status: showWaiting
      ? "awaiting_human"
      : (row.status as string | undefined) || (snapshot.status as string | undefined),
    agent: Object.keys(snapshot).length ? snapshot : undefined,
    evidence_graph: graph,
  };
}

function evidenceGraphFrom(snapshot: Record<string, unknown>, row: RunRow) {
  const values = snapshot.values;
  if (values && typeof values === "object") {
    const report = (values as { report?: { metrics?: { evidence_graph?: unknown } } }).report;
    if (report?.metrics?.evidence_graph) return report.metrics.evidence_graph;
  }
  const metrics = row.metrics_json;
  if (metrics && typeof metrics === "object" && "evidence_graph" in metrics) {
    return (metrics as { evidence_graph?: unknown }).evidence_graph;
  }
  return undefined;
}
