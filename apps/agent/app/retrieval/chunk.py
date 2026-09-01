from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.config import settings
from app.domain.credibility import credibility_score
from app.domain.schema import AgentName, SourceTier

FRONT_MATTER_RE = re.compile(r"^(?:# (.+)\n+)?(?:Source: (.+)\n)?(?:URL: (.+)\n)?(?:Published: (.+)\n)?(?:Credibility: (.+)\n)?", re.M)


def chunk_text(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    i = 0
    while i < len(words):
        piece = words[i : i + size]
        chunks.append(" ".join(piece))
        i += size - overlap
    return [c for c in chunks if c.strip()]


def load_corpus(corpus_dir: str | None = None, *, org_id: str | None = None) -> list[dict]:
    if org_id:
        return _load_from_root(_org_corpus_root(org_id), org_id=org_id)
    candidates = [
        Path(corpus_dir) if corpus_dir else None,
        Path(settings.corpus_dir),
        Path("/app/data/corpus"),
        Path("data/corpus"),
        Path("../../data/corpus"),
        Path(__file__).resolve().parents[3] / "data" / "corpus",
    ]
    root = next((p for p in candidates if p and p.exists()), Path("data/corpus"))
    return _load_from_root(root, org_id=None)


def org_corpus_root(org_id: str) -> Path:
    return _org_corpus_root(org_id)


def _org_corpus_root(org_id: str) -> Path:
    candidates = [
        Path(settings.corpus_dir) / "orgs" / org_id,
        Path("/app/data/corpus/orgs") / org_id,
        Path("data/corpus/orgs") / org_id,
        Path(__file__).resolve().parents[3] / "data" / "corpus" / "orgs" / org_id,
    ]
    return next((p for p in candidates if p.parent.parent.exists() or p.exists()), candidates[0])


def _load_from_root(root: Path, *, org_id: str | None) -> list[dict]:
    docs: list[dict] = []
    if not root.exists():
        return docs
    id_prefix = f"org_{org_id}_" if org_id else "doc_"
    for path in sorted(root.rglob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta = _parse_header(raw)
        body = _body_without_header(raw)
        if not body.strip():
            continue
        for chunk_i, chunk in enumerate(chunk_text(body)):
            doc_id = hashlib.sha1(f"{path.name}:{chunk_i}".encode()).hexdigest()[:12]
            url = meta.get("url") or f"corpus://{path.name}"
            tier, score = credibility_score(url, meta.get("published", ""), _tier(meta.get("credibility")))
            tier = SourceTier.SPECIALIST_RESEARCH
            score = min(float(score), 0.68)
            docs.append(
                {
                    "id": f"{id_prefix}{doc_id}",
                    "title": meta.get("title") or path.stem,
                    "url": url,
                    "snippet": chunk[:1200],
                    "quote": chunk[:600],
                    "source_agent": AgentName.DOCS.value,
                    "tier": tier.value,
                    "credibility": score,
                    "published": meta.get("published", ""),
                    "path": str(path),
                    "source_kind": "corpus_note" if not org_id else "org_upload",
                }
            )
    return docs


def _body_without_header(raw: str) -> str:
    lines = raw.splitlines()
    i = 0
    if i < len(lines) and lines[i].startswith("# "):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines) and re.match(r"^(Source|URL|Published|Credibility|Secondary):", lines[i], re.I):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "\n".join(lines[i:])


def _parse_header(raw: str) -> dict[str, str]:
    lines = raw.splitlines()
    meta: dict[str, str] = {}
    if lines and lines[0].startswith("# "):
        meta["title"] = lines[0][2:].strip()
    for line in lines[1:12]:
        if line.startswith("Source:"):
            meta["source"] = line.split(":", 1)[1].strip()
        elif line.startswith("URL:"):
            meta["url"] = line.split(":", 1)[1].strip()
        elif line.startswith("Published:"):
            meta["published"] = line.split(":", 1)[1].strip()
        elif line.startswith("Credibility:"):
            meta["credibility"] = line.split(":", 1)[1].strip()
    return meta


def _tier(raw: str | None) -> SourceTier | None:
    if not raw:
        return None
    key = raw.strip().lower().replace(" ", "_")
    try:
        return SourceTier(key)
    except ValueError:
        aliases = {
            "official_regulation": SourceTier.OFFICIAL_REGULATION,
            "primary_docs": SourceTier.OFFICIAL_REGULATION,
            "intergovernmental": SourceTier.INTERGOVERNMENTAL,
            "eval_lab": SourceTier.INTERGOVERNMENTAL,
            "eval_standard": SourceTier.INTERGOVERNMENTAL,
            "standard_body": SourceTier.STANDARD_BODY,
            "framework": SourceTier.STANDARD_BODY,
            "specialist_research": SourceTier.SPECIALIST_RESEARCH,
            "industry_association": SourceTier.INDUSTRY_ASSOCIATION,
            "news_analysis": SourceTier.NEWS_ANALYSIS,
            "peer_reviewed": SourceTier.PEER_REVIEWED,
        }
        for token, tier in aliases.items():
            if token in key:
                return tier
    return None
