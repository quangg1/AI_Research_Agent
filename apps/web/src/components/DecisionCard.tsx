/** Compact decision strip — prose only, no ASCII / no hard truncation. */

export function DecisionCard({
  decisionRule,
  confidence,
  confidenceLabel,
  confidenceBreakdown,
  flipCondition,
  executiveSummary,
  atAGlance,
}: {
  decisionRule?: string;
  confidence?: number | null;
  confidenceLabel?: string;
  confidenceBreakdown?: Record<string, unknown> | null;
  flipCondition?: string;
  executiveSummary?: string;
  atAGlance?: string;
}) {
  const recommendation =
    (atAGlance && atAGlance.trim()) || pickRecommendation(executiveSummary, decisionRule);
  if (!recommendation && confidence == null) return null;

  const flip =
    (flipCondition && !looksLikeDiagram(flipCondition) && flipCondition.trim()) ||
    extractFlip(decisionRule || "") ||
    "New measured evidence undercuts the lead recommendation, or your latency/cost envelope forbids the gated path.";

  const breakdown = confidenceBreakdown as {
    must_answer_pct?: number;
    critical_pct?: number;
    primary_sources_pct?: number;
  } | null;

  return (
    <section className="decision-card" aria-label="Decision summary">
      <div className="decision-kicker">At a glance</div>
      <p className="decision-rec">{recommendation}</p>
      <div className="decision-meta">
        <div className="decision-confidence">
          <span className="decision-label">Confidence</span>
          <strong>
            {confidence != null ? `${confidence}/100` : "—"}
            {confidenceLabel ? ` · ${confidenceLabel}` : ""}
          </strong>
          {breakdown && (breakdown.must_answer_pct != null || breakdown.primary_sources_pct != null) && (
            <p className="decision-breakdown">
              Must-answer {breakdown.must_answer_pct ?? "—"}% · Primary sources{" "}
              {breakdown.primary_sources_pct ?? "—"}%
              {breakdown.critical_pct != null ? ` · Critical ${breakdown.critical_pct}%` : ""}
            </p>
          )}
        </div>
        <div className="decision-revisit">
          <span className="decision-label">Revisit if</span>
          <strong>{flip}</strong>
        </div>
      </div>
    </section>
  );
}

function pickRecommendation(exec?: string, rule?: string): string {
  const fromExec = firstSentences(cleanProse(exec || ""), 3);
  if (fromExec && fromExec.length > 40) return fromExec;

  const fromRule = cleanProse(extractRecommendation(rule || ""));
  if (fromRule && !looksLikeDiagram(fromRule) && fromRule.length > 24) return fromRule;

  return fromExec || "";
}

function extractRecommendation(rule: string): string {
  const withoutFences = rule
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/^\s{0,3}#{1,3}\s+.+$/gm, " ");
  const bullets = [...withoutFences.matchAll(/^[-*]\s+\*?\*?([^*\n][^\n]+)/gm)].map((m) =>
    m[1].replace(/\*\*/g, "").trim(),
  );
  const useful = bullets.find((b) => !looksLikeDiagram(b) && !/^\|/.test(b));
  if (useful) return firstSentences(useful, 2) || useful;
  return firstSentences(withoutFences, 2);
}

function extractFlip(rule: string): string {
  const cleaned = rule.replace(/```[\s\S]*?```/g, " ");
  const m =
    /(?:flip|unless|except|reconsider|invalidate|revisit)[:\s]+([^\n]+)/i.exec(cleaned) ||
    /###\s*Engineering heuristics[\s\S]*?\n[-*]\s+([^\n]+)/i.exec(cleaned);
  const hit = (m?.[1] || "").replace(/\*\*/g, "").trim();
  if (!hit || looksLikeDiagram(hit)) return "";
  return firstSentences(hit, 2) || hit;
}

function cleanProse(s: string): string {
  return s
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/\|/g, " ")
    .replace(/[▼▲►◄─│┌┐└┘├┤┬┴┼]+/g, " ")
    .replace(/\[(?:DIRECT|INFERRED|DERIVED|RECOMMENDATION|SPECULATIVE)\]\s*/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function looksLikeDiagram(s: string): boolean {
  const t = s || "";
  if ((t.match(/\|/g) || []).length >= 2) return true;
  if (/[▼▲─│]/.test(t)) return true;
  if (/Is Query Domain|Entropy\s*<|Volatile\s*\/\s*Dynamic/i.test(t)) return true;
  if (t.length > 80 && !/[.!?]/.test(t) && /\[.+\]/.test(t)) return true;
  return false;
}

function firstSentences(s: string, n: number): string {
  const parts = cleanProse(s)
    .split(/(?<=[.!?])\s+/)
    .map((p) => p.trim())
    .filter(Boolean);
  return parts.slice(0, n).join(" ");
}
