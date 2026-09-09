"""ODR-style clean-then-write for Kiln memos.

Aligned with Open Deep Research / modern deep-research report norms:
answer-first Key findings, analytical Detailed analysis, measured-only
Quantitative findings, a Worked example when evidence supports it, and a
single Contradictions home — not a padded academic template.
"""

from __future__ import annotations

import re

WORD_TARGET = {"quick": 900, "standard": 2400, "deep": 5500}
REPORT_MAX_TOKENS = {"quick": 6144, "standard": 12000, "deep": 24000}
# Depth-aware note budgets (deep keeps more operational detail for the writer).
# "deep" skips the LLM compress pass (SKIP_LLM_COMPRESS_DEPTHS below), so this
# ceiling *is* what the writer sees. With merge_unique_evidence now keeping
# enriched full_text instead of dropping it, quotes run closer to the
# QUOTE_CHARS cap (2800/item) more often, so 8 dims x ~16 items could already
# exceed the old 64k ceiling on its own — later (still-relevant) dimensions
# were getting silently truncated off the end, not because there was too
# little evidence but because there was too little room for it. The model
# (gemini-3.6-flash) has a context window far above this either way, so
# raising it costs more input tokens on the one already-budgeted writer call,
# not an extra Gemini request.
NOTES_MAX_CHARS = {"quick": 20_000, "standard": 40_000, "deep": 140_000}
ITEMS_PER_DIMENSION = {"quick": 4, "standard": 10, "deep": 16}
QUOTE_CHARS = {"quick": 700, "standard": 1400, "deep": 2800}
COMPRESS_MAX_TOKENS = {"quick": 6144, "standard": 8192, "deep": 12288}
# Deep research: skip the LLM clean pass — raw dossier notes go to the writer.
SKIP_LLM_COMPRESS_DEPTHS = frozenset({"deep"})


def _depth(depth: str) -> str:
    d = (depth or "standard").strip().lower()
    return d if d in WORD_TARGET else "standard"


def word_target(depth: str) -> int:
    return WORD_TARGET[_depth(depth)]


def report_max_tokens(depth: str) -> int:
    return REPORT_MAX_TOKENS[_depth(depth)]


def notes_max_chars(depth: str = "standard") -> int:
    return NOTES_MAX_CHARS[_depth(depth)]


def items_per_dimension(depth: str = "standard") -> int:
    return ITEMS_PER_DIMENSION[_depth(depth)]


def quote_chars(depth: str = "standard") -> int:
    return QUOTE_CHARS[_depth(depth)]


def compress_max_tokens(depth: str = "standard") -> int:
    return COMPRESS_MAX_TOKENS[_depth(depth)]


def should_skip_llm_compress(depth: str = "standard") -> bool:
    return _depth(depth) in SKIP_LLM_COMPRESS_DEPTHS


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def filter_dossier_for_writer(dossier: list[dict], *, depth: str = "standard") -> list[dict]:
    """Prefer open gaps first but keep enough covered evidence for a long memo."""
    if (depth or "standard").lower() != "deep":
        return list(dossier or [])
    out: list[dict] = []
    for dim in dossier or []:
        row = dict(dim)
        if row.get("status") == "covered" and not row.get("critical"):
            items = list(row.get("items") or [])
            row["items"] = items[:4]
        out.append(row)
    return out


def narrative_mode_block(*, depth: str) -> str:
    if (depth or "standard").lower() == "quick":
        return ""
    return (
        "Narrative mode (deep research readability):\n"
        "- Concise means no repetition — NOT a short memo. Use the full word budget on NEW evidence.\n"
        "- ## At a glance and ## Executive summary must read as flowing prose for a C-level reader, "
        "not bullet dumps.\n"
        "- Open Executive summary with the direct answer in the first sentence.\n"
        "- Each ### under Detailed analysis: 250–450 words when notes support it — mechanism steps, "
        "named systems, quoted numbers, and at least two distinct [n] sources where available.\n"
    )


def wants_visual_artifacts(query: str, brief: dict | None = None) -> bool:
    """Deprecated — visual/code appendix removed from memo pipeline."""
    return False


def prioritize_dossier_for_writer(dossier: list[dict]) -> list[dict]:
    """Open/weak dimensions first so the writer closes gaps before covered slots."""
    rank = {"open": 0, "weak": 1, "unknown": 2, "covered": 3}
    return sorted(
        list(dossier or []),
        key=lambda d: (rank.get(str(d.get("status") or "unknown").lower(), 2), str(d.get("id") or "")),
    )


def format_research_notes(
    dossier: list[dict],
    citations: list[dict],
    *,
    depth: str = "standard",
    items_per_dimension: int | None = None,
    quote_limit: int | None = None,
) -> str:
    """Dense, citation-preserving notes for the writer. Heuristic; no LLM."""
    from app.domain.adversarial import extract_quantitative_rows

    per_dim = items_per_dimension if items_per_dimension is not None else ITEMS_PER_DIMENSION[_depth(depth)]
    qchars = quote_limit if quote_limit is not None else QUOTE_CHARS[_depth(depth)]
    url_to_n = {c.get("url", "").rstrip("/").lower(): c.get("n") for c in citations if c.get("url")}
    blocks: list[str] = [
        "## Protected operational detail (writer must retain)",
        "- Keep named system mechanisms step-by-step (e.g. PIVOT trajectory loops).",
        "- Keep named runtime/eval metrics with units and harness (e.g. RAMP).",
        "- Keep full enumerated failure-mode inventories (do not collapse '19 modes' to 'several').",
        "- Keep scalability facts (KV-cache, HBM/bandwidth, multi-node) separate from latency.",
        "- Keep concrete worked-example traces when present (token flow of named systems).",
        "",
    ]
    flat: list[dict] = []
    for dim in dossier:
        items = dim.get("items") or []
        if not items and not dim.get("label"):
            continue
        status = dim.get("status") or "unknown"
        blocks.append(f"## {dim.get('id') or 'slot'}: {dim.get('label') or ''} (status: {status})")
        if not items:
            blocks.append("- No collected source covers this dimension. The memo must say so.")
            continue
        for ev in items[:per_dim]:
            flat.append(ev)
            url = (ev.get("url") or "").rstrip("/").lower()
            n = url_to_n.get(url, "?")
            quote = (ev.get("quote") or ev.get("snippet") or ev.get("full_text") or "")[:qchars]
            title = ev.get("title") or url or "untitled"
            tier = ev.get("tier") or ""
            blocks.append(f"- [{n}] {title} ({tier}): {quote}")
    numbers = extract_quantitative_rows(flat, citations)
    blocks.append(
        "## Quantitative fragments (copy into the memo table — outcome metrics only)\n"
        "Table-worthy: %, ms, FLOPs, tok/s, cost/accuracy deltas. "
        "Not table-worthy (mention in prose/Worked example only): N studies, ISL/OSL tokens, N runs. "
        "Do not invent Latency/FLOP/Cost rows when no number is present; list those under Metric gaps."
    )
    if numbers:
        for row in numbers:
            blocks.append(
                f"- [{row['n']}] {row['metric']} — benchmark: {row.get('benchmark_name') or 'unverified'}; "
                f"condition: {row.get('condition') or 'unset'}; "
                f"{row.get('warning') or 'ok'} — {row['title']} ({row.get('year') or 'year?'})"
            )
    else:
        blocks.append(
            "- None extracted from quotes. Leave the Quantitative findings table empty or omit it; "
            "use Metric gaps for asked metrics — do not invent 'not reported' scaffold rows."
        )
    return "\n".join(blocks) if blocks else "(no dimension-grouped evidence)"


def compress_system() -> str:
    return (
        "You CLEAN applied-AI research notes — you do not deeply summarize them. "
        "Deduplicate repeated bullets, fix markdown structure, and group by dimension. "
        "MUST PRESERVE verbatim: named mechanisms and how they work step-by-step, "
        "every metric with units/benchmark/condition, full numbered failure-mode lists, "
        "quotes, years, and every [n] citation. "
        "FORBIDDEN: collapsing '19 failure modes' into 'several modes'; dropping PIVOT/RAMP "
        "(or similar) operational steps; inventing sources; attributing a paper's corpus size to Kiln. "
        "Prefer longer faithful notes over short paraphrase. Markdown only."
    )


def compress_prompt(notes: str, query: str, *, depth: str = "standard") -> str:
    limit = notes_max_chars(depth)
    return (
        f"User question:\n{query}\n\n"
        "Research notes (already cited). CLEAN them — do not deeply summarize.\n"
        "Do not drop sources, quotes, numbers, years, mechanism steps, metric tables, "
        "or enumerated failure modes. Do not invent facts.\n"
        "Keep every [n] citation. Prefer verbatim quotes over paraphrase.\n"
        "Output markdown with these sections (keep content under each; do not empty them):\n"
        "- Sources used (title, year if present, quality band if given)\n"
        "- Findings by dimension, each with supporting quote + [n]\n"
        "- Operational mechanisms (named systems; keep step-by-step behavior)\n"
        "- Quantitative fragments (copy numbers exactly; include benchmark/condition; "
        "if missing write 'not reported')\n"
        "- Failure-mode inventory (keep full enumeration when the notes list N modes)\n"
        "- Counter-evidence / disagreements (required even if thin)\n"
        "- Paper-says vs inference\n"
        "- Temporal cautions (old papers used for current SOTA)\n"
        "- Genuine field unknowns\n\n"
        f"{notes[:limit]}"
    )


def writer_system() -> str:
    return (
        "You are Kiln's research writer for applied AI / LLM systems "
        "(serving, RAG, agents, eval, fine-tune vs retrieval, inference stacks). "
        "Write a modern deep-research memo in the Open Deep Research style: "
        "answer-first, analytical prose, not a literature survey or template dump. "
        "No self-reference, no process narration, no Research plan section, "
        "no internal telemetry, no ASCII art diagrams, no fenced code blocks, no mermaid diagrams. "
        "Stay on the asked question. "
        "Invent nothing. "
        "Hold two competing hypotheses in tension, but argue them ONCE in "
        "'## Contradictions & debates' — never duplicate under Detailed analysis. "
        "Never invent universal numeric laws (e.g. '15% synthetic is always safe') — "
        "thresholds need [n] + source domain + 're-benchmark on your workload'. "
        "Never convert benchmark failures into metaphysical claims "
        "('no autonomy', 'unconstrained problem-solving') unless a cited source uses those words. "
        "Write professional reader prose — NEVER prefix sentences with bracket tags like "
        "[DIRECT], [INFERRED], [DERIVED], [RECOMMENDATION], or [SPECULATIVE]. "
        "Epistemic stance (if needed) goes in natural language once, not as debug metadata. "
        "Attribution: if a cited paper reviewed N artifacts/papers, write "
        "'Based on [n]'s synthesis of N …' — never 'Our/this synthesis across N'. "
        "Quantitative findings table: ONLY outcome / performance measurements "
        "(error/accuracy %, latency ms, FLOPs, tok/s, cost multipliers, deltas vs a baseline). "
        "FORBIDDEN table rows: experiment setup parameters (corpus size 'N studies', "
        "ISL/OSL token lengths, 'N runs', batch size, prompt length) — those belong in "
        "prose or Worked example, not the metrics table. "
        "Never invent scaffold rows filled with 'not reported' / 'Not reported' for "
        "Latency, FLOP, or Cost — list those missing metrics once under "
        "## Uncertainties & gaps (Measurement gaps), not a separate Metric gaps block. "
        "Never invent a before→after causal story from two differently conditioned percentages. "
        "When the question names Scalability separately from Latency/Cost, analyze it in its own "
        "### subsection (KV-cache limits, GPU memory bandwidth, multi-node/cluster behavior, "
        "batching under search/MCTS) — do not bury it inside latency. "
        "Prefer one concrete worked example (token-flow / system walkthrough of named stacks "
        "in the notes, e.g. DeepSeek-R1 vs o1/o3) over abstract taxonomy. "
        "Named systems need operational description, not name-drops. Enumerated failure modes stay enumerated. "
        "Counter-evidence must engage the strongest contradicting source in substance. "
        "Form claims bottom-up from extracted numbers first. "
        "'Verified' / quote-matched ≠ independently measured. "
        "Author estimates stay author_assumption, never High. "
        "GitHub Awesome-lists are Band C. arXiv+OpenReview of the same paper = one work. "
        "Write 42% → 62% as +20 percentage points (relative +47.6%), never '+20% boost'. "
        "Folklore ('bigger models always win', 'RAG always needs a vector DB', "
        "'LLM-as-judge is ground truth') must not be recommended."
    )


def writer_prompt(
    *,
    query: str,
    brief: dict,
    notes: str,
    citations: list[dict],
    min_words: int,
    comparison_rule: str,
    prior_note: str,
    dimension_list: str,
    method_block: str = "",
) -> str:
    depth = str((brief or {}).get("depth") or "standard")
    limit = notes_max_chars(depth)
    ledger = "\n".join(
        f"[{c.get('n')}] {c.get('title') or ''} — {c.get('url') or ''}"
        for c in citations
        if c.get("n")
    )
    method = (method_block or "").strip()
    method_section = f"{method}\n\n" if method else ""
    claim_appendix = (
        "## Appendix: Claim register\n"
        "(deep only — compact table; do not restate Analysis)\n"
        if depth == "deep"
        else ""
    )
    return (
        f"User question:\n{query}\n\n"
        f"Research brief:\n{brief}\n\n"
        f"{prior_note}"
        f"{narrative_mode_block(depth=depth)}"
        f"{method_section}"
        f"Dimensions this answer must cover (each gets a ### under Detailed analysis):\n"
        f"{dimension_list}\n\n"
        f"Research findings (cleaned notes — ground-truth excerpts):\n{notes[:limit]}\n\n"
        f"Citation ledger (ONLY these [n] are legal):\n{ledger}\n\n"
        "Write a reader-facing deep-research memo that ANSWERS the question.\n"
        f"Target length: at least {min_words} words of substantive prose. "
        "Concise ≠ short: avoid repeating the same thesis, but DO use the full budget to surface "
        "every distinct fact, metric, and named study from the notes.\n"
        "Anti-redundancy (critical):\n"
        "- Say each load-bearing argument and each % / ms / FLOP figure ONCE. "
        "Elsewhere use one cross-reference ('see Contradictions & debates').\n"
        "- FORBIDDEN inside ## Detailed analysis ### subsections: "
        "Contradictions, Counter-evidence, Open questions, or #### Counter-evidence blocks. "
        "Those live ONLY in ## Contradictions & debates.\n"
        "- Do NOT duplicate missing-metric lists: one home under ## Uncertainties & gaps.\n"
        "- The core architectural thesis appears ONCE (Executive summary OR one ### under Analysis). "
        "Key findings = distinct claims; Contradictions = H1/H2 tension; Decision rule = cutoffs only.\n"
        "- Do NOT include ## Research plan, ## Scope as a long list, ## Findings "
        "(use Key findings), or ## Competing hypotheses as a second H1/H2 dump.\n"
        "- Detailed analysis must be ANALYTICAL (conclusion → evidence → nuance). "
        "Each ### must add facts NOT already stated in Executive summary / Key findings. "
        "No Evidence/Counter-evidence/Inference stencil per subsection.\n"
        "- Every ### heading is a noun phrase naming the dimension (e.g. '### Verification mechanisms'). "
        "NEVER a sentence or transition phrase carried over from the prose before it "
        "(FORBIDDEN: '### Then we address alignment', '### And discuss methodologies for X' — "
        "write that sentence as body text under the PRIOR heading instead of promoting it to a new one).\n"
        "Paper-specific subsection structure (CRITICAL for quality):\n"
        "When a dimension references a specific paper [n], structure that ### subsection as:\n"
        "  1. Technical definition: One sentence defining the method/framework from the paper.\n"
        "  2. Measured findings: Specific metrics WITH conditions AND baselines.\n"
        "     Example: 'Self-Instruct achieves +33 points on SuperNI with filtering [8] vs -7.6 without [1]'\n"
        "     NOT: 'improves performance' or 'shows good results'\n"
        "  3. Mechanism: 2-3 sentences on HOW/WHY it works.\n"
        "  4. Operational constraints: When it applies, when it fails, trade-offs.\n"
        "Always cite the specific paper: [8], [1], [Nature 2024], NOT just [1 peer].\n"
        "Method:\n"
        "- Extract numeric rows BEFORE leaning H1 or H2.\n"
        "- Path: answer → key findings → analysis → measured table → worked example → "
        "contradictions → decision → gaps.\n"
        "- FORBIDDEN in the memo body: [DIRECT], [INFERRED], [DERIVED], [RECOMMENDATION], "
        "[SPECULATIVE] tags — prose only; claim kinds belong in the claim-register extract, not the narrative.\n"
        "- Every factual sentence carries an inline [n] from the ledger (prefer 1–2 cites, not stacks of 3–4).\n"
        "- When a dimension lacks evidence, one honest sentence — no speculation.\n"
        f"- {comparison_rule}"
        "- Exclude critic status, tool-call counts, iteration stats, and quality scores.\n"
        "Required sections, in order (omit unused optional ones entirely):\n"
        "# <title>\n"
        "## At a glance\n"
        "  (Exactly 1–2 sentences: the decision or lean + top caveat. "
        "MUST differ from Executive summary — no copy-paste.)\n"
        "## Executive summary\n"
        "  (3–6 sentences: analytical answer, evidence weight, main tension — no process talk)\n"
        "## Key findings\n"
        "  (4–8 numbered insights ordered by importance; each is a distinct claim + [n], "
        "not a paraphrase of the Executive summary thesis)\n"
        "## Detailed analysis\n"
        "  (### one subsection per dimension above, in that order. "
        "Each ###: analytical prose with mechanism + implication + cited numbers; "
        "250–450 words per subsection when notes allow. "
        "Mine ALL relevant bullets from notes — do not stop after one source per dimension. "
        "If Scalability is listed, keep it separate from Latency/Cost.)\n"
        "## Quantitative findings\n"
        "  Markdown table ONLY for measured outcome metrics found in notes:\n"
        "  Metric | Value | Benchmark | Condition | Baseline | Source [n]\n"
        "  Include: accuracy/error %, latency, FLOPs, throughput, cost deltas.\n"
        "  Exclude: N studies/papers, ISL/OSL token lengths, N runs, batch/prompt size.\n"
        "  Forbidden: padding with Latency/FLOP/Cost rows set to 'not reported'.\n"
        "  Forbidden: qualitative mechanism claims (e.g. 'eliminates hallucinations') — those belong in Analysis.\n"
        "  Do NOT add ### Metric gaps here — missing metrics go to Uncertainties & gaps.\n"
        "## Worked example\n"
        "  (Required for deep / comparisons when notes name ≥2 systems: concrete token-flow or "
        "runtime walkthrough. Setup sizes like ISL/OSL belong here if needed. "
        "CRITICAL: Must be from ONE source [n]. If steps come from different sources, "
        "open with 'Composite — steps drawn from N separate systems not evaluated together' "
        "and cite each step separately. "
        "If notes lack measured numbers, open with 'Illustrative only — no measured run in sources' "
        "and do NOT invent token counts or % thresholds. Every factual claim needs [n].)\n"
        "## Comparison (only if applicable)\n"
        "## Contradictions & debates\n"
        "  (Single home for H1 vs H2 + vendor-vs-independent disagreements; resolve with evidence weight; "
        "do not restate the full gated-pipeline design)\n"
        "## Decision rule\n"
        "  ### Empirical cutoffs (sources only)\n"
        "  ### Engineering heuristics (AI suggestion — not from papers; no full re-architecture dump)\n"
        "## Uncertainties & gaps\n"
        "  (Single home for field unknowns AND missing measurements from sources. "
        "Use ### Measurement gaps for metrics not in notes; do not repeat under Quantitative.)\n"
        "## Limitations\n"
        "  (This run's evidence limits only — not a second gap list)\n"
        "## Source quality\n"
        "  (Max ~5 bullets: ONE bullet per band label; merge ALL sources in that band into one "
        "citation group e.g. [1, 6, 8, 9 peer] — never repeat the same bullet title with different cites.)\n"
        "## References\n"
        f"{claim_appendix}"
        "Decision rule: ### Empirical cutoffs — ONLY thresholds explicitly measured in a cited source "
        "[n], with benchmark/domain named. Append 'Re-benchmark before applying elsewhere.' "
        "If none: write 'Evidence-backed threshold: none.' "
        "### Engineering heuristics — qualitative guidance only; NO numeric % cutoffs unless copied "
        "from a citation with domain scope. FORBIDDEN: universal laws like 'synthetic data must stay "
        "below 15%' without [n] and without naming the paper's experimental regime.\n"
        "Write markdown only. Do not wrap the memo in JSON. "
        "Math: wrap display equations in $$…$$ on their own lines; "
        "inline vars as $b$, $d_{head}$ — never leave an unclosed $ before prose."
    )


def claims_prompt(body: str, citations: list[dict]) -> str:
    ledger = [{"n": c.get("n"), "url": c.get("url"), "title": c.get("title")} for c in citations[:20]]
    return (
        "Extract grounded claims from this memo. JSON only: "
        "{claims: [{id, text, quote, url, tier, support_ids, contradict_ids, confidence, caveats, "
        "kind, published, locator, quality_band, provenance}], "
        "limitations: [str], open_questions: [str], decision_rule: str, title: str, "
        "executive_summary: str, at_a_glance: str}.\n"
        "kind is direct | derived | inferred | recommendation | speculative. "
        "provenance is measured | author_assumption | secondhand | unknown "
        "(author_assumption when the source uses estimate/assume/illustrative compute). "
        "locator is section/table/page if the memo states it. "
        "quality_band is S|A|B|C (GitHub awesome-lists = C). published is a year if present. "
        "Do not promote an inference to paper_says.\n"
        "Each direct claim MUST include a verbatim quote (12+ chars) copied from the cited source.\n"
        f"Ledger:\n{ledger}\n\nMemo:\n{body[:12000]}"
    )


def parse_report_markdown(markdown: str) -> dict[str, str | list[str]]:
    text = (markdown or "").strip()
    title = ""
    first = text.splitlines()[0] if text else ""
    if first.startswith("# "):
        title = first[2:].strip()
    exec_summary = _section(text, "Executive summary")
    at_glance = _section(text, "At a glance")
    decision = _section(text, "Decision rule")
    limitations_raw = _section(text, "Limitations")
    limitations = [
        re.sub(r"^[-*]\s+", "", line).strip()
        for line in (limitations_raw or "").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return {
        "title": title,
        "at_a_glance": at_glance,
        "executive_summary": exec_summary,
        "decision_rule": decision,
        "limitations": limitations[:12],
        "body_markdown": text,
    }


def _section(markdown: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(markdown or "")
    if not match:
        return ""
    rest = (markdown or "")[match.end() :]
    nxt = re.search(r"^##\s+", rest, re.MULTILINE)
    body = rest[: nxt.start()] if nxt else rest
    return body.strip()
