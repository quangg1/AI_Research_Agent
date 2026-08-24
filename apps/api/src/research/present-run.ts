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
  const gateNode = snapshot.current_node === "hitl" || snapshot.current_node === "briefing";
  const creditsGate = interruptPeek?.type === "credits_exhausted";
  // Only resurrect a gate when the row is still waiting, or a heartbeat flipped a
  // true parked HITL/credits pause to "running" while interrupt_payload is still set.
  // After Start/Approve/Continue, resume clears interrupt_payload — never revive from snapshot alone.
  const showWaiting =
    waiting || (row.status === "running" && Boolean(persisted) && (gateNode || creditsGate));
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
