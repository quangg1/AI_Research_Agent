from __future__ import annotations

import os
import sys
import types

import pytest

from app.llm.client import CreditsExhaustedError, LLMClient, bind_llm, current_role, get_llm, redact_secret, reset_llm
from app.llm.providers import OPENAI_COMPAT_BASE, is_credits_error, is_retryable_slot_error, split_api_keys
from app.llm.redact import scrub_obj, scrub_text


def test_split_api_keys_drops_blanks_and_dupes():
    assert split_api_keys("  sk-aaaaaaa1 ; ; sk-bbbbbbb2 ; sk-aaaaaaa1 ") == [
        "sk-aaaaaaa1",
        "sk-bbbbbbb2",
    ]
    assert split_api_keys("short;sk-long-enough") == ["sk-long-enough"]
    assert split_api_keys("") == []
    assert split_api_keys(None) == []


def test_redact_secret_strips_key():
    assert redact_secret("failed key=sk-secret-123456", "sk-secret-123456") == "failed key=***"


def test_scrub_obj_strips_api_key_fields():
    cleaned = scrub_obj({"llm": {"provider": "openai", "apiKey": "sk-user-secret-999"}, "ok": True})
    assert cleaned["llm"]["apiKey"] == "***"
    assert cleaned["ok"] is True
    assert "sk-user" not in scrub_text("Authorization: Bearer sk-user-secret-999")


def test_from_credential_does_not_use_env(monkeypatch):
    monkeypatch.setattr("app.llm.client.settings.google_api_key", "env-should-not-win")
    client = LLMClient.from_credential(
        type("C", (), {"provider": "openai", "api_key": "sk-user-key-123456", "model": "gpt-4.1-mini"})()
    )
    assert client.mode == "openai"
    assert client.source == "byok"
    assert client.available
    assert client._api_key == "sk-user-key-123456"
    assert client._base_url == OPENAI_COMPAT_BASE["openai"]
    client.close()
    assert client._api_key == ""
    assert not client.available


def test_from_credential_without_key_uses_platform(monkeypatch):
    monkeypatch.setattr("app.config.settings.openai_api_key", "sk-platform-key")
    monkeypatch.setattr("app.llm.client.settings.openai_api_key", "sk-platform-key")
    client = LLMClient.from_credential(type("C", (), {"provider": "openai", "api_key": None, "model": None})())
    assert client.source == "platform"
    assert client._api_key == "sk-platform-key"
    client.close()


def test_chat_completions_and_json(monkeypatch):
    client = LLMClient(provider="grok", api_key="xai-test-key-123456", model="grok-4.6", use_env=False)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"ok": true}'}],
                    }
                ],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            }

    def fake_post(url, headers, json):
        assert url.endswith("/responses")
        assert headers["Authorization"] == "Bearer xai-test-key-123456"
        assert json["model"] == "grok-4.6"
        assert isinstance(json["input"], list)
        return FakeResponse()

    monkeypatch.setattr(client._http, "post", fake_post)
    assert client.generate_json("hi") == {"ok": True}
    assert client.last_call_ok
    assert client.last_tokens == 6
    client.close()


def test_insufficient_quota_raises(monkeypatch):
    client = LLMClient(provider="openai", api_key="sk-platform", use_env=False, source="platform")

    class FakeResponse:
        status_code = 429
        text = '{"error":{"code":"insufficient_quota","message":"You exceeded your current quota"}}'

    monkeypatch.setattr(client._http, "post", lambda *args, **kwargs: FakeResponse())
    with pytest.raises(CreditsExhaustedError, match="credits are exhausted"):
        client.generate("hello")
    client.close()


def test_same_provider_semicolon_keys_failover(monkeypatch):
    monkeypatch.setattr("app.llm.client.settings.google_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.openai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.xai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.grok_api_key", "")
    cred = type(
        "C",
        (),
        {
            "provider": "openai",
            "api_key": "sk-dead-key-aaaaaaa; sk-live-key-bbbbbbb",
            "model": None,
            "keys": None,
        },
    )()
    client = LLMClient.from_credential(cred)
    assert [slot.api_key for slot in client._slots] == ["sk-dead-key-aaaaaaa", "sk-live-key-bbbbbbb"]
    assert [slot.provider for slot in client._slots] == ["openai", "openai"]

    def fake_post_chat(self, body):
        if self._api_key.endswith("aaaaaaa"):
            self._raise_if_credits(429, '{"error":{"code":"insufficient_quota"}}')
            return None
        return '{"ok": true}'

    monkeypatch.setattr(LLMClient, "_post_chat", fake_post_chat)
    assert client.generate_json("hello") == {"ok": True}
    assert client._api_key == "sk-live-key-bbbbbbb"
    client.close()


def test_generate_logs_slot_key_fingerprint_role_and_real_tokens(monkeypatch):
    """Regression: there was no per-call log of which key served a request or
    how many tokens it actually cost — only silent success or a slot-level
    failure line. Without it, reconstructing token usage after the fact
    means guessing from constants (see: manual ~105k-token estimate that
    turned out ~15% high once measured against the real tokenizer)."""
    monkeypatch.setattr("app.llm.client.settings.google_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.openai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.xai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.grok_api_key", "")

    def fake_init(self, api_key):
        self._gemini = object()
        self.mode = "gemini"

    class Usage:
        prompt_token_count = 120
        candidates_token_count = 40

    class Response:
        text = "hello"
        usage_metadata = Usage()

    def fake_generate_content(**_kwargs):
        return Response()

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(LLMClient, "_init_gemini", fake_init)
    monkeypatch.setattr("app.observability.logging.event", lambda name, **kw: events.append((name, kw)))

    client = LLMClient(provider="gemini", api_key="AIza-abcdefghijklmnop123456", use_env=False)
    client._gemini = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=fake_generate_content))

    role_token = current_role.set("critic")
    try:
        assert client.generate("hello") == "hello"
    finally:
        current_role.reset(role_token)

    ok_events = [kw for name, kw in events if name == "llm_call_ok"]
    assert len(ok_events) == 1
    payload = ok_events[0]
    assert payload["slot"] == 1
    assert payload["role"] == "critic"
    assert payload["prompt_tokens"] == 120
    assert payload["output_tokens"] == 40
    assert payload["total_tokens"] == 160
    assert payload["token_source"] == "usage_metadata"
    assert payload["key_fingerprint"] == "123456"
    assert "abcdefghijklmnop" not in payload["key_fingerprint"]
    client.close()


def test_env_semicolon_keys_become_slots(monkeypatch):
    monkeypatch.setattr("app.llm.client.settings.google_api_key", "AIza-first-xxxxxxxx;AIza-second-yyyyyyyy")
    monkeypatch.setattr("app.config.settings.google_api_key", "AIza-first-xxxxxxxx;AIza-second-yyyyyyyy")
    monkeypatch.setattr("app.llm.client.settings.openai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.xai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.grok_api_key", "")
    client = LLMClient(provider="gemini", use_env=True)
    assert [slot.api_key for slot in client._slots] == ["AIza-first-xxxxxxxx", "AIza-second-yyyyyyyy"]
    client.close()


def test_invalid_key_fails_over_to_next_slot(monkeypatch):
    client = LLMClient(
        provider="openai",
        api_key="sk-dead-key-aaaaaaa;sk-live-key-bbbbbbb",
        use_env=False,
    )

    def fake_post_chat(self, body):
        if self._api_key.endswith("aaaaaaa"):
            self._raise_if_credits(401, '{"error":{"code":"invalid_api_key"}}')
            return None
        return "ok"

    monkeypatch.setattr(LLMClient, "_post_chat", fake_post_chat)
    assert client.generate("hello") == "ok"
    assert client._api_key == "sk-live-key-bbbbbbb"
    client.close()


def test_failover_uses_remaining_provider_after_credits(monkeypatch):
    monkeypatch.setattr("app.llm.client.settings.google_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.openai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.xai_api_key", "xai-backup-key-123456")
    monkeypatch.setattr("app.llm.client.settings.grok_api_key", "")
    cred = type(
        "C",
        (),
        {"provider": "openai", "api_key": "sk-dead-key-123456", "model": None, "keys": None},
    )()
    client = LLMClient.from_credential(cred)
    assert [slot.provider for slot in client._slots] == ["openai", "grok"]

    def fake_post_chat(self, body):
        self._raise_if_credits(429, '{"error":{"code":"insufficient_quota"}}')
        return None

    def fake_post_responses(self, body):
        return '{"ok": true}'

    monkeypatch.setattr(LLMClient, "_post_chat", fake_post_chat)
    monkeypatch.setattr(LLMClient, "_post_responses", fake_post_responses)
    assert client.generate_json("hello") == {"ok": True}
    assert client.provider == "grok"
    assert client.source == "platform"
    client.close()


def test_failover_exhausted_when_every_slot_is_dead(monkeypatch):
    cred = type(
        "C",
        (),
        {
            "provider": "openai",
            "api_key": "sk-dead-key-123456",
            "model": None,
            "keys": {"grok": "xai-also-dead-123456"},
        },
    )()
    monkeypatch.setattr("app.llm.client.settings.google_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.openai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.xai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.grok_api_key", "")
    client = LLMClient.from_credential(cred)

    def fake_post_chat(self, body):
        self._raise_if_credits(429, '{"error":{"code":"insufficient_quota"}}')
        return None

    def fake_post_responses(self, body):
        self._raise_if_credits(429, '{"error":{"code":"insufficient_quota"}}')
        return None

    monkeypatch.setattr(LLMClient, "_post_chat", fake_post_chat)
    monkeypatch.setattr(LLMClient, "_post_responses", fake_post_responses)
    with pytest.raises(CreditsExhaustedError, match="All configured providers"):
        client.generate("hello")
    client.close()


def test_credits_pause_retries_after_interrupt_continue(monkeypatch):
    """When LangGraph interrupt resumes with continue, the same prompt is retried."""
    cred = type(
        "C",
        (),
        {"provider": "openai", "api_key": "sk-dead-key-123456", "model": None, "keys": None},
    )()
    monkeypatch.setattr("app.llm.client.settings.google_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.openai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.xai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.grok_api_key", "")
    client = LLMClient.from_credential(cred)
    calls = {"n": 0}

    def fake_post_chat(self, body):
        calls["n"] += 1
        if calls["n"] == 1:
            self._raise_if_credits(429, '{"error":{"code":"insufficient_quota"}}')
            return None
        return "resumed-ok"

    monkeypatch.setattr(LLMClient, "_post_chat", fake_post_chat)
    monkeypatch.setattr(
        "langgraph.types.interrupt",
        lambda payload: {"action": "continue", "type": payload.get("type")},
    )
    assert client.generate("hello") == "resumed-ok"
    assert calls["n"] == 2
    client.close()


def test_is_credits_error_detects_quota_not_generic_rate_limit():
    assert is_credits_error(402, "pay up")
    assert is_credits_error(429, "insufficient_quota")
    # Gemini's free-tier per-minute throttle reuses RESOURCE_EXHAUSTED /
    # "exceeded your current quota" wording for a plain RPM bump — verified
    # against a live account sitting at 1/5 RPM, 9/20 RPD when this exact
    # message fired. That text is indistinguishable from real daily quota
    # exhaustion, so a 429 must not be treated as a dead wallet unless it
    # carries an explicit provider "insufficient_quota" style marker.
    assert not is_credits_error(429, "429 RESOURCE_EXHAUSTED. exceeded your current quota")
    assert not is_credits_error(429, "429 RESOURCE_EXHAUSTED")
    assert not is_credits_error(429, "Resource has been exhausted (e.g. check quota).")
    assert not is_credits_error(429, "rate_limit_exceeded please retry")
    assert not is_credits_error(
        429,
        '{"error":{"message":"Rate limit reached","type":"requests","code":"rate_limit_exceeded"}}',
    )
    assert is_retryable_slot_error(429, "rate_limit_exceeded please retry")
    assert is_retryable_slot_error(429, "429 RESOURCE_EXHAUSTED. Please try again later.")
    assert is_retryable_slot_error(429, "429 RESOURCE_EXHAUSTED. exceeded your current quota")


def test_retryable_429_tries_every_gemini_key_before_pause(monkeypatch):
    monkeypatch.setattr("app.llm.client.settings.google_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.openai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.xai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.grok_api_key", "")
    # A 429/503 that never carries a real credits/dead-key marker gets several
    # backoff-and-retry passes across the whole pool, waiting longer each
    # time, before CreditsExhaustedError (see app.llm.client.
    # _MAX_RETRYABLE_BACKOFF_PASSES / _RETRYABLE_BACKOFF_SECONDS) — a shared
    # rate-limit bucket clearing takes longer than one 20s pass, and
    # escalating too early parks the whole graph on an interrupt() nothing
    # is watching, making an unattended run look hung. Don't sleep for real
    # in tests.
    slept: list[float] = []
    monkeypatch.setattr("app.llm.client.time.sleep", lambda s: slept.append(s))
    seen: list[str] = []

    def fake_init(self, api_key):
        self._gemini = object()
        self.mode = "gemini"

    def fake_gemini(self, *args, **kwargs):
        self._note_attempt()
        seen.append(self._api_key)
        raise self._slot_failed(credits=False, reason="retryable")

    monkeypatch.setattr(LLMClient, "_init_gemini", fake_init)
    monkeypatch.setattr(LLMClient, "_generate_gemini", fake_gemini)
    client = LLMClient(
        provider="gemini",
        api_key="AIza-first-xxxxxxxx;AIza-second-yyyyyyyy;AIza-third-zzzzzzzz",
        use_env=False,
    )
    with pytest.raises(CreditsExhaustedError):
        client.generate("hello")
    # 1 initial pass + 4 backoff-and-retry passes (still all-retryable) = 5
    # full passes across the 3-key pool before giving up. Each retry after a
    # backoff resumes from wherever the pool rotation left off (the
    # last-tried slot), not a restart at the first key.
    assert len(seen) == 15
    assert seen[:3] == ["AIza-first-xxxxxxxx", "AIza-second-yyyyyyyy", "AIza-third-zzzzzzzz"]
    assert slept == [20, 40, 60, 80]
    client.close()


def test_gemini_second_key_is_called_after_first_credits(monkeypatch):
    monkeypatch.setattr("app.llm.client.settings.google_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.openai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.xai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.grok_api_key", "")
    seen: list[str] = []

    def fake_init(self, api_key):
        self._gemini = object()
        self.mode = "gemini"

    def fake_gemini(self, *args, **kwargs):
        self._note_attempt()
        seen.append(self._api_key)
        if self._api_key.endswith("xxxxxxxx"):
            raise self._slot_failed(credits=True, reason="credits")
        return "ok-from-key-2"

    monkeypatch.setattr(LLMClient, "_init_gemini", fake_init)
    monkeypatch.setattr(LLMClient, "_generate_gemini", fake_gemini)
    client = LLMClient(
        provider="gemini",
        api_key="AIza-first-xxxxxxxx;AIza-second-yyyyyyyy",
        use_env=False,
    )
    assert client.generate("hello") == "ok-from-key-2"
    assert seen == ["AIza-first-xxxxxxxx", "AIza-second-yyyyyyyy"]
    assert client._tried == ["gemini"]
    client.close()


def test_gemini_generic_error_failovers_to_openai(monkeypatch):
    monkeypatch.setattr("app.llm.client.settings.google_api_key", "AIza-first-xxxxxxxx;AIza-second-yyyyyyyy")
    monkeypatch.setattr("app.config.settings.google_api_key", "AIza-first-xxxxxxxx;AIza-second-yyyyyyyy")
    monkeypatch.setattr("app.llm.client.settings.openai_api_key", "sk-backup-key-123456")
    monkeypatch.setattr("app.config.settings.openai_api_key", "sk-backup-key-123456")
    monkeypatch.setattr("app.llm.client.settings.xai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.grok_api_key", "")
    gemini_keys: list[str] = []

    def fake_init(self, api_key):
        self._gemini = object()
        self.mode = "gemini"

    def fake_gemini(self, *args, **kwargs):
        self._note_attempt()
        gemini_keys.append(self._api_key)
        if self._api_key.endswith("xxxxxxxx"):
            raise self._slot_failed(credits=True, reason="credits")
        raise self._slot_failed(credits=False, reason="generate_failed")

    def fake_post_chat(self, body):
        self._note_attempt()
        return '{"ok": true}'

    monkeypatch.setattr(LLMClient, "_init_gemini", fake_init)
    monkeypatch.setattr(LLMClient, "_generate_gemini", fake_gemini)
    monkeypatch.setattr(LLMClient, "_post_chat", fake_post_chat)
    client = LLMClient.from_credential(type("C", (), {"provider": "gemini", "api_key": None, "model": None})())
    assert [slot.provider for slot in client._slots] == ["gemini", "gemini", "openai"]
    assert client.generate_json("hello") == {"ok": True}
    assert gemini_keys == ["AIza-first-xxxxxxxx", "AIza-second-yyyyyyyy"]
    assert client.provider == "openai"
    assert client._tried == ["gemini", "openai"]
    client.close()


def test_credits_then_dead_fallback_does_not_claim_all_out_of_credits(monkeypatch):
    monkeypatch.setattr("app.llm.client.settings.google_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.openai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.xai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.grok_api_key", "")

    def fake_init(self, api_key):
        self._gemini = object()
        self.mode = "gemini"

    def fake_gemini(self, *args, **kwargs):
        self._note_attempt()
        raise self._slot_failed(credits=True, reason="credits")

    def fake_post_chat(self, body):
        self._note_attempt()
        self._raise_if_credits(401, '{"error":{"code":"invalid_api_key"}}')
        return None

    monkeypatch.setattr(LLMClient, "_init_gemini", fake_init)
    monkeypatch.setattr(LLMClient, "_generate_gemini", fake_gemini)
    monkeypatch.setattr(LLMClient, "_post_chat", fake_post_chat)
    cred = type(
        "C",
        (),
        {"provider": "gemini", "api_key": "AIza-dead-xxxxxxxx", "model": None, "keys": {"openai": "sk-dead-key-123456"}},
    )()
    client = LLMClient.from_credential(cred)
    with pytest.raises(CreditsExhaustedError, match="Fallback") as err:
        client.generate("hello")
    assert err.value.tried == ["gemini", "openai"]
    assert err.value.fallback_failed
    assert "All configured providers are out of credits" not in str(err.value)
    client.close()


def test_credits_message_does_not_claim_openai_if_never_called(monkeypatch):
    def fake_init(self, api_key):
        self._gemini = object()
        self.mode = "gemini"

    def fake_gemini(self, *args, **kwargs):
        self._note_attempt()
        raise self._slot_failed(credits=True, reason="credits")

    monkeypatch.setattr(LLMClient, "_init_gemini", fake_init)
    monkeypatch.setattr(LLMClient, "_generate_gemini", fake_gemini)
    client = LLMClient(provider="gemini", api_key="AIza-only-xxxxxxxx", use_env=False)
    with pytest.raises(CreditsExhaustedError, match="out of credits") as err:
        client.generate("hello")
    assert err.value.tried == ["gemini"]
    assert "openai" not in str(err.value)
    client.close()


def test_gemini_init_uses_single_env_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-first-xxxxxxxx;AIza-second-yyyyyyyy")
    captured: list[tuple[str | None, str | None]] = []

    class FakeClient:
        def __init__(self, api_key=None, http_options=None):
            captured.append((api_key, os.environ.get("GOOGLE_API_KEY")))
            self.models = self

        def generate_content(self, **kwargs):
            raise RuntimeError("no call expected")

    google = sys.modules.get("google")
    if google is not None and getattr(google, "genai", None) is not None:
        monkeypatch.setattr(google.genai, "Client", FakeClient)
    else:
        fake_genai = types.ModuleType("google.genai")
        fake_genai.Client = FakeClient
        fake_google = types.ModuleType("google")
        fake_google.genai = fake_genai
        monkeypatch.setitem(sys.modules, "google", fake_google)
        monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    client = LLMClient(provider="gemini", api_key="AIza-first-xxxxxxxx;AIza-second-yyyyyyyy", use_env=False)
    assert captured == [("AIza-first-xxxxxxxx", "AIza-first-xxxxxxxx")]
    assert os.environ["GOOGLE_API_KEY"] == "AIza-first-xxxxxxxx;AIza-second-yyyyyyyy"
    client._advance()
    assert captured[-1] == ("AIza-second-yyyyyyyy", "AIza-second-yyyyyyyy")
    assert os.environ["GOOGLE_API_KEY"] == "AIza-first-xxxxxxxx;AIza-second-yyyyyyyy"
    client.close()


def test_dead_slot_is_skipped_on_later_generate(monkeypatch):
    monkeypatch.setattr("app.llm.client.settings.google_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.openai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.xai_api_key", "")
    monkeypatch.setattr("app.llm.client.settings.grok_api_key", "")
    seen: list[str] = []

    def fake_post_chat(self, body):
        self._note_attempt()
        seen.append(self._api_key)
        if self._api_key.endswith("aaaaaaa"):
            raise self._slot_failed(credits=True, reason="credits")
        return "ok"

    monkeypatch.setattr(LLMClient, "_post_chat", fake_post_chat)
    client = LLMClient(
        provider="openai",
        api_key="sk-dead-key-aaaaaaa;sk-live-key-bbbbbbb",
        use_env=False,
    )
    assert client.generate("one") == "ok"
    assert client.generate("two") == "ok"
    assert seen == ["sk-dead-key-aaaaaaa", "sk-live-key-bbbbbbb", "sk-live-key-bbbbbbb"]
    client.close()


def test_bind_llm_is_request_scoped():
    a = LLMClient(provider="openai", api_key="sk-aaaaaaaabbbb", use_env=False)
    b = LLMClient(provider="grok", api_key="xai-ccccccccdddd", use_env=False)
    token = bind_llm(a)
    try:
        assert get_llm() is a
        inner = bind_llm(b)
        try:
            assert get_llm() is b
        finally:
            reset_llm(inner)
        assert get_llm() is a
    finally:
        reset_llm(token)
        a.close()
        b.close()


def test_missing_key_stays_heuristic():
    client = LLMClient(use_env=False)
    assert client.mode == "heuristic"
    assert client.generate("hello") == ""
    assert client.last_error == "no_llm_client"
