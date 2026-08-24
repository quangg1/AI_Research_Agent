export type ExportPackInput = {
  title: string;
  query: string;
  bodyMarkdown: string;
  decisionRule?: string;
  citations: Array<{
    n?: number;
    title?: string;
    url?: string;
    quote?: string;
    tier?: string;
    host?: string;
  }>;
  claims?: Array<{ id?: string; text?: string; confidence?: number; url?: string }>;
  metrics?: Record<string, unknown>;
  runId?: string;
};

export function buildExportPack(input: ExportPackInput) {
  const cites = (input.citations || [])
    .map((c) => {
      const n = c.n ?? "?";
      const title = c.title || c.url || "source";
      const url = c.url || "";
      const quote = c.quote ? `\n> ${c.quote}` : "";
      return `[${n}] ${title}${url ? `\n${url}` : ""}${quote}`;
    })
    .join("\n\n");

  const claimLines = (input.claims || [])
    .map((c, i) => {
      const pct =
        c.confidence != null ? ` (${Math.round(Number(c.confidence) * 100)}%)` : "";
      return `${i + 1}. ${c.text || ""}${pct}${c.url ? `\n   ${c.url}` : ""}`;
    })
    .join("\n");

  const meta = [
    `title: ${input.title}`,
    `query: ${input.query}`,
    input.runId ? `run_id: ${input.runId}` : "",
    input.metrics?.knowledge_version != null
      ? `memo_version: ${input.metrics.knowledge_version}`
      : "",
    input.metrics?.depth_score != null ? `confidence: ${input.metrics.depth_score}` : "",
  ]
    .filter(Boolean)
    .join("\n");

  const md = [
    "---",
    meta,
    "---",
    "",
    `# ${input.title}`,
    "",
    input.decisionRule ? `## Decision\n\n${input.decisionRule}\n` : "",
    input.bodyMarkdown.trim(),
    "",
    "## Citation ledger",
    "",
    cites || "_No citations._",
    "",
    claimLines ? `## Claims\n\n${claimLines}\n` : "",
  ].join("\n");

  const ledger = {
    title: input.title,
    query: input.query,
    runId: input.runId,
    metrics: input.metrics || {},
    citations: input.citations,
    claims: input.claims || [],
    exportedAt: new Date().toISOString(),
  };

  return { markdown: md, ledger };
}

export function downloadText(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function downloadExportPack(input: ExportPackInput) {
  const { markdown, ledger } = buildExportPack(input);
  const base = (input.title || "kiln-memo")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 48) || "kiln-memo";
  downloadText(`${base}.md`, markdown, "text/markdown;charset=utf-8");
  downloadText(`${base}-ledger.json`, JSON.stringify(ledger, null, 2), "application/json");
}

export async function copyForNotion(input: ExportPackInput) {
  const { markdown } = buildExportPack(input);
  await navigator.clipboard.writeText(markdown);
}
