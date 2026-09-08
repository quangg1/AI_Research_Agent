"""Tier-B trustworthiness audit: an INDEPENDENT judge (a different model
from the one that wrote the memo, or a human relaying to ChatGPT/Grok) checks
whether a real, published memo's cited numeric claims actually hold up
against the source text Kiln retrieved — catching whatever slipped past
Tier A's deterministic gates (trust_bench.py) on a memo Kiln actually
published, not a hand-crafted case.

Workflow (manual hand-off — what this was built for):
    python -m app.eval.trust_bench_e2e export <run_dump.json> --out data/eval/trust_bench_e2e_memos/<name>.json
    python -m app.eval.trust_bench_e2e build data/eval/trust_bench_e2e_memos/<name>.json --out packet.md
    # paste packet.md into ChatGPT/Grok, save its JSON verdict array to verdicts.json
    python -m app.eval.trust_bench_e2e score packet.claims.json verdicts.json

`run_dump.json` is whatever `SELECT result_json FROM research_runs WHERE id=...`
produces (the LangGraph checkpoint state used throughout this project's own
debugging this session) — `export` flattens it to the minimal snapshot shape
`build`/`score` actually need, so nothing here depends on LangGraph's state
layout.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.domain.report_integrity import CITE_RE, LOAD_NUMBER_RE

_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?%")


def _eval_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "eval"
        if candidate.is_dir():
            return candidate
    return Path("../../data/eval")


def _git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def export_memo_snapshot(run_dump: dict) -> dict:
    """Flatten a `research_runs.result_json` dump (LangGraph checkpoint
    state) down to the minimal shape the rest of this module needs, so a
    saved snapshot can be re-used without dragging in graph-state internals."""
    values = run_dump.get("values") or run_dump
    report = values.get("report") or {}
    evidence = values.get("retrieved") or values.get("evidence") or []
    return {
        "title": report.get("title") or "",
        "body_markdown": report.get("body_markdown") or "",
        "citations": [
            {"n": c.get("n"), "url": c.get("url"), "title": c.get("title")} for c in (report.get("citations") or [])
        ],
        "evidence": [
            {
                "url": e.get("url"),
                "snippet": e.get("snippet") or "",
                "quote": e.get("quote") or "",
                # Deliberately NOT truncated: build_audit_packet windows a
                # small excerpt around each claim's own number out of this,
                # and real source text can run 20-40k chars before a given
                # figure appears (papers front-load author/abstract text) —
                # capping this here just reproduces the "excerpt is only the
                # abstract" bug this export exists to avoid. Kiln's own
                # enrich pipeline already bounds full_text at fetch time.
                "full_text": e.get("full_text") or "",
            }
            for e in evidence
        ],
    }


def extract_cited_quant_claims(body_markdown: str, limit: int = 8) -> list[str]:
    """Lines making a numeric assertion with a citation attached — the same
    shape of claim Tier A's checkers police one at a time, pulled here
    straight out of a real, full memo instead of a hand-crafted case."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in (body_markdown or "").splitlines():
        line = raw.strip().lstrip("#-* ").strip()
        line = re.sub(r"^\d+\.\s+", "", line)  # ordered-list index, not a claim number
        if not line or not CITE_RE.search(line):
            continue
        # Check for a number outside any [n]/[n specialist] marker — the
        # citation's own digit would otherwise trivially satisfy this check
        # on lines that assert nothing numeric at all.
        without_cites = CITE_RE.sub("", line)
        if not (_PERCENT_RE.search(without_cites) or LOAD_NUMBER_RE.search(without_cites)):
            continue
        key = line[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        # Keep the full line here — truncating before _first_cited_n /
        # _evidence_excerpt run on it can cut the citation marker itself off
        # a long sentence, silently turning a real claim into "no citation
        # found." Truncate only for display, in build_audit_packet.
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _first_cited_n(line: str) -> int | None:
    m = CITE_RE.search(line)
    if not m:
        return None
    first = m.group(1).split(",")[0].strip()
    digits = re.match(r"\d+", first)
    return int(digits.group(0)) if digits else None


def _first_number(line: str) -> str | None:
    without_cites = CITE_RE.sub("", line)
    m = _PERCENT_RE.search(without_cites) or LOAD_NUMBER_RE.search(without_cites)
    return m.group(0) if m else None


def _evidence_excerpt(citations: list[dict], evidence: list[dict], n: int, claim_number: str | None, max_chars: int = 900) -> tuple[str, str]:
    from app.domain.quantitative_verify import _number_local_context

    cite = next((c for c in citations if c.get("n") == n), None)
    if not cite:
        return (f"(citation [{n}] not found in ledger)", "")
    url = (cite.get("url") or "").strip().rstrip("/").lower()
    ev = next((e for e in evidence if (e.get("url") or "").strip().rstrip("/").lower() == url), None)
    label = cite.get("title") or cite.get("url") or f"[{n}]"
    if not ev:
        return (label, "")
    full_text = ev.get("full_text") or ""
    # Prefer a window around the claim's own number inside the full source
    # text over `snippet`/`quote` — those are often just the paper's
    # title/author/abstract header, which never shows the actual supporting
    # figure the claim is about.
    if claim_number and full_text:
        local = _number_local_context(full_text, claim_number, window=400)
        if local:
            return (label, local[:max_chars])
    text = full_text or ev.get("quote") or ev.get("snippet") or ""
    return (label, text[:max_chars])


def build_audit_packet(memo: dict, n_claims: int = 8) -> tuple[str, list[dict]]:
    """Returns (markdown_packet, claim_records). Keep claim_records — feed
    it back into score_judge_response() alongside the judge's verdicts."""
    body = memo.get("body_markdown") or ""
    citations = memo.get("citations") or []
    evidence = memo.get("evidence") or []
    title = memo.get("title") or "(untitled memo)"

    lines = extract_cited_quant_claims(body, limit=n_claims)
    claim_records: list[dict] = []
    packet = [
        f'# Independent grounding audit — "{title}"\n\n',
        "You are an independent fact-checking judge. For EACH numbered claim below, "
        "you are given the exact sentence from a research memo (with its citation "
        "marker) and the actual excerpt from the source it cites. Decide whether the "
        "excerpt genuinely supports the SPECIFIC number/fact in the claim — not just "
        "the general topic, and not a different number attached to a different "
        "condition or subject.\n\n"
        "Verdicts:\n"
        "- SUPPORTED — the excerpt states this specific number/fact, or a clear paraphrase of it.\n"
        "- NOT_SUPPORTED — the excerpt doesn't contain this number/fact, states a different "
        "number, or attaches it to a different condition/subject than the claim does.\n"
        "- CANNOT_VERIFY — the excerpt is too short or unrelated to judge either way.\n\n"
        'Return ONLY a JSON array, one object per claim, in this exact shape:\n'
        '`[{"id": 1, "verdict": "SUPPORTED", "reason": "one sentence"}, ...]`\n\n'
        "---\n\n",
    ]
    for i, line in enumerate(lines, start=1):
        n = _first_cited_n(line)
        claim_number = _first_number(line)
        label, excerpt = (
            ("(no citation found)", "")
            if n is None
            else _evidence_excerpt(citations, evidence, n, claim_number)
        )
        display = line[:350]
        claim_records.append({"id": i, "claim": display, "cite_n": n, "source_label": label})
        packet.append(
            f"## Claim {i}\n\n"
            f"**Memo text:** {display}\n\n"
            f"**Cited source** ([{n}] {label}):\n\n"
            f"> {excerpt or '(no retrievable excerpt for this source)'}\n\n---\n\n"
        )
    return "".join(packet), claim_records


def score_judge_response(claim_records: list[dict], judge_verdicts: list[dict], save_history: bool = True, *, memo_id: str | None = None, trigger_feedback: bool = False) -> dict:
    """Score judge's verdicts and optionally trigger Tier-B feedback loop.
    
    Args:
        claim_records: Claim records from build_audit_packet
        judge_verdicts: Verdicts from judge LLM
        save_history: Save to history.jsonl
        memo_id: Memo identifier for feedback (if trigger_feedback=True)
        trigger_feedback: If True, call tier_b_feedback to flag memo if issues found
    
    Returns:
        Audit summary dict
    """
    by_id = {c["id"]: c for c in claim_records}
    verdict_by_id = {int(v["id"]): str(v.get("verdict", "")).upper() for v in judge_verdicts}
    reason_by_id = {int(v["id"]): v.get("reason", "") for v in judge_verdicts}

    supported = sum(1 for cid in by_id if verdict_by_id.get(cid) == "SUPPORTED")
    not_supported = sum(1 for cid in by_id if verdict_by_id.get(cid) == "NOT_SUPPORTED")
    cannot_verify = sum(1 for cid in by_id if verdict_by_id.get(cid) == "CANNOT_VERIFY")
    judged = supported + not_supported
    hallucination_rate = round(not_supported / judged, 3) if judged else None

    # Build confidence breakdown for tier_b_feedback
    confidence_breakdown = {
        "HIGH": supported,
        "NOT_SUPPORTED": not_supported,
        "CANNOT_VERIFY": cannot_verify,
    }

    summary = {
        "n_claims": len(claim_records),
        "supported": supported,
        "not_supported": not_supported,
        "cannot_verify": cannot_verify,
        "hallucination_rate": hallucination_rate,
        "confidence_breakdown": confidence_breakdown,
        "flagged_claims": [
            {**by_id[cid], "reason": reason_by_id.get(cid, "")} for cid in by_id if verdict_by_id.get(cid) == "NOT_SUPPORTED"
        ],
    }

    if save_history:
        history_path = _eval_dir() / "trust_bench_e2e_history.jsonl"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "date": datetime.now(timezone.utc).isoformat(),
            "git_sha": _git_sha(),
            "n_claims": summary["n_claims"],
            "hallucination_rate": hallucination_rate,
        }
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    # NEW: Tier-B feedback integration
    if trigger_feedback and memo_id:
        try:
            from app.maintenance.tier_b_feedback import process_audit_result
            from app.observability.logging import event
            
            # Trigger feedback loop (flags memo if issues found)
            action_summary = process_audit_result(
                memo_id=memo_id,
                audit_result=summary,
                # NOTE: update_callback and notify_callback are optional
                # They should be wired to actual DB update and notification functions
                # For now, we log the action instead
            )
            
            event("tier_b_feedback_triggered", 
                  memo_id=memo_id, 
                  action=action_summary.get("action"),
                  severity=action_summary.get("severity"),
                  hallucination_rate=hallucination_rate)
            
            summary["tier_b_feedback"] = action_summary
        except Exception as exc:
            # Don't fail the audit if feedback fails
            from app.observability.logging import event
            event("tier_b_feedback_error", memo_id=memo_id, error=str(exc)[:200])

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or score a Tier-B independent grounding audit.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    export_p = sub.add_parser("export", help="Flatten a research_runs result_json dump into a memo snapshot.")
    export_p.add_argument("run_dump_path")
    export_p.add_argument("--out", required=True)

    build_p = sub.add_parser("build", help="Build an audit packet from a memo snapshot.")
    build_p.add_argument("memo_path")
    build_p.add_argument("--n-claims", type=int, default=8)
    build_p.add_argument("--out", default="trust_bench_e2e_packet.md")

    score_p = sub.add_parser("score", help="Score a judge's returned verdicts.")
    score_p.add_argument("claims_path", help="the *.claims.json file written alongside the packet")
    score_p.add_argument("verdicts_path", help="JSON array the judge returned")

    args = parser.parse_args()
    if args.cmd == "export":
        run_dump = json.loads(Path(args.run_dump_path).read_text(encoding="utf-8"))
        snapshot = export_memo_snapshot(run_dump)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote memo snapshot: {args.out} ({len(snapshot['citations'])} citations, {len(snapshot['evidence'])} evidence rows)")
    elif args.cmd == "build":
        memo = json.loads(Path(args.memo_path).read_text(encoding="utf-8"))
        packet, records = build_audit_packet(memo, n_claims=args.n_claims)
        Path(args.out).write_text(packet, encoding="utf-8")
        records_path = Path(args.out).with_suffix("").with_suffix(".claims.json")
        records_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote packet: {args.out} ({len(records)} claims)")
        print(f"Wrote claim records: {records_path}")
    elif args.cmd == "score":
        records = json.loads(Path(args.claims_path).read_text(encoding="utf-8"))
        verdicts = json.loads(Path(args.verdicts_path).read_text(encoding="utf-8"))
        summary = score_judge_response(records, verdicts)
        print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
