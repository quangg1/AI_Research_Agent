"""RACE-oriented report quality: section-wise generation, continuation, synthesis rewrite.

Targets DeepResearch Bench dimensions: comprehensiveness, insight, instruction-following, readability.
"""

from __future__ import annotations

import re

from app.domain.decompose import derive_slots, must_cover_from_slots
from app.domain.research_intent import user_goal
from app.llm.client import CreditsExhaustedError, llm
from app.llm.roles import use_role_model
from app.report.deep_write import notes_max_chars, word_count, word_target, writer_system
from app.report.memo_structure import consolidate_memo_structure, merge_inline_citations

_REQUIRED_SECTIONS = (
    "At a glance",
    "Executive summary",
    "Key findings",
    "Detailed analysis",
    "Decision rule",
    "References",
)

_TRUNC_TAIL_RE = re.compile(r"[.!?)\]\"']$")
_CITE_STACK_RE = re.compile(r"(\[\d+(?:\s+(?:peer|repo|docs|news|preprint|primary|specialist|vendor|unreliable|industry))?\])(?:\s*\[\d+(?:\s+(?:peer|repo|docs|news|preprint|primary|specialist|vendor|unreliable|industry))?\]){2,}")


def race_criteria(query: str, brief: dict | None = None, dossier: list[dict] | None = None) -> dict:
    """Task-specific evaluation criteria (RACE-style) for writer + rewrite passes."""
    goal = user_goal(query) or query or ""
    slots = derive_slots(goal, use_llm=False)
    must = must_cover_from_slots(slots)
    open_dims = [
        d.get("label") or d.get("id")
        for d in (dossier or [])
        if d.get("status") in {"open", "weak"} and d.get("label")
    ]
    depth = str((brief or {}).get("depth") or "standard").lower()
    return {
        "comprehensiveness": [
            f"Cover every explicit ask in: {goal[:220]}",
            *(f"Address dimension: {m}" for m in must[:8]),
            *(f"Close gap: {g}" for g in open_dims[:4]),
        ],
        "insight": [
            "State mechanism + implication, not only definitions.",
            "Contradictions appear once under ## Contradictions & debates only.",
            "Quant table only for measured outcomes; missing metrics → Uncertainties & gaps.",
        ],
        "instruction_following": [
            f"Answer the exact question first in Executive summary: {goal[:180]}",
            "One ### subsection per must-answer dimension under Detailed analysis.",
            "Decision rule: cite [n] for every % threshold or label as heuristic + re-benchmark.",
        ],
        "readability": [
            "No [DIRECT]/[INFERRED] tags; prose only.",
            "Max 2 inline cites per sentence OR one merged group [1, 6, 8, 9 peer] — never duplicate the same bullet title.",
            "Source quality: ONE bullet per band label; merge all cites for that band into one bracket group.",
        ],
        "min_words": word_target(str((brief or {}).get("depth") or "standard")),
    }


def criteria_block(criteria: dict) -> str:
    lines = ["Task-specific quality bar (RACE):"]
    for dim in ("comprehensiveness", "insight", "instruction_following", "readability"):
        items = criteria.get(dim) or []
        if not items:
            continue
        lines.append(f"- {dim.replace('_', ' ').title()}:")
        lines.extend(f"  • {x}" for x in items[:6])
    return "\n".join(lines)


def _tail_looks_complete(text: str) -> bool:
    """References often end in URLs/paths — that is NOT truncation."""
    tail = (text or "")[-160:].strip()
    if not tail:
        return False
    if tail.endswith("```"):
        return True
    if _TRUNC_TAIL_RE.search(tail):
        return True
    # URL / path / markdown-link / cite / fence leftovers
    if re.search(r"(https?://\S+|www\.\S+|\]\([^)]+\)|`[^`]+`|\[[0-9]+[^\]]*\])\s*$", tail, re.I):
        return True
    # Ends on identifier / filename when closing sections already exist
    if re.search(r"[A-Za-z0-9/_\-]+\s*$", tail) and re.search(
        r"^##\s+(References|Source quality|Limitations)\s*$", text, re.I | re.M
    ):
        return True
    return False


def memo_looks_truncated(markdown: str) -> bool:
    """False-positive guard: research memos normally end on References URLs.

    Root bug was treating URL/path tails as truncation, then discarding the whole
    LLM memo for compose. Section contract (including At a glance) stays strict.
    """
    text = (markdown or "").strip()
    if not text or word_count(text) < 400:
        return True
    for heading in _REQUIRED_SECTIONS:
        if not re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.I | re.M):
            return True
    if not _tail_looks_complete(text):
        return True
    if "## Detailed analysis" in text and text.count("### ") < 2:
        return True
    return False


def missing_sections(markdown: str) -> list[str]:
    text = markdown or ""
    return [h for h in _REQUIRED_SECTIONS if not re.search(rf"^##\s+{re.escape(h)}\s*$", text, re.I | re.M)]


_CITE_ANY_RE = re.compile(r"\[\d+(?:\s+(?:peer|repo|docs|news|preprint|primary|specialist|vendor|unreliable|industry))?\]")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def split_stacked_citation_sentences(markdown: str) -> str:
    """Merge 3+ citations into one group; never duplicate prose to attach extra cites."""
    lines: list[str] = []
    for line in (markdown or "").splitlines():
        if line.strip().startswith("#") or line.strip().startswith("|") or line.strip().startswith("```"):
            lines.append(line)
            continue
        parts = _SENT_SPLIT_RE.split(line.strip())
        if not parts:
            lines.append(line)
            continue
        rebuilt: list[str] = []
        for sent in parts:
            cites = _CITE_ANY_RE.findall(sent)
            if len(cites) <= 2:
                rebuilt.append(sent)
                continue
            rebuilt.append(merge_inline_citations(sent))
        lines.append(" ".join(rebuilt) if rebuilt else line)
    return "\n".join(lines)


def destack_inline_citations(markdown: str) -> str:
    """Merge [1][2][3] stacks into [1, 2, 3] (with optional peer/repo suffix)."""

    def repl(match: re.Match) -> str:
        return merge_inline_citations(match.group(0))

    return _CITE_STACK_RE.sub(repl, markdown or "")


_HRULE_LINE = re.compile(r"^\s*---\s*$", re.M)
_VISUAL_SECTION = re.compile(
    r"\n## Visual summary & code[\s\S]*?(?=\n## References|\Z)",
    re.I,
)


def strip_spurious_hrules(markdown: str) -> str:
    """Remove standalone --- lines (LLM section breaks)."""
    text = _HRULE_LINE.sub("", markdown or "")
    return re.sub(r"\n{4,}", "\n\n\n", text)


def strip_visual_artifacts_section(markdown: str) -> str:
    """Drop optional code/mermaid appendix if the model still emits it."""
    return _VISUAL_SECTION.sub("\n", markdown or "")


def polish_citations(
    markdown: str,
    *,
    citations: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> str:
    text = consolidate_memo_structure(markdown or "", citations=citations, evidence=evidence)
    text = split_stacked_citation_sentences(destack_inline_citations(text))
    text = strip_visual_artifacts_section(text)
    return strip_spurious_hrules(text)


def section_front_prompt(base_prompt: str, criteria: dict) -> str:
    return (
        f"{base_prompt}\n\n"
        f"{criteria_block(criteria)}\n\n"
        "PHASE 1 ONLY — write from # title through the END of ## Detailed analysis "
        "(include every ### dimension subsection). "
        "Do NOT write Quantitative findings, Worked example, Contradictions, Decision rule, or References yet. "
        "Do NOT put Contradictions, Counter-evidence, or Open questions inside Detailed analysis. "
        "Each ### subsection should be substantive (250+ words when notes allow). "
        "Stop after the last ### under Detailed analysis."
    )


def section_back_prompt(
    *,
    query: str,
    front_markdown: str,
    notes: str,
    citations: list[dict],
    criteria: dict,
    comparison_rule: str,
) -> str:
    ledger = "\n".join(
        f"[{c.get('n')}] {c.get('title') or ''}" for c in citations if c.get("n")
    )
    return (
        f"User question:\n{query}\n\n"
        f"{criteria_block(criteria)}\n\n"
        "PHASE 2 — continue the memo below. Do NOT repeat the Executive summary thesis.\n"
        f"Write ONLY these remaining sections in order:\n"
        "## Quantitative findings\n## Worked example\n## Contradictions & debates\n"
        "## Decision rule\n## Uncertainties & gaps\n## Limitations\n## Source quality\n"
        "## References\n\n"
        "Contradictions & debates is the ONLY section for H1/H2 tension and counter-evidence. "
        "Uncertainties & gaps is the ONLY section for missing metrics and field unknowns. "
        "Quantitative findings table: measured numbers only (% / ms / tok/s / FLOPs) — no qualitative mechanism claims. "
        "Source quality: ONE bullet per band; merge cites as [1, 6, 8, 9 peer] — never repeat the same bullet title.\n\n"
        f"{comparison_rule}\n"
        f"Citation ledger:\n{ledger}\n\n"
        f"Evidence notes excerpt:\n{notes[:16000]}\n\n"
        f"Memo part 1 (continue from here):\n{front_markdown[-14000:]}"
    )


def continuation_prompt(
    *,
    query: str,
    partial: str,
    missing: list[str],
    notes: str,
    criteria: dict,
) -> str:
    return (
        f"User question:\n{query}\n\n"
        f"{criteria_block(criteria)}\n\n"
        "The memo was cut off. Continue EXACTLY where it stopped.\n"
        f"Missing or incomplete sections: {', '.join(missing) or 'tail'}\n"
        "Do not restart from the title. Do not duplicate earlier paragraphs.\n"
        f"Notes:\n{notes[:12000]}\n\n"
        f"Partial memo tail:\n{partial[-10000:]}"
    )


def rewrite_prompt(
    *,
    query: str,
    markdown: str,
    audit_notes: list[str],
    criteria: dict,
    critic_reasons: list[str] | None = None,
) -> str:
    issues = list(audit_notes or []) + list(critic_reasons or [])[:6]
    issue_block = "\n".join(f"- {n}" for n in issues) or "- Improve depth and reduce redundancy."
    return (
        f"User question:\n{query}\n\n"
        f"{criteria_block(criteria)}\n\n"
        "Rewrite this research memo to fix the issues below while preserving all valid [n] citations.\n"
        "Requirements:\n"
        "- Keep the same section structure and title.\n"
        "- Fix truncation, thin counter-evidence, cite stacking, and structural duplication.\n"
        "- Remove nested Contradictions/Counter-evidence from Detailed analysis; merge duplicate gap lists.\n"
        "- Preserve or increase length when adding facts from notes; never shorten to fix redundancy.\n"
        "- Deepen ### subsections with NEW facts from notes; do not pad with boilerplate.\n"
        "- Do NOT add a 'Research critic' dump — integrate fixes into the prose.\n\n"
        f"Issues to fix:\n{issue_block}\n\n"
        f"Memo:\n{markdown[:28000]}"
    )


def generate_sectionwise_memo(
    base_prompt: str,
    *,
    query: str,
    notes: str,
    citations: list[dict],
    criteria: dict,
    comparison_rule: str,
    depth: str,
    max_tokens_front: int,
    max_tokens_back: int,
) -> tuple[str, str]:
    """Two-phase deep write. Returns (markdown, mode)."""
    if (depth or "standard").lower() != "deep":
        text = _gen(base_prompt, max_tokens_front)
        return text, "single_pass"

    front = _gen(section_front_prompt(base_prompt, criteria), max_tokens_front)
    if not front.strip():
        return _gen(base_prompt, max_tokens_front + max_tokens_back), "single_fallback"

    back = _gen(
        section_back_prompt(
            query=query,
            front_markdown=front,
            notes=notes,
            citations=citations,
            criteria=criteria,
            comparison_rule=comparison_rule,
        ),
        max_tokens_back,
        system=(
            writer_system()
            + " You are completing phase 2 of a memo. Output only the remaining ## sections."
        ),
    )
    if not back.strip():
        return _gen(base_prompt, max_tokens_front + max_tokens_back), "single_fallback"
    merged = f"{front.rstrip()}\n\n{back.lstrip()}".strip()
    return merged, "sectionwise_deep"


def repair_truncation(
    markdown: str,
    *,
    query: str,
    notes: str,
    criteria: dict,
    max_tokens: int,
) -> tuple[str, bool]:
    if not memo_looks_truncated(markdown):
        return markdown, False
    missing = missing_sections(markdown)
    cont = _gen(
        continuation_prompt(
            query=query,
            partial=markdown,
            missing=missing,
            notes=notes,
            criteria=criteria,
        ),
        max_tokens,
        system=writer_system() + " Continue a truncated memo without repeating earlier content.",
    )
    if not cont.strip():
        return markdown, False
    if cont.lstrip().startswith("#"):
        return f"{markdown.rstrip()}\n\n{cont.lstrip()}", True
    return f"{markdown.rstrip()}\n\n{cont.lstrip()}", True


def rewrite_for_quality(
    markdown: str,
    *,
    query: str,
    audit_notes: list[str],
    criteria: dict,
    critic: dict | None = None,
    max_tokens: int = 12000,
) -> tuple[str, bool]:
    reasons = list((critic or {}).get("reasons") or [])
    depth = (critic or {}).get("depth_score") or {}
    score = int(depth.get("score") or 0)
    if not audit_notes and score >= 65 and (critic or {}).get("status") == "sufficient":
        return markdown, False
    try:
        rewritten = _gen(
            rewrite_prompt(
                query=query,
                markdown=markdown,
                audit_notes=audit_notes,
                criteria=criteria,
                critic_reasons=reasons,
            ),
            max_tokens,
            system=writer_system() + " Rewrite the full memo; output markdown only.",
        )
    except CreditsExhaustedError:
        raise
    except Exception:
        return markdown, False
    text = (rewritten or "").strip()
    if not text or word_count(text) < max(300, int(word_count(markdown) * 0.5)):
        return markdown, False
    return text, True


def expand_memo_if_short(
    markdown: str,
    *,
    query: str,
    notes: str,
    criteria: dict,
    min_words: int,
    max_tokens: int,
    depth: str = "standard",
) -> tuple[str, bool]:
    target = int(min_words or word_target(depth))
    if word_count(markdown) >= int(target * 0.92):
        return markdown, False
    note_limit = notes_max_chars(depth)
    expanded = _gen(
        (
            f"User question:\n{query}\n\n"
            f"{criteria_block(criteria)}\n\n"
            f"The memo below is too thin for deep research ({word_count(markdown)} words; target {target}). "
            "Expand with NEW material from the notes — not repetition of existing sentences.\n"
            "Priority order:\n"
            "1. ## Detailed analysis ### subsections — add mechanism steps, benchmarks, named papers, quotes.\n"
            "2. ## Worked example — concrete walkthrough if notes name systems.\n"
            "3. ## Contradictions & debates — strongest disagreeing source in depth.\n"
            "4. ## Key findings — only if a major claim from notes is missing entirely.\n"
            "Do NOT duplicate Executive summary prose or add nested Contradictions inside Analysis.\n"
            "Keep all valid [n] citations; add cites for every new fact.\n\n"
            f"Research notes (mine unused bullets):\n{notes[:note_limit]}\n\n"
            f"Memo:\n{markdown[:28000]}"
        ),
        max_tokens,
        system=writer_system() + " Expand the memo in place; return the full updated markdown.",
    )
    text = (expanded or "").strip()
    min_gain = max(400, int(target * 0.12))
    if not text or word_count(text) < word_count(markdown) + min_gain:
        return markdown, False
    return text, True


def ensure_memo_depth(
    markdown: str,
    *,
    query: str,
    notes: str,
    criteria: dict,
    min_words: int,
    max_tokens: int,
    depth: str,
    max_passes: int = 2,
) -> tuple[str, int]:
    """Run expansion until word target met or passes exhausted."""
    text = markdown
    passes = 0
    target = int(min_words or word_target(depth))
    while passes < max_passes and word_count(text) < int(target * 0.92):
        text, did = expand_memo_if_short(
            text,
            query=query,
            notes=notes,
            criteria=criteria,
            min_words=target,
            max_tokens=max_tokens,
            depth=depth,
        )
        if not did:
            break
        passes += 1
    return text, passes


def _gen(prompt: str, max_tokens: int, system: str | None = None) -> str:
    slots = getattr(llm, "_slots", None) or []
    if not llm.available and not slots:
        return ""
    with use_role_model(llm, "writer"):
        return (
            llm.generate(prompt, system=system or writer_system(), max_tokens=max_tokens) or ""
        ).strip()
