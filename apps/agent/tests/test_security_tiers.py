from app.config import settings
from app.domain.tenancy import missing_org_guard
from app.graph.nodes.briefing import briefing_node_auto
from app.llm.roles import model_for_role, use_role_model
from app.llm.client import LLMClient, current_role


def test_missing_org_guard_blocks_when_required(monkeypatch):
    monkeypatch.setattr(settings, "require_org_id", True)
    blocked = missing_org_guard({"query": "test"})
    assert blocked and blocked.get("status") == "failed"


def test_missing_org_guard_allows_when_org_present(monkeypatch):
    monkeypatch.setattr(settings, "require_org_id", True)
    assert missing_org_guard({"query": "test", "org_id": "org_a"}) is None


def test_briefing_auto_fails_without_org(monkeypatch):
    monkeypatch.setattr(settings, "require_org_id", True)
    out = briefing_node_auto({"query": "LoRA serving latency"})
    assert out.get("status") == "failed"


def test_model_for_role_reads_env(monkeypatch):
    monkeypatch.setattr(settings, "gemini_model_critic", "gemini-test-critic")
    assert model_for_role("critic") == "gemini-test-critic"
    assert model_for_role("unknown") is None


def test_use_role_model_temporarily_overrides(monkeypatch):
    client = LLMClient(provider="gemini", api_key="x", model="base-model", use_env=False)
    monkeypatch.setattr("app.llm.roles.model_for_role", lambda role: "override-model")
    with use_role_model(client, "critic"):
        assert client.model == "override-model"
    assert client.model == "base-model"


def test_use_role_model_tags_current_role_for_logging(monkeypatch):
    """current_role backs the "role=" field on llm_call_ok / llm_slot_failed
    log lines (added so `docker logs` can show which graph step issued a
    given LLM call, not just which key slot). Must be set and restored even
    when there's no model override for this role."""
    client = LLMClient(provider="gemini", api_key="x", model="base-model", use_env=False)
    assert current_role.get("") == ""
    with use_role_model(client, "critic"):
        assert current_role.get("") == "critic"
    assert current_role.get("") == ""
