"""Canonical anchor papers for methodology / synthetic-data research queries."""

from __future__ import annotations

from app.domain.research_intent import is_methodology_eval_query

# Stable URLs + scholar-friendly retrieval queries.
METHODOLOGY_CLASSIC_SEEDS: list[dict[str, str]] = [
    {
        "key": "self_instruct",
        "title": "Self-Instruct: Aligning Language Models with Self-Generated Instructions",
        "url": "https://arxiv.org/abs/2212.10560",
        "query": "Self-Instruct Wang 2022 Super-NaturalInstructions",
    },
    {
        "key": "phi1",
        "title": "Textbooks Are All You Need (phi-1)",
        "url": "https://arxiv.org/abs/2306.11644",
        "query": "phi-1 textbook synthetic data HumanEval Gunasekar",
    },
    {
        "key": "nature_collapse",
        "title": "AI models collapse when trained on recursively generated data",
        "url": "https://www.nature.com/articles/s41586-024-07566-y",
        "query": "Shumailov model collapse Nature 2024 recursive synthetic",
    },
    {
        "key": "german_law",
        "title": "Synthetic legal QA pipeline German law LegalMC4",
        "url": "https://arxiv.org/html/2603.23515v1",
        "query": "German law synthetic QA LegalMC4 difficulty filtering",
    },
    {
        "key": "gowal_robust",
        "title": "Improving Robustness using Generated Data (Gowal NeurIPS 2021)",
        "url": "https://proceedings.neurips.cc/paper/2019/hash/254ed7d2de3b23ab10936522dd547b78-Abstract.html",
        "query": "Gowal DDPM synthetic adversarial robustness CIFAR-10",
    },
    {
        "key": "wyllie_fairness",
        "title": "Model-induced distribution shifts and fairness",
        "url": "https://arxiv.org/abs/2402.01763",
        "query": "Wyllie model-induced distribution shift fairness recursive training",
    },
    {
        "key": "ganev_privacy",
        "title": "Privacy attacks on synthetic data (Ganev De Cristofaro)",
        "url": "https://arxiv.org/abs/2301.09384",
        "query": "Ganev De Cristofaro synthetic data privacy reconstruction outlier",
    },
    {
        "key": "van_breugel",
        "title": "Van Breugel et al. synthetic tabular minority classes",
        "url": "https://proceedings.mlr.press/v202/van-breugel23a.html",
        "query": "Van Breugel synthetic tabular minority low-density",
    },
]

def methodology_seed_urls() -> list[str]:
    return [s["url"] for s in METHODOLOGY_CLASSIC_SEEDS]


def methodology_scholar_queries(goal: str, *, limit: int = 6) -> list[str]:
    """Compact scholar sub-queries to retrieve anchor papers."""
    out: list[str] = []
    seen: set[str] = set()
    for seed in METHODOLOGY_CLASSIC_SEEDS:
        q = seed["query"].strip()
        if q and q not in seen:
            out.append(q)
            seen.add(q)
        if len(out) >= limit:
            break
    return out


def seed_evidence_stubs(query: str) -> list[dict]:
    """Placeholder evidence rows so enrich/collector can fetch classics."""
    if not is_methodology_eval_query(query):
        return []
    stubs: list[dict] = []
    for seed in METHODOLOGY_CLASSIC_SEEDS:
        stubs.append(
            {
                "id": f"seed_{seed['key']}",
                "title": seed["title"],
                "url": seed["url"],
                "snippet": seed["title"],
                "quote": seed["title"],
                "source_agent": "canonical_seed",
                "tier": "specialist_research",
                "credibility": 0.82,
                "canonical_seed": True,
            }
        )
    return stubs


def merge_seed_evidence(query: str, evidence: list[dict]) -> list[dict]:
    """Prepend missing canonical seeds without duplicating URLs."""
    if not is_methodology_eval_query(query):
        return evidence
    existing = {(e.get("url") or "").rstrip("/").lower() for e in evidence or []}
    merged = list(evidence or [])
    for stub in seed_evidence_stubs(query):
        url = (stub.get("url") or "").rstrip("/").lower()
        if url and url not in existing:
            merged.append(stub)
            existing.add(url)
    return merged
