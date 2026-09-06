"""Shared caps for web/scholar retrieval and full-page enrich."""

SEARCH_QUERY_CAPS = {"quick": 3, "standard": 6, "deep": 10}
API_RESULTS_PER_QUERY = 15
RANK_PRIMARY_CAP = 15
RANK_SECONDARY_REST = 5
RANK_NO_PRIMARY_CAP = 10

ENRICH_FETCH_CAP: dict[str, tuple[int, int]] = {
    "quick": (3, 6),
    "standard": (6, 12),
    # Most deep runs finish in 1 critic iteration (see logs: critic passes on
    # iteration 1 far more often than it loops) — a 10-of-24 first-iteration
    # cap left ~14 already-budgeted enrich calls unused on those runs, so
    # most of the ~20 retrieved sources stayed snippet-only even though the
    # pool had paid for full-text on nearly all of them. Since
    # merge_unique_evidence now actually keeps a fetched full_text instead of
    # discarding it, raising this uses the same ENRICH_POOL budget more
    # fully — richer writer notes and quant candidates at no extra cost.
    "deep": (18, 24),
}

PLAN_SUBQUERY_CAPS = {"quick": 4, "standard": 8, "deep": 12}
RETRIEVAL_POOL = {"quick": 6, "standard": 16, "deep": 28}
ENRICH_POOL = {"quick": 4, "standard": 12, "deep": 24}
DEEP_MAX_TOOL_CALLS = RETRIEVAL_POOL["deep"] + ENRICH_POOL["deep"]
DEEP_RESERVE_CALLS = 10
STANDARD_MAX_TOOL_CALLS = RETRIEVAL_POOL["standard"] + ENRICH_POOL["standard"]

RETRIEVE_TOP_K = 20
QDRANT_TOP_K = 12
FANOUT_CEILING = 5
