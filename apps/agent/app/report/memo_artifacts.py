"""Structured memo artifacts: thesis, evidence matrix, contrast pairs, checklists.

Deterministic sections that complement LLM synthesis — inspired by deep-research
report norms (conditional thesis, evidence tables, evaluation chain).
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.adversarial import extract_quantitative_rows
from app.domain.citations import Citation, sanitize_memo_excerpt
from app.domain.evidence_filter import filter_memo_evidence, is_memo_excerpt_noise
from app.domain.research_intent import is_methodology_eval_query, user_goal
from app.domain.textutil import distinctive_terms

_PCT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
_PCT_DELTA_RE = re.compile(
    r"(?:(?:increase|improve|gain|rise|boost|up)\s+(?:by\s+)?|"
    r"(?:decrease|drop|fall|reduce|decline|worsen)\s+(?:by\s+)?|"
    r"(\+|\-|−))\s*(\d+(?:\.\d+)?)\s*(?:%|points?|pp|percentage points?)",
    re.I,
)
_GAIN_WORDS = re.compile(
    r"\b(increas\w+|improv\w+|gain\w*|rise\w*|boost\w*|higher|better|outperform\w*)\b",
    re.I,
)
_HARM_WORDS = re.compile(
    r"\b(decreas\w+|drop(?:ped|s|ping)\b|fall\w*|reduc\w+|declin\w+|worse|harm|collapse|fail\w*)\b",
    re.I,
)
_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+")


def _blob(ev: dict) -> str:
    return " ".join(
        str(ev.get(k) or "")
        for k in ("title", "snippet", "quote", "full_text")
    )


def _cite_n(ev: dict, citations: list[dict]) -> int | str:
    url = (ev.get("url") or "").rstrip("/").lower()
    for c in citations or []:
        if (c.get("url") or "").rstrip("/").lower() == url:
            return c.get("n") or "?"
    return "?"


def _valid_cite_nums(citations: list[dict]) -> set[int]:
    out: set[int] = set()
    for c in citations or []:
        n = c.get("n")
        if n is None:
            continue
        try:
            out.add(int(n))
        except (TypeError, ValueError):
            continue
    return out


def _is_survey_without_cite(title: str, n: int | str) -> bool:
    low = (title or "").lower()
    if n in ("?", None, ""):
        return True
    if re.search(r"\b(comprehensive overview|survey of|a survey)\b", low):
        return True
    return False


def _short_title(ev: dict, limit: int = 56) -> str:
    t = (ev.get("title") or ev.get("url") or "source").strip()
    return t[:limit] + ("…" if len(t) > limit else "")


def evidence_matrix_rows(evidence: list[dict], citations: list[dict], query: str = "") -> list[dict[str, Any]]:
    """One row per distinct source: domain, headline result, verdict, limitation."""
    evidence = filter_memo_evidence(query, evidence) if query else evidence
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    anchors = set()
    for ev in evidence or []:
        url = (ev.get("url") or "").rstrip("/").lower()
        if not url or url in seen:
            continue
        seen.add(url)
        blob = _blob(ev)
        if len(blob.strip()) < 40:
            continue
        n = _cite_n(ev, citations)
        sentences = [sanitize_memo_excerpt(s) for s in _SENT_SPLIT.split(blob) if len(s.strip()) > 50]
        sentences = [s for s in sentences if s]
        best = ""
        best_score = -1
        for sent in sentences[:12]:
            score = sum(1 for m in _PCT_RE.finditer(sent))
            score += sum(1 for m in _PCT_DELTA_RE.finditer(sent))
            score += 1 if _GAIN_WORDS.search(sent) or _HARM_WORDS.search(sent) else 0
            if score > best_score:
                best_score = score
                best = sent
        if not best and sentences:
            best = sentences[0][:220]
        verdict = "mixed / unclear"
        low = best.lower()
        if _HARM_WORDS.search(low) and not _GAIN_WORDS.search(low):
            verdict = "caution / may harm"
        elif _GAIN_WORDS.search(low):
            verdict = "helps when grounded"
        domain = _infer_domain(blob, ev, query)
        limitation = _infer_limitation(blob, ev)
        rows.append(
            {
                "n": n,
                "study": _short_title(ev),
                "domain": domain,
                "result": best[:200] if best else "No extractable headline in collected excerpt.",
                "verdict": verdict,
                "limitation": limitation,
            }
        )
        if len(rows) >= 14:
            break
    return rows


def _infer_domain(blob: str, ev: dict, query: str = "") -> str:
    qlow = (query or "").lower()
    low = blob.lower()
    if any(x in qlow for x in ("legal", "law", "regulatory", "statute")) and any(
        x in low for x in ("legal", "law", "bgb", "statute", "regulatory")
    ):
        return "legal / regulatory"
    if any(x in qlow for x in ("medical", "clinical", "health", "patient")) and any(
        x in low for x in ("medical", "clinical", "ehr", "patient", "icd")
    ):
        return "healthcare"
    if any(x in low for x in ("robot", "simulator", "sim-to-real", "isaac")):
        return "simulation / robotics"
    if any(x in low for x in ("tabular", "ctgan", "gan")):
        return "tabular / structured"
    url = (ev.get("url") or "").lower()
    if "github.com" in url:
        return "repo / survey index"
    if "arxiv" in url:
        return "LLM / ML research"
    if any(x in low for x in ("synthetic", "model collapse", "data generation", "data-generation")):
        return "synthetic data / agents"
    if any(x in low for x in ("instruction", "self-instruct", "fine-tun")):
        return "instruction tuning"
    return "general ML"


def _infer_limitation(blob: str, ev: dict) -> str:
    low = blob.lower()
    tier = (ev.get("tier") or "").lower()
    if "github.com" in (ev.get("url") or ""):
        return "Curated index — not primary experiment"
    if tier in {"vendor_or_consultancy", "news_analysis"}:
        return "Vendor or secondary — not peer-reviewed"
    if "preprint" in low or "arxiv" in (ev.get("url") or ""):
        return "Preprint — not yet peer-reviewed"
    if len(str(ev.get("full_text") or "")) < 400:
        return "Abstract/snippet only in this run"
    return "See full paper for scope"


def evidence_matrix_markdown(evidence: list[dict], citations: list[dict], query: str = "") -> str:
    rows = evidence_matrix_rows(evidence, citations, query=query)
    if not rows:
        return ""
    header = "| Study | Domain | Main result (excerpt) | Helps / harms | Limitation |"
    sep = "| --- | --- | --- | --- | --- |"
    body = []
    for r in rows:
        cite = f"[{r['n']}]"
        body.append(
            f"| {r['study']} {cite} | {r['domain']} | {r['result']} | {r['verdict']} | {r['limitation']} |"
        )
    return "## Evidence matrix\n\n" + "\n".join([header, sep, *body]) + "\n"


def find_contrast_pairs(
    query: str,
    evidence: list[dict],
    citations: list[dict],
    *,
    limit: int = 4,
) -> list[dict[str, str]]:
    """Same-topic sources with opposing numeric outcomes (helps vs harms)."""
    evidence = filter_memo_evidence(query, evidence)
    valid_ns = _valid_cite_nums(citations)
    anchors = distinctive_terms(user_goal(query), limit=8)
    candidates: list[dict[str, Any]] = []
    for ev in evidence or []:
        blob = _blob(ev)
        if not blob.strip():
            continue
        n = _cite_n(ev, citations)
        if n == "?" or (valid_ns and n not in valid_ns):
            continue
        title = _short_title(ev, 48)
        if _is_survey_without_cite(title, n):
            continue
        for sent in _SENT_SPLIT.split(blob):
            clean = sanitize_memo_excerpt(sent)
            if not clean or is_memo_excerpt_noise(clean):
                continue
            if not (_PCT_DELTA_RE.search(clean) or _PCT_RE.search(clean)):
                continue
            hay = f"{clean} {(ev.get('title') or '')}".lower()
            if anchors and not any(a in hay for a in anchors):
                continue
            direction = "gain"
            if _HARM_WORDS.search(clean) and not _GAIN_WORDS.search(clean):
                direction = "harm"
            elif _GAIN_WORDS.search(clean) and _HARM_WORDS.search(clean):
                direction = "mixed"
            if direction == "mixed":
                continue
            candidates.append(
                {
                    "direction": direction,
                    "text": clean[:240],
                    "title": title,
                    "n": n,
                    "domain": _infer_domain(blob, ev, query),
                }
            )
    gains = [c for c in candidates if c["direction"] == "gain"]
    harms = [c for c in candidates if c["direction"] == "harm"]
    pairs: list[dict[str, str]] = []
    used_keys: set[str] = set()
    used_domains: set[str] = set()
    for g in gains:
        dom = g["domain"]
        for h in harms:
            if g["n"] == h["n"]:
                continue
            if h["domain"] != dom:
                continue
            key = f"{g['n']}-{h['n']}"
            if key in used_keys:
                continue
            domain_key = dom.lower()
            if domain_key in used_domains:
                continue
            used_keys.add(key)
            used_domains.add(domain_key)
            pairs.append(
                {
                    "domain": dom,
                    "positive": f"{g['title']} [{g['n']}]: {g['text']}",
                    "negative": f"{h['title']} [{h['n']}]: {h['text']}",
                    "lesson": (
                        "Generation recipe and verification matter more than volume — "
                        "the same domain can show large gains or losses depending on pipeline design."
                    ),
                }
            )
            if len(pairs) >= limit:
                return pairs
    return pairs[:limit]


def contrast_pairs_markdown(pairs: list[dict[str, str]]) -> str:
    if not pairs:
        return ""
    blocks = ["## Contrast pairs\n"]
    for i, p in enumerate(pairs, 1):
        blocks.append(f"### {i}. {p.get('domain', 'Same domain')}\n")
        blocks.append(f"- **Helps / positive signal:** {p.get('positive', '')}")
        blocks.append(f"- **Harms / caution:** {p.get('negative', '')}")
        blocks.append(f"- **Reading:** {p.get('lesson', '')}\n")
    return "\n".join(blocks) + "\n"


def build_thesis_paragraph(
    query: str,
    evidence: list[dict],
    ledger: list[Citation],
    dossier: list[dict],
    *,
    contrasts: list[dict[str, str]] | None = None,
) -> str:
    """Conditional central thesis with anchor numbers — answer-first."""
    goal = user_goal(query)
    cite_dicts = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in ledger]
    filtered = filter_memo_evidence(query, evidence)
    numbers = extract_quantitative_rows(filtered, cite_dicts)[:6]
    num_bits = []
    for row in numbers[:3]:
        num_bits.append(f"{row.get('metric')} ({row.get('title', '')[:40]} [{row.get('n')}])")
    num_clause = ""
    if num_bits:
        num_clause = " Anchor measurements in this run include: " + "; ".join(num_bits) + "."

    weak = sum(1 for d in dossier or [] if d.get("status") in {"weak", "open"})
    covered = sum(1 for d in dossier or [] if d.get("status") == "covered")
    if is_methodology_eval_query(query):
        core = (
            "Synthetic data and data-generation agents can address high-quality data scarcity in specialized "
            "domains **only when** generation targets measured coverage gaps, passes verifier or human gates, "
            "mixes with a real-data anchor, and is accepted only after gains on held-out real evaluation — "
            "not when volume alone replaces curation."
        )
    else:
        core = (
            f"This memo addresses: {goal[:200]}. "
            "Treat conclusions as **conditional** on source depth and verification — "
            "strongest when multiple independent primary sources agree on mechanism and measured outcomes."
        )

    contrast_clause = ""
    if contrasts:
        c0 = contrasts[0]
        contrast_clause = (
            f" The same domain can swing sharply by pipeline: one study reports gains while another reports harm "
            f"({c0.get('domain', 'see Contrast pairs')}) — recipe and grounding matter more than the label "
            "'synthetic'."
        )

    evidence_clause = ""
    if weak and not covered:
        evidence_clause = (
            " Current evidence is thin across several dimensions; use this as orientation, not a final decision."
        )
    elif weak:
        evidence_clause = (
            f" Several dimensions rest on indirect sources ({weak} weak/open); confirm with primary literature "
            "before production bets."
        )

    return (core + contrast_clause + num_clause + evidence_clause).strip()


def conceptual_foundations_markdown(query: str) -> str:
    if not is_methodology_eval_query(query):
        return ""
    return """## Conceptual foundations

- **Synthetic sample** — a new example (record, image, dialogue, trajectory) not copied from a real event.
- **Synthetic label** — auto-generated annotation on real or synthetic inputs.
- **Synthetic task / curriculum** — an agent chooses *what* to generate (task, difficulty, subgroup), not only text.
- **Data-generation agent** — planner → generator → critic/verifier → filter → curator with provenance.
- **Domain adaptation** — shift from a source distribution to a target domain with some target signal.
- **Domain generalization** — perform on unseen domains never used to tune the generator.
- **Distribution shift** — train and deploy distributions differ (covariate, label, or concept shift).
- **Feedback loop** — model outputs re-enter the training pool and can amplify errors or bias.
- **Model collapse** — recursive training on model-generated data loses tail diversity and accumulates error.

"""


def architecture_taxonomy_markdown(query: str) -> str:
    if not is_methodology_eval_query(query):
        return ""
    return """## Architecture taxonomy

| Family | Modalities | Scale | Fidelity | Control | Typical use |
| --- | --- | --- | --- | --- | --- |
| LLM / agent pipelines | text, code, QA, structured | high after pipeline stable | medium–high if grounded | prompts, schema, difficulty | domain QA, instruction tuning, low-resource NLP |
| Simulators | RGB, depth, 3D, trajectories | very high when automated | high if physics faithful | camera, lighting, pose | robotics, perception, rare events |
| GAN / tabular synthesizers | tabular, some time-series | high post-training | medium; mode-drop risk | conditional generation | tabular augmentation, imbalance |
| Diffusion | image, 3D volumes | medium (sampling cost) | high in vision | conditional / geometry | medical imaging, robust training |
| Rules / DSL / templates | events, code, protocols | very high | low–high | exact | compliance edge cases, testing |

"""


def evaluation_chain_markdown() -> str:
    return """## Evaluation chain

Report evidence in this order — similarity alone is not sufficient:

1. **Fidelity** — does synthetic data match needed marginals / tails?
2. **Coverage** — does it fill measured gaps (class, subgroup, rare event)?
3. **Utility** — TSTR / hybrid training gain on **real held-out** test?
4. **OOD / robustness** — natural shift, corruption, adversarial slices?
5. **Fairness** — subgroup TPR/FPR, worst-group, calibration-by-group?
6. **Privacy** — membership / reconstruction / DP accounting when required?
7. **Recursive stability** — multi-generation stress (replacement vs anchored mix)?

| Question | Primary metrics | Decision use |
| --- | --- | --- |
| Real generalization? | task metric on held-out real; TSTR | accept only if Δ > 0 with CI |
| Distribution shift? | MMD, C2ST, conditional slices | reject global-only similarity |
| Robustness? | OOD / corruption / adversarial | do not infer from ID accuracy |
| Fairness? | equalized odds / worst-group | no harm beyond tolerance |
| Collapse risk? | tail stats across generations | cap synthetic fraction |

"""


def implementation_checklist_markdown(query: str) -> str:
    if not is_methodology_eval_query(query):
        return ""
    return """## Implementation checklist

**Before generation**
- [ ] Define target task, population, and shift type; set primary, OOD, and worst-group metrics upfront.
- [ ] Split real train / calibration / test / OOD **before** prompt or generator tuning (passage-level holdout when one doc yields many samples).
- [ ] Audit real data for class balance, tails, duplicates, label noise — set coverage targets for the generator.

**During generation**
- [ ] Match generator family to scarcity type (LLM semantic gaps, simulator physical, GAN tabular, rules for edge cases).
- [ ] Oversample candidates, then filter (schema, grounding, verifier, dedupe, targeted human review).
- [ ] Record per-record provenance (generator version, prompt, seed, grounding source, scores).

**Before training**
- [ ] Measure alignment (MMD/C2ST) but do not reject intentional tail oversampling.
- [ ] Run privacy / memorization audit on sensitive sources.
- [ ] Ablate real-only, synthetic-only, and hybrid ratios under fixed compute.

**Evaluation & deploy**
- [ ] Call it an improvement only if real held-out gain holds with guardrails on OOD, fairness, calibration, privacy.
- [ ] Shadow / canary deploy with drift and subgroup monitoring.
- [ ] On gaps, collect **targeted real data** first — do not only generate more from the same teacher.

"""


_INFERENCE_QUERY_RE = re.compile(
    r"\b(kv[- ]?cache|pagedattention|inference|serving|ttft|time[- ]to[- ]first|"
    r"throughput|flop|gpu memory|hbm|latency|vllm|tensorrt)\b",
    re.I,
)
_SERVING_GAP_RE = re.compile(
    r"\b(kv[- ]?cache|ttft|time[- ]to[- ]first|flop|tflop|gflop|gpu memory|hbm|"
    r"throughput|serving stack|hardware stack)\b",
    re.I,
)


def is_inference_query(query: str) -> bool:
    return bool(_INFERENCE_QUERY_RE.search(query or ""))


def research_gaps_markdown(
    query: str,
    critic: dict,
    dossier: list[dict],
) -> str:
    gaps: list[str] = []
    for dim in dossier or []:
        if dim.get("status") in {"open", "weak"} and dim.get("label"):
            gaps.append(f"Independent primary evidence for: {dim.get('label')}")
    for g in (critic.get("coverage") or {}).get("critical_gaps") or []:
        label = g.get("label") or g.get("id")
        if label:
            gaps.append(str(label))
    if is_methodology_eval_query(query):
        gaps.extend(
            [
                "Unified synthetic-data budget theory (optimal real:synthetic ratio per domain/generator quality).",
                "Tail fidelity benchmarks — global FID/MMD can hide rare-mode loss before collapse.",
                "Causal fidelity tests — correlation-matched synthetics that fail under intervention/OOD.",
                "Fairness early-warning metrics across recursive generations (before LM perplexity moves).",
                "Generator–evaluator coupling bias (same-model judge inflation vs independent audit).",
            ]
        )
    if not is_inference_query(query):
        gaps = [g for g in gaps if not _SERVING_GAP_RE.search(g)]
    if not gaps:
        return ""
    uniq = list(dict.fromkeys(gaps))[:10]
    return "## Research gaps & next experiments\n\n" + "\n".join(f"- {g}" for g in uniq) + "\n"


def unverified_quant_markdown(
    evidence: list[dict],
    citations: list[dict],
    *,
    query: str = "",
    verified_count: int = 0,
    pipeline_summary: str = "",
) -> str:
    pool = filter_memo_evidence(query, evidence) if query else evidence
    rows = extract_quantitative_rows(pool, citations)
    if not rows:
        if pipeline_summary:
            return (
                "### Unverified numeric mentions\n\n"
                f"{pipeline_summary}\n\n"
                "_No numeric tokens were found in collected excerpts._\n"
            )
        return ""
    if verified_count > 0:
        return ""
    lines = [
        "### Unverified numeric mentions",
        "",
        "These figures appeared in collected excerpts but were **not** verified into the quantitative table "
        "(deep fetch may be incomplete or grounding failed). Use as leads, not decision cutoffs:",
        "",
        "| Value | Source | Context |",
        "| --- | --- | --- |",
    ]
    for row in rows[:12]:
        lines.append(
            f"| {row.get('metric', '?')} | [{row.get('n', '?')}] {(row.get('title') or '')[:44]} | "
            f"{(row.get('condition') or row.get('benchmark_name') or 'excerpt')[:60]} |"
        )
    if pipeline_summary:
        lines.extend(["", f"_{pipeline_summary}_"])
    return "\n".join(lines) + "\n"


def format_artifacts_for_writer(
    query: str,
    evidence: list[dict],
    citations: list[dict],
    dossier: list[dict],
    critic: dict,
) -> str:
    """Dense block appended to writer notes."""
    filtered = filter_memo_evidence(query, evidence)
    contrasts = find_contrast_pairs(query, filtered, citations)
    parts = [
        evidence_matrix_markdown(filtered, citations, query=query),
        contrast_pairs_markdown(contrasts),
        conceptual_foundations_markdown(query),
        architecture_taxonomy_markdown(query),
        evaluation_chain_markdown(),
        implementation_checklist_markdown(query),
        research_gaps_markdown(query, critic, dossier),
    ]
    return "\n".join(p for p in parts if p.strip())


def inject_structured_sections(
    markdown: str,
    query: str,
    evidence: list[dict],
    citations: list[dict],
    dossier: list[dict],
    critic: dict,
    *,
    verified_quant: int = 0,
    pipeline_summary: str = "",
) -> str:
    """Insert deterministic sections if the writer omitted them."""
    from app.domain.report_audit import _section

    text = markdown or ""
    filtered = filter_memo_evidence(query, evidence)
    inserts: list[tuple[str, str]] = []

    def need(heading: str, body: str) -> None:
        if body.strip() and not _section(text, heading):
            inserts.append((heading, body.strip()))

    contrasts = find_contrast_pairs(query, filtered, citations)
    need("Evidence matrix", evidence_matrix_markdown(filtered, citations, query=query))
    need("Contrast pairs", contrast_pairs_markdown(contrasts))
    need("Conceptual foundations", conceptual_foundations_markdown(query))
    need("Architecture taxonomy", architecture_taxonomy_markdown(query))
    need("Evaluation chain", evaluation_chain_markdown())
    need("Implementation checklist", implementation_checklist_markdown(query))
    gaps = research_gaps_markdown(query, critic, dossier)
    if gaps and not _section(text, "Research gaps"):
        need("Research gaps & next experiments", gaps)

    unverified = unverified_quant_markdown(
        filtered,
        citations,
        query=query,
        verified_count=verified_quant,
        pipeline_summary=pipeline_summary,
    )
    if unverified and _section(text, "Quantitative findings"):
        qf = _section(text, "Quantitative findings")
        if unverified.strip() not in (qf or ""):
            text = text.replace(
                "## Quantitative findings\n\n" + (qf or ""),
                "## Quantitative findings\n\n" + (qf or "") + "\n\n" + unverified,
                1,
            )

    if not inserts:
        return text

    anchor = "## Detailed analysis"
    if anchor in text:
        block = "\n\n".join(body for _, body in inserts)
        return text.replace(anchor, block + "\n\n" + anchor, 1)

    anchor = "## Key findings"
    if anchor in text:
        block = "\n\n".join(body for _, body in inserts)
        return text.replace(anchor, anchor + "\n\n" + block, 1)

    return text + "\n\n" + "\n\n".join(body for _, body in inserts)
