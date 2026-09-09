import time

import pytest

from app.config import settings
from app.domain.evidence_cache import lookup_verified_rows, reset_cache, store_verified_rows


def test_evidence_cache_ttl_expires_rows():
    reset_cache()
    rows = [{"url": "https://arxiv.org/abs/1", "metric": "92%", "n": 1}]
    store_verified_rows("org_a", "fp_ttl", rows)
    from app.domain import evidence_cache as mod

    key = mod._cache_key("org_a", "fp_ttl")
    with mod._CACHE_LOCK:
        mod._VERIFIED[key][0]["_cached_at"] = time.time() - (31 * 86400)
    assert lookup_verified_rows("org_a", "fp_ttl") == []


def test_evidence_cache_org_isolation():
    reset_cache()
    store_verified_rows("org_a", "fp_x", [{"url": "https://a/1", "metric": "1"}])
    store_verified_rows("org_b", "fp_x", [{"url": "https://b/1", "metric": "2"}])
    a = lookup_verified_rows("org_a", "fp_x")
    b = lookup_verified_rows("org_b", "fp_x")
    assert a and a[0]["url"] == "https://a/1"
    assert b and b[0]["url"] == "https://b/1"


def test_evidence_cache_requires_org_when_configured(monkeypatch):
    reset_cache()
    monkeypatch.setattr(settings, "require_org_id", True)
    with pytest.raises(ValueError):
        store_verified_rows(None, "fp", [{"url": "https://x", "metric": "1"}])
