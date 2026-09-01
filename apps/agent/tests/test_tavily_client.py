from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools import tavily_client


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(self._payload)

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url: str, json: dict):
        self.calls += 1
        if not self._responses:
            raise RuntimeError("no responses")
        return self._responses.pop(0)


def test_split_tavily_keys(monkeypatch):
    monkeypatch.setattr(
        tavily_client.settings,
        "tavily_api_key",
        "tvly-aaaaaaaa; tvly-bbbbbbbb",
    )
    client = tavily_client.TavilyClient()
    client.reload()
    assert len(client._keys) == 2


def test_tavily_failover_on_rate_limit(monkeypatch):
    monkeypatch.setattr(
        tavily_client.settings,
        "tavily_api_key",
        "tvly-firstkey1;tvly-secondkey2",
    )
    responses = [
        _FakeResponse(429, text='{"error":"rate limit"}'),
        _FakeResponse(200, {"results": [{"title": "Hit", "url": "https://example.com/a", "content": "body"}]}),
    ]
    fake = _FakeClient(responses)

    def fake_client(*args, **kwargs):
        return fake

    monkeypatch.setattr(tavily_client.httpx, "Client", fake_client)
    client = tavily_client.TavilyClient()
    rows = client.search("lora serving")
    assert fake.calls == 2
    assert rows[0]["title"] == "Hit"


def test_tavily_skips_dead_key(monkeypatch):
    monkeypatch.setattr(
        tavily_client.settings,
        "tavily_api_key",
        "tvly-deadkey01;tvly-goodkey02",
    )
    responses = [
        _FakeResponse(401, text='{"error":"invalid api key"}'),
        _FakeResponse(200, {"results": [{"title": "OK", "url": "https://example.com/b", "content": "x"}]}),
    ]
    fake = _FakeClient(responses)
    monkeypatch.setattr(tavily_client.httpx, "Client", lambda *a, **k: fake)
    client = tavily_client.TavilyClient()
    rows = client.search("rag")
    assert rows[0]["title"] == "OK"
    assert 0 in client._dead
