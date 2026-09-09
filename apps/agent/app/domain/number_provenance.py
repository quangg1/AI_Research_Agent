"""Number/claim provenance classes (Kiln Phase C).

One path for whether a *specific number* may be author-attributed in memo prose.

Provenance kinds (prefer these labels in new code)
-------------------------------------------------
- span_quote (alias: direct)
    Figure appears in 1-2 contiguous cited source sentences.
    Only this class may keep author-attribution phrasing for a specific number
    ("as reported by X", "Dettmers et al. report Y GB", "according to …").

- computed (alias: formula)
    Derived by arithmetic in the memo (e.g. 7B x 0.5 byte/param = 3.5 GB).
    Must be labeled as a calculation — never "as reported by".

- multi_source_estimate (alias: synthesis)
    Aggregated / stitched across sources or not grounded in one span.
    Must not be attributed as a single author's measured result.

Legacy mapping (adversarial.infer_provenance / claim kind)
----------------------------------------------------------
- measured + span-ok     -> span_quote
- secondhand             -> multi_source_estimate (unless span-ok upgrades)
- author_assumption      -> multi_source_estimate
- unknown                -> multi_source_estimate until span-checked
- claim kind "derived"   -> computed
- claim kind "direct"    -> span_quote *candidate* (still needs span check)
- claim kind "inferred"  -> multi_source_estimate

Repair policy (least destructive)
---------------------------------
False author-attribution is neutralized in place: keep the number and [n]
markers, rewrite the attribution clause to calculation/estimate phrasing.
Never blank citation markers inside ## References / ## Source quality.
Fail-soft: callers catch exceptions; this module itself never raises on
normal memo text.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.metric_grounding import assess_claim_span_grounding

# --- Canonical kinds ----------------------------------------------------------

SPAN_QUOTE = "span_quote"
COMPUTED = "computed"
MULTI_SOURCE_ESTIMATE = "multi_source_estimate"

PROVENANCE_KINDS = (SPAN_QUOTE, COMPUTED, MULTI_SOURCE_ESTIMATE)

# Public aliases used in architecture notes / older drafts.
KIND_ALIASES = {
    "span_quote": SPAN_QUOTE,
    "direct": SPAN_QUOTE,
    "computed": COMPUTED,
    "formula": COMPUTED,
    "derived": COMPUTED,
    "multi_source_estimate": MULTI_SOURCE_ESTIMATE,
    "synthesis": MULTI_SOURCE_ESTIMATE,
    "estimate": MULTI_SOURCE_ESTIMATE,
    # Legacy adversarial.infer_provenance labels:
    "measured": SPAN_QUOTE,  # candidate; still requires span check at use site
    "secondhand": MULTI_SOURCE_ESTIMATE,
    "author_assumption": MULTI_SOURCE_ESTIMATE,
    "unknown": MULTI_SOURCE_ESTIMATE,
}

PROTECTED_SECTIONS = ("source quality", "references")

CITE_RE = re.compile(r"\[(\d+(?:\s+[A-Za-z]+)?(?:\s*,\s*\d+(?:\s+[A-Za-z]+)?)*)\]")

AUTHOR_STOPWORDS = frozenset(
    {
        "as", "on", "in", "when", "benchmark", "estimate", "results", "the", "a",
        "an", "for", "with", "from", "by", "at", "to", "of", "and", "or", "if",
        "then", "than", "that", "this", "these", "those", "our", "we", "they",
        "their", "its", "his", "her", "not", "no", "yes", "per", "via", "using",
        "used", "use", "based", "according", "reported", "report", "reports",
        "measure", "measures", "measured", "find", "finds", "found", "observe",
        "observes", "observed", "calculation", "computed", "synthesis",
        "multi", "source", "direct", "cited", "author", "authors", "paper",
        "study", "studies", "table", "figure", "section", "key", "findings",
        "comparison", "decision", "rule", "worked", "example",
    }
)

_AUTHOR_TOKEN = r"[A-Z][\w.\-]+(?:\s+et\s+al\.?)?"
_NUM_TOKEN = r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?(?:[eE][+]?\d+)?"
_UNIT_TOKEN = r"GB|GiB|MB|MiB|TB|TiB|%|percent|VRAM"

AUTHOR_BEFORE_NUM_RE = re.compile(
    r"(?:"
    r"as\s+reported\s+by\s+(?P<author>" + _AUTHOR_TOKEN + r")"
    r"|according\s+to\s+(?P<author2>" + _AUTHOR_TOKEN + r")"
    r"|(?P<author3>" + _AUTHOR_TOKEN + r")\s+(?:et\s+al\.?\s+)?"
    r"(?:report|reports|measure|measures|find|finds|found|measured|observe|observes|observed)\b"
    r")"
    r"(?P<mid>.{0,160}?)"
    r"(?P<num>" + _NUM_TOKEN + r")\s*(?P<unit>" + _UNIT_TOKEN + r")?",
    re.I | re.S,
)

NUM_THEN_ATTRIB_RE = re.compile(
    r"(?P<num>" + _NUM_TOKEN + r")\s*(?P<unit>" + _UNIT_TOKEN + r")?"
    r"(?P<mid>.{0,80}?)"
    r"(?:"
    r"as\s+reported\s+by\s+(?P<author>" + _AUTHOR_TOKEN + r")"
    r"|according\s+to\s+(?P<author2>" + _AUTHOR_TOKEN + r")"
    r"|reported\s+by\s+(?P<author3>" + _AUTHOR_TOKEN + r")"
    r")",
    re.I | re.S,
)

# Arithmetic that produces a memory/size figure in-memo.
COMPUTED_FORMULA_RE = re.compile(
    r"(?:"
    # 7B x 0.5 byte = 3.5 GB  /  7e9 * 0.5 = 3.5
    r"\d+(?:\.\d+)?\s*[BbEe]?\d*\s*[xX\u00d7*]\s*\d+(?:\.\d+)?\s*"
    r"(?:(?:byte|bytes|B)\s*(?:/\s*param(?:eter)?s?)?\s*)?"
    r"(?:=\s*|\u2248\s*|~\s*)\d+(?:\.\d+)?\s*(?:GB|GiB|MB|MiB)?"
    r"|"
    # bare 7e9*0.5 without unit on RHS still counts when GB nearby in sentence
    r"\d+\s*[eE][+]?\d+\s*[xX\u00d7*]\s*\d+(?:\.\d+)?"
    r"|"
    r"\d+\s*[Bb]\s*[xX\u00d7*]\s*\d+(?:\.\d+)?\s*(?:byte|bytes)\b"
    r")",
    re.I,
)

SECTION_SPLIT_RE = re.compile(r"(^##\s+.+$)", re.M)


def _is_valid_author_token(token: str | None) -> bool:
    """Reject English stopwords / section nouns mistaken for author surnames."""
    raw = (token or "").strip()
    if not raw:
        return False
    head = raw.split()[0].rstrip(".")
    if head.lower() in AUTHOR_STOPWORDS:
        return False
    # Single stopword-like tokens or all-lowercase non-names.
    if len(head) <= 2:
        return False
    if not head[0].isupper():
        return False
    return True


def _match_author(m: re.Match) -> str | None:
    for key in ("author", "author2", "author3"):
        val = m.groupdict().get(key)
        if val and _is_valid_author_token(val):
            return val.strip()
    return None


def normalize_provenance_kind(raw: str | None) -> str:
    """Map any alias/legacy label onto a canonical Phase C kind."""
    key = (raw or "").strip().lower()
    return KIND_ALIASES.get(key, MULTI_SOURCE_ESTIMATE)


def map_legacy_provenance(legacy: str | None, *, claim_kind: str | None = None) -> str:
    """Bridge adversarial.infer_provenance + claim kind into Phase C kinds."""
    if claim_kind:
        ck = (claim_kind or "").strip().lower()
        if ck in ("derived", "formula", "computed"):
            return COMPUTED
        if ck in ("inferred", "inference", "speculative", "recommendation"):
            return MULTI_SOURCE_ESTIMATE
    return normalize_provenance_kind(legacy)


def is_computed_formula(text: str) -> bool:
    return bool(COMPUTED_FORMULA_RE.search(text or ""))


def _norm_num(raw: str) -> str:
    return (raw or "").replace(",", "").strip()


def _cite_nums(text: str) -> list[int]:
    out: list[int] = []
    for m in CITE_RE.finditer(text or ""):
        for piece in m.group(1).split(","):
            digits = re.match(r"\s*(\d+)", piece)
            if digits:
                out.append(int(digits.group(1)))
    return out


def _evidence_blob(ev: dict) -> str:
    return " ".join(
        str(ev.get(k) or "")
        for k in ("title", "snippet", "quote", "text", "content", "full_text")
    )


def _source_blobs_for_line(
    line: str,
    *,
    citations: list[dict],
    evidence: list[dict],
) -> list[str]:
    by_n: dict[int, dict] = {}
    for c in citations or []:
        try:
            by_n[int(c["n"])] = c
        except (KeyError, TypeError, ValueError):
            continue
    by_url = {
        (ev.get("url") or "").strip().rstrip("/").lower(): ev
        for ev in (evidence or [])
        if ev.get("url")
    }
    blobs: list[str] = []
    for n in _cite_nums(line):
        cite = by_n.get(n) or {}
        url = (cite.get("url") or "").strip().rstrip("/").lower()
        ev = by_url.get(url) or {}
        blob = _evidence_blob(ev) or (cite.get("snippet") or "")
        if blob.strip():
            blobs.append(blob)
    return blobs


def _has_author_attribution(text: str) -> bool:
    t = text or ""
    if re.search(r"\bas\s+reported\s+by\b|\baccording\s+to\b|\breported\s+by\b", t, re.I):
        return True
    if re.search(
        r"\b[A-Z][\w.\-]+(?:\s+et\s+al\.?)?\s+(?:report|reports|measure|measures|find|finds|found|measured)\b",
        t,
    ):
        return True
    return False


def _first_author(text: str) -> str | None:
    for rx in (AUTHOR_BEFORE_NUM_RE, NUM_THEN_ATTRIB_RE):
        m = rx.search(text or "")
        if m:
            a = _match_author(m)
            if a:
                return a
    m = re.search(
        r"(?:as\s+reported\s+by|according\s+to|reported\s+by)\s+([A-Z][\w.\-]+(?:\s+et\s+al\.?)?)",
        text or "",
        re.I,
    )
    if m and _is_valid_author_token(m.group(1)):
        return m.group(1).strip()
    m = re.search(
        r"\b([A-Z][\w.\-]+(?:\s+et\s+al\.?)?)\s+(?:report|reports|measure|measures|find|finds|found|measured)\b",
        text or "",
    )
    if m and _is_valid_author_token(m.group(1)):
        return m.group(1).strip()
    return None


def _first_attributed_number(text: str) -> str | None:
    for rx in (AUTHOR_BEFORE_NUM_RE, NUM_THEN_ATTRIB_RE):
        m = rx.search(text or "")
        if m:
            return _norm_num(m.group("num"))
    return None


def classify_number_attribution(
    claim_text: str,
    source_text: str = "",
    *,
    cite_count: int = 0,
) -> dict[str, Any]:
    """Classify a claim sentence's number provenance.

    Returns keys: kind, status ('ok'|'demote'), reason, author (optional), number.
    """
    claim = claim_text or ""
    if is_computed_formula(claim):
        return {
            "kind": COMPUTED,
            "status": "demote" if _has_author_attribution(claim) else "ok",
            "reason": "formula_computation",
            "author": _first_author(claim),
            "number": _first_attributed_number(claim),
        }

    if cite_count >= 3 and _has_author_attribution(claim):
        return {
            "kind": MULTI_SOURCE_ESTIMATE,
            "status": "demote",
            "reason": "multi_source_synthesis",
            "author": _first_author(claim),
            "number": _first_attributed_number(claim),
        }

    if not _has_author_attribution(claim):
        kind = COMPUTED if is_computed_formula(claim) else (
            SPAN_QUOTE if source_text else MULTI_SOURCE_ESTIMATE
        )
        return {
            "kind": kind,
            "status": "ok",
            "reason": "no_author_attribution",
            "author": None,
            "number": None,
        }

    author = _first_author(claim)
    number = _first_attributed_number(claim)
    if not (source_text or "").strip() or len(source_text.strip()) < 40:
        return {
            "kind": MULTI_SOURCE_ESTIMATE,
            "status": "demote",
            "reason": "missing_source_span",
            "author": author,
            "number": number,
        }

    ground = assess_claim_span_grounding(claim, source_text)
    if ground and ground.get("status") == "ok":
        span = (ground.get("span") or "").replace(",", "")
        src_flat = (source_text or "").replace(",", "")
        num = _norm_num(number or "")
        if num and num not in span:
            if num not in src_flat:
                reason = "number_absent_from_span"
            else:
                reason = "number_not_in_contiguous_span"
            return {
                "kind": MULTI_SOURCE_ESTIMATE,
                "status": "demote",
                "reason": reason,
                "author": author,
                "number": number,
                "grounding": ground,
            }
        return {
            "kind": SPAN_QUOTE,
            "status": "ok",
            "reason": "span_grounded",
            "author": author,
            "number": number,
            "grounding": ground,
        }

    return {
        "kind": MULTI_SOURCE_ESTIMATE,
        "status": "demote",
        "reason": (ground or {}).get("note") or "ungrounded_attribution",
        "author": author,
        "number": number,
        "grounding": ground,
    }


def _rewrite_false_attribution(line: str, *, kind: str, author: str | None) -> str:
    """Neutralize author-attribution; keep numbers and citation markers.

    Least-destructive clear fix: replace attribution clauses with calculation/
    estimate labels rather than deleting the claim or blanking [n].
    """
    text = line
    who = (author if _is_valid_author_token(author) else None) or "the cited source"

    if kind == COMPUTED:
        label = f"Calculation (not a measured figure reported by {who})"
    else:
        label = f"Estimate (not a direct measured quote from {who})"

    text = re.sub(
        rf"\bAs\s+reported\s+by\s+{re.escape(who)}\s*,?",
        f"{label}:",
        text,
        count=1,
        flags=re.I,
    )
    text = re.sub(
        rf"\bAccording\s+to\s+{re.escape(who)}\s*,?",
        f"{label}:",
        text,
        count=1,
        flags=re.I,
    )
    text = re.sub(
        rf"\bas\s+reported\s+by\s+{re.escape(who)}\b",
        f"({label.lower()})",
        text,
        count=1,
        flags=re.I,
    )
    text = re.sub(
        rf"\breported\s+by\s+{re.escape(who)}\b",
        f"({label.lower()})",
        text,
        count=1,
        flags=re.I,
    )
    text = re.sub(
        rf"\b{re.escape(who)}\s+(?:et\s+al\.?\s+)?"
        rf"(?:report|reports|measure|measures|find|finds|found|measured)\b",
        label,
        text,
        count=1,
        flags=re.I,
    )

    if text == line:
        text = f"{label}: {line.lstrip()}"

    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r":\s*:", ":", text)
    if line[:1].isspace():
        return text
    return text.strip()


def polish_number_provenance(
    body: str,
    *,
    citations: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> dict[str, Any]:
    """Rewrite false author-attributions of numbers; leave References intact.

    Returns body_markdown, flags, rewrites (audit trail).
    """
    text = body or ""
    citations = citations or []
    evidence = evidence or []
    flags: list[str] = []
    rewrites: list[dict[str, Any]] = []

    if not text.strip():
        return {"body_markdown": text, "flags": flags, "rewrites": rewrites}

    parts = SECTION_SPLIT_RE.split(text)
    out: list[str] = []
    in_protected = False

    for part in parts:
        if re.match(r"^##\s+", part or ""):
            name = re.sub(r"^##\s+", "", part).strip().lower()
            in_protected = any(p in name for p in PROTECTED_SECTIONS)
            out.append(part)
            continue

        if in_protected or not part:
            out.append(part)
            continue

        lines = part.splitlines(keepends=True)
        new_lines: list[str] = []
        for line in lines:
            raw = line.rstrip("\n")
            ending = "\n" if line.endswith("\n") else ""
            if not raw.strip() or not _has_author_attribution(raw):
                new_lines.append(line)
                continue

            blobs = _source_blobs_for_line(raw, citations=citations, evidence=evidence)
            source = "\n".join(blobs)
            cite_n = len(_cite_nums(raw))
            decision = classify_number_attribution(raw, source, cite_count=cite_n)
            if decision.get("status") != "demote":
                new_lines.append(line)
                continue

            rewritten = _rewrite_false_attribution(
                raw,
                kind=decision.get("kind") or MULTI_SOURCE_ESTIMATE,
                author=decision.get("author"),
            )
            if rewritten != raw:
                flags.append(f"number_provenance_demoted_{decision.get('kind')}")
                rewrites.append(
                    {
                        "before": raw[:240],
                        "after": rewritten[:240],
                        "kind": decision.get("kind"),
                        "reason": decision.get("reason"),
                    }
                )
                new_lines.append(rewritten + ending)
            else:
                new_lines.append(line)
        out.append("".join(new_lines))

    return {
        "body_markdown": "".join(out),
        "flags": flags,
        "rewrites": rewrites,
    }


def apply_number_provenance_polish(
    body: str,
    *,
    citations: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> dict[str, Any]:
    """Fail-soft entry used by report._report_sync after constraint_audit."""
    try:
        return polish_number_provenance(
            body, citations=citations, evidence=evidence
        )
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "body_markdown": body or "",
            "flags": ["number_provenance_skipped"],
            "rewrites": [],
            "skipped": True,
            "error": type(exc).__name__,
        }
