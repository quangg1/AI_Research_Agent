from __future__ import annotations

import json
from pathlib import Path

from app.observability.logging import logger

_CACHED: Path | None = None


def _candidates() -> list[Path]:
    paths = [
        Path("/app/data/memory/runs.jsonl"),
        Path("data/memory/runs.jsonl"),
        Path("/tmp/kiln-memory/runs.jsonl"),
    ]
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "data").exists() or (parent / "data" / "corpus").exists():
            paths.insert(0, parent / "data" / "memory" / "runs.jsonl")
            break
    return paths


def _writable(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        probe = path.parent / ".writable"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _path() -> Path:
    global _CACHED
    if _CACHED is not None:
        return _CACHED
    for candidate in _candidates():
        if _writable(candidate):
            _CACHED = candidate
            return candidate
    fallback = Path("/tmp/kiln-memory/runs.jsonl")
    try:
        fallback.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    _CACHED = fallback
    return fallback


def remember(query: str, title: str, claims: list[dict], citations: list[dict]) -> None:
    row = {
        "query": query[:500],
        "title": title[:200],
        "claims": [c.get("text") for c in claims[:6] if isinstance(c, dict)],
        "urls": [c.get("url") for c in citations[:8] if isinstance(c, dict)],
    }
    try:
        with _path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("memory_write_skipped %s", exc)


def recall(query: str, k: int = 3) -> list[dict]:
    try:
        path = _path()
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-80:]
    except Exception as exc:
        logger.debug("memory_read_skipped %s", exc)
        return []
    tokens = set(query.lower().split())
    scored: list[tuple[int, dict]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        blob = (row.get("query") or "") + " " + " ".join(row.get("claims") or [])
        score = len(tokens & set(blob.lower().split()))
        if score:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:k]]
