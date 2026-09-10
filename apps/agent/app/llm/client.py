from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Literal

import httpx
from pydantic import SecretStr

from app.config import settings
from app.llm.providers import (
    DEFAULT_MODELS,
    OPENAI_COMPAT_BASE,
    PROVIDERS,
    is_credits_error,
    is_retryable_slot_error,
    is_unusable_key,
    split_api_keys,
)
from app.llm.redact import scrub_text
from app.observability.logging import logger

JSON_RE = re.compile(r"\{[\s\S]*\}|\[[\s\S]*\]")
_GENERATE_TIMEOUT = httpx.Timeout(180.0, connect=15.0)
_GEMINI_ENV = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
_RETRYABLE_BACKOFF_SECONDS = 20
# How many times to back off and retry the whole pool when every slot fails
# with a purely transient error (429/503), before treating it as real
# credit exhaustion and pausing the graph for a human to paste a new key.
# One pass (20s) wasn't enough grace for a shared-project RPM window to
# reset — a transient "everyone's rate-limited right now" got escalated
# into an indefinite interrupt() wait that nothing was watching, making an
# unattended run look hung for 5-20+ minutes instead of self-recovering.
_MAX_RETRYABLE_BACKOFF_PASSES = 4
_bound: ContextVar[LLMClient | None] = ContextVar("kiln_llm_client", default=None)
# Set by app.llm.roles.use_role_model so _log_call_ok can tag which graph
# node/purpose (briefing, planner, critic, report, rerank...) issued a call —
# purely for observability, no behavioral effect if left unset.
current_role: ContextVar[str] = ContextVar("kiln_llm_role", default="")

KeySource = Literal["platform", "byok"]


@dataclass(frozen=True)
class _Slot:
    provider: str
    api_key: str
    source: KeySource
    model: str | None = None


class SlotFailed(Exception):
    """This key cannot serve the call; try the next slot."""

    def __init__(self, provider: str, source: KeySource, *, credits: bool, reason: str = "") -> None:
        self.provider = provider
        self.source = source
        self.credits = credits
        self.reason = reason
        super().__init__(provider)


class CreditsExhaustedError(Exception):
    def __init__(
        self,
        provider: str,
        source: KeySource,
        tried: list[str] | None = None,
        *,
        fallback_failed: bool = False,
    ) -> None:
        self.provider = provider
        self.source = source
        self.tried = [name for name in (tried or [provider]) if name]
        self.fallback_failed = fallback_failed
        if len(self.tried) > 1 and fallback_failed:
            first, *rest = self.tried
            message = (
                f"{first} is out of credits. Fallback ({', '.join(rest)}) was tried but failed "
                "(invalid key, rejected, or unreachable). Paste another Gemini, OpenAI, or Grok key "
                "and Continue — Kiln resumes from this step. Keys are never stored."
            )
        elif len(self.tried) > 1:
            labels = ", ".join(self.tried)
            message = (
                f"All configured providers are out of credits (tried {labels}). "
                "Paste another Gemini, OpenAI, or Grok key and Continue — "
                "research resumes from this step. Keys are never stored."
            )
        elif source == "platform":
            message = (
                f"Kiln's {provider} credits are exhausted. Paste your own {provider} API key "
                "and Continue — research resumes from this step. Keys are never stored."
            )
        else:
            message = (
                "The API key you provided is out of credits. Use another key or provider, "
                "then Continue — research resumes from this step."
            )
        super().__init__(message)


def redact_secret(text: str, secret: str) -> str:
    return scrub_text(text, (secret,) if secret else ())


class LLMClient:
    def __init__(
        self,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        use_env: bool = True,
        source: KeySource | None = None,
    ) -> None:
        self.last_tokens = 0
        self.last_error: str | None = None
        self.last_call_ok = False
        self.mode = "heuristic"
        self._gemini = None
        self._http: httpx.Client | None = None
        self._api_key = ""
        self._base_url = ""
        self.model = model or settings.gemini_model
        self.provider = (provider or "").strip().lower()
        self.source: KeySource = source or "platform"
        self._slots: list[_Slot] = []
        self._slot_index = 0
        self._tried: list[str] = []
        self._dead: set[int] = set()

        key = (api_key or "").strip()
        if key:
            self.source = source or "byok"
            if self.provider in PROVIDERS:
                self._slots = [
                    _Slot(self.provider, part, self.source, model) for part in split_api_keys(key)
                ]
                if self._slots:
                    self._activate(self._slots[0])
            return
        if use_env:
            self._slots = _platform_slots(self.provider, model)
            if self._slots:
                self._activate(self._slots[0])

    @classmethod
    def from_credential(cls, cred: Any) -> LLMClient:
        slots = _slots_for_credential(cred)
        client = cls(use_env=False)
        client._slots = slots
        if slots:
            client._activate(slots[0])
        return client

    def _init_gemini(self, api_key: str) -> None:
        try:
            from google import genai

            # google-genai may re-read GOOGLE_API_KEY / GEMINI_API_KEY. The process
            # env is often a semicolon pool — keep the active slot pinned for the
            # whole lifetime of this Client, not only during construction.
            with _pin_gemini_env(api_key):
                self._gemini = genai.Client(
                    api_key=api_key,
                    http_options={"headers": {"x-goog-api-key": api_key}},
                )
            self.mode = "gemini"
        except Exception as exc:  # pragma: no cover
            logger.warning("gemini_init_failed %s", redact_secret(str(exc), api_key))
            self.last_error = "gemini_init_failed"
            self.mode = "heuristic"
            self._api_key = ""

    def _init_openai_compat(self, provider: str, api_key: str) -> None:
        self._base_url = OPENAI_COMPAT_BASE[provider]
        self._http = httpx.Client(timeout=_GENERATE_TIMEOUT)
        self.mode = provider

    @property
    def available(self) -> bool:
        return bool(self._gemini or self._http)

    def close(self) -> None:
        self._teardown()
        self._slots = []
        self._slot_index = 0
        self._tried = []
        self._dead = set()

    def _teardown(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None
        self._gemini = None
        self._api_key = ""
        self._base_url = ""
        self.mode = "heuristic"

    def _activate(self, slot: _Slot) -> None:
        self._teardown()
        self.provider = slot.provider
        self.source = slot.source
        self._api_key = slot.api_key
        self.model = _model_for(slot.provider, slot.model)
        if slot.provider == "gemini":
            self._init_gemini(slot.api_key)
        else:
            self._init_openai_compat(slot.provider, slot.api_key)

    def _note_attempt(self) -> None:
        name = self.provider or self.mode
        if name and name not in self._tried:
            self._tried.append(name)
        logger.info(
            "llm_slot_attempt provider=%s slot=%s/%s dead=%s",
            name or "none",
            self._slot_index + 1,
            len(self._slots),
            sorted(i + 1 for i in self._dead),
        )

    def _key_fingerprint(self) -> str:
        """Last 6 chars of the active key — enough to tell keys apart across
        a container restart (slot index resets, this doesn't), never enough
        to reconstruct the secret."""
        key = self._api_key or ""
        return key[-6:] if len(key) >= 6 else ("***" if key else "")

    def _log_call_ok(self, *, prompt_tokens: int, output_tokens: int, token_source: str) -> None:
        from app.observability.logging import event

        event(
            "llm_call_ok",
            provider=self.provider or self.mode,
            slot=self._slot_index + 1,
            slots_total=len(self._slots),
            key_fingerprint=self._key_fingerprint(),
            model=self.model,
            role=current_role.get(""),
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            total_tokens=prompt_tokens + output_tokens if prompt_tokens or output_tokens else self.last_tokens,
            token_source=token_source,
        )

    def _slot_failed(self, *, credits: bool, reason: str = "") -> SlotFailed:
        return SlotFailed(self.provider or self.mode, self.source, credits=credits, reason=reason)

    def _mark_dead(self) -> None:
        self._dead.add(self._slot_index)

    def _advance(self) -> bool:
        return self._failover(set(), permanent=True)

    def _failover(self, tried: set[int], *, permanent: bool) -> bool:
        """Rotate to the next configured key. Tries every slot before giving up."""
        if permanent:
            self._mark_dead()
        if not self._slots:
            return False
        tried.add(self._slot_index)
        from_name = self.provider or self.mode
        for step in range(1, len(self._slots) + 1):
            nxt = (self._slot_index + step) % len(self._slots)
            if nxt in tried or nxt in self._dead:
                continue
            slot = self._slots[nxt]
            self._slot_index = nxt
            logger.warning(
                "llm_failover from=%s to=%s slot=%s/%s permanent=%s",
                from_name,
                slot.provider,
                self._slot_index + 1,
                len(self._slots),
                permanent,
            )
            self._activate(slot)
            if self.available:
                return True
            logger.warning("llm_slot_unavailable provider=%s", slot.provider)
            if permanent:
                self._mark_dead()
            tried.add(nxt)
            from_name = slot.provider
        return False

    def _raise_slots_exhausted(
        self, exc: SlotFailed | None = None, *, saw_credits: bool = False
    ) -> None:
        tried = list(self._tried or ([exc.provider] if exc else [self.provider or self.mode]))
        provider = (exc.provider if exc else None) or self.provider or self.mode or "gemini"
        raise CreditsExhaustedError(
            provider,
            self.source,
            tried=tried,
            fallback_failed=bool(saw_credits and exc and not exc.credits),
        ) from exc

    def generate(
        self, prompt: str, system: str = "", max_tokens: int = 2048, json_mode: bool = False
    ) -> str:
        while True:
            try:
                return self._generate_once(prompt, system, max_tokens, json_mode)
            except CreditsExhaustedError as exc:
                if not self._pause_for_credits(exc):
                    raise
                # Resume may bind a new BYOK client; don't keep retrying the exhausted instance.
                current = _bound.get()
                if current is not None and current is not self:
                    return current.generate(
                        prompt, system=system, max_tokens=max_tokens, json_mode=json_mode
                    )
                self._revive_slots()
                continue

    def _generate_once(
        self, prompt: str, system: str = "", max_tokens: int = 2048, json_mode: bool = False
    ) -> str:
        self.last_tokens = _estimate_tokens(system + prompt)
        self.last_error = None
        self.last_call_ok = False
        saw_credits = False
        saw_only_retryable = True
        backoff_passes_left = _MAX_RETRYABLE_BACKOFF_PASSES
        tried: set[int] = set()
        # Skip keys that already failed permanently earlier in this run.
        while self._slot_index in self._dead or not self.available:
            if not self._failover(tried, permanent=True):
                break
        while True:
            try:
                if getattr(self, "_gemini", None):
                    return self._generate_gemini(prompt, system, max_tokens, json_mode)
                if getattr(self, "_http", None):
                    return self._generate_chat(prompt, system, max_tokens, json_mode)
                self.last_error = "no_llm_client"
                if self._failover(tried, permanent=True):
                    continue
                if self._slots:
                    self._raise_slots_exhausted()
                return ""
            except SlotFailed as exc:
                saw_credits = saw_credits or exc.credits
                saw_only_retryable = saw_only_retryable and exc.reason == "retryable"
                # empty_response is excluded: Gemini occasionally returns empty text as a
                # one-off content/formatting hiccup unrelated to the key itself — permanently
                # benching a healthy key (with untouched RPM/RPD headroom) over a single blip
                # is exactly the "one key never fully utilized" failure mode.
                permanent = exc.credits or exc.reason in {
                    "dead_key",
                    "generate_failed",
                    "credits",
                }
                logger.warning(
                    "llm_slot_failed provider=%s credits=%s reason=%s slot=%s/%s key=...%s role=%s",
                    exc.provider,
                    exc.credits,
                    exc.reason or "slot_failed",
                    self._slot_index + 1,
                    len(self._slots),
                    self._key_fingerprint(),
                    current_role.get("") or "-",
                )
                if self._failover(tried, permanent=permanent):
                    continue
                # Every slot in the pool failed, but none of them ever said
                # "credits exhausted" or "dead key" — only transient 429/503
                # (RPM throttle or momentary model overload). Failing over
                # doesn't help there (the whole pool can share one rate-limit
                # bucket, e.g. keys minted under the same Google Cloud
                # project), so give the pool a few backoff-and-retry passes,
                # waiting longer each time, before surfacing a false "out of
                # credits" prompt.
                if saw_only_retryable and backoff_passes_left > 0 and self._slots:
                    pass_num = _MAX_RETRYABLE_BACKOFF_PASSES - backoff_passes_left + 1
                    wait_seconds = _RETRYABLE_BACKOFF_SECONDS * pass_num
                    backoff_passes_left -= 1
                    logger.warning(
                        "llm_all_slots_retryable_backoff wait=%ss slots=%s pass=%s/%s",
                        wait_seconds,
                        len(self._slots),
                        pass_num,
                        _MAX_RETRYABLE_BACKOFF_PASSES,
                    )
                    time.sleep(wait_seconds)
                    tried.clear()
                    continue
                if self._slots:
                    self._raise_slots_exhausted(exc, saw_credits=saw_credits)
                return ""
            except CreditsExhaustedError as exc:
                if self._failover(tried, permanent=True):
                    continue
                tried_providers = list(self._tried or [exc.provider])
                raise CreditsExhaustedError(
                    exc.provider,
                    exc.source,
                    tried=tried_providers,
                    fallback_failed=exc.fallback_failed,
                ) from exc

    def _revive_slots(self) -> None:
        """Allow previously dead keys to be tried again after the user tops up or swaps keys."""
        self._dead.clear()
        self._tried.clear()
        if not self._slots:
            return
        self._slot_index = 0
        self._activate(self._slots[0])

    def _pause_for_credits(self, exc: CreditsExhaustedError) -> bool:
        """Park the LangGraph run at this LLM call. True → caller should retry the same prompt."""
        try:
            from langgraph.errors import GraphInterrupt
            from langgraph.types import interrupt
        except ImportError:
            return False
        payload = {
            "type": "credits_exhausted",
            "title": "Model credits exhausted",
            "message": str(exc),
            "provider": exc.provider,
            "tried": list(exc.tried or []),
            "source": exc.source,
            "resume_hint": (
                "Paste a new API key or top up credits, then Continue — "
                "research resumes from this step, not from scratch."
            ),
        }
        try:
            decision = interrupt(payload)
        except GraphInterrupt:
            raise
        except Exception:
            # Outside a graph node (unit tests, one-shot scripts).
            return False
        if isinstance(decision, str):
            decision = {"action": decision}
        action = str((decision or {}).get("action") or "continue").strip().lower()
        if action in {"cancel", "abort", "stop"}:
            return False
        logger.info(
            "llm_credits_resume action=%s tried=%s",
            action,
            list(exc.tried or []),
        )
        return True

    def generate_json(self, prompt: str, system: str = "", max_tokens: int = 4096) -> Any:
        raw = self.generate(
            prompt + "\n\nReturn JSON only.",
            system=system,
            json_mode=True,
            max_tokens=max_tokens,
        )
        return parse_json(raw)

    def _raise_if_credits(self, status: int | None, body: str) -> None:
        if is_credits_error(status, body):
            raise self._slot_failed(credits=True, reason="credits")
        if is_unusable_key(status, body):
            raise self._slot_failed(credits=False, reason="dead_key")
        if is_retryable_slot_error(status, body):
            raise self._slot_failed(credits=False, reason="retryable")

    def _safe_error(self, raw: str) -> str:
        extras = tuple(slot.api_key for slot in self._slots if slot.api_key)
        return scrub_text(raw, (self._api_key, *extras))[:240]

    def _generate_gemini(
        self, prompt: str, system: str, max_tokens: int, json_mode: bool
    ) -> str:
        self._note_attempt()
        contents = prompt if not system else f"{system}\n\n{prompt}"
        try:
            kwargs: dict[str, Any] = {"model": self.model, "contents": contents}
            cfg: dict[str, Any] = {"max_output_tokens": max_tokens}
            if json_mode:
                cfg["response_mime_type"] = "application/json"
            kwargs["config"] = cfg
            with _pin_gemini_env(self._api_key):
                response = self._gemini.models.generate_content(**kwargs)
            text = (response.text or "").strip()
            if not text:
                self.last_error = "empty_response"
                raise self._slot_failed(credits=False, reason="empty_response")
            usage = getattr(response, "usage_metadata", None)
            if usage:
                prompt_t = int(getattr(usage, "prompt_token_count", 0) or 0)
                out_t = int(getattr(usage, "candidates_token_count", 0) or 0)
                total_t = prompt_t + out_t
                if not total_t:
                    total_t = self.last_tokens + _estimate_tokens(text)
                self.last_tokens = total_t
                token_source = "usage_metadata"
            else:
                prompt_t = out_t = 0
                self.last_tokens += _estimate_tokens(text)
                token_source = "estimated"
            self.last_call_ok = True
            self._log_call_ok(prompt_tokens=prompt_t, output_tokens=out_t, token_source=token_source)
            return text
        except (CreditsExhaustedError, SlotFailed):
            raise
        except Exception as exc:
            blob = _exception_blob(exc)
            status = _exception_status(exc)
            self._raise_if_credits(status, blob)
            if _is_transport(exc):
                raise self._slot_failed(credits=False, reason="transport") from exc
            # Any remaining Gemini API failure should rotate keys — do not soft-fail.
            self.last_error = self._safe_error(blob)
            logger.warning("gemini_generate_failed %s", self.last_error)
            raise self._slot_failed(credits=False, reason="generate_failed") from exc

    def _generate_chat(
        self, prompt: str, system: str, max_tokens: int, json_mode: bool
    ) -> str:
        self._note_attempt()
        if self.provider == "grok" or self.mode == "grok":
            return self._generate_grok(prompt, system, max_tokens, json_mode)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        text = self._post_chat(body)
        if text is None and json_mode:
            body.pop("response_format", None)
            text = self._post_chat(body)
        if not text:
            raise self._slot_failed(credits=False, reason=self.last_error or "empty_response")
        self.last_call_ok = True
        return text

    def _generate_grok(self, prompt: str, system: str, max_tokens: int, json_mode: bool) -> str:
        # xAI recommends Responses API for current Grok models (e.g. grok-4.6).
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        user = prompt if not json_mode else f"{prompt}\n\nReturn JSON only."
        messages.append({"role": "user", "content": user})
        body: dict[str, Any] = {
            "model": self.model,
            "input": messages,
            "max_output_tokens": max_tokens,
            "store": False,
        }
        text = self._post_responses(body)
        if not text:
            raise self._slot_failed(credits=False, reason=self.last_error or "empty_response")
        self.last_call_ok = True
        return text

    def _post_responses(self, body: dict[str, Any]) -> str | None:
        assert self._http is not None
        try:
            response = self._http.post(
                f"{self._base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if response.status_code >= 400:
                err_code, err_type = _openai_error_bits(response.text)
                logger.warning(
                    "%s_responses_failed status=%s code=%s type=%s body=%s",
                    self.mode,
                    response.status_code,
                    err_code or "-",
                    err_type or "-",
                    self._safe_error(response.text),
                )
                self._raise_if_credits(response.status_code, response.text)
                self.last_error = self._safe_error(response.text)
                # xAI often returns bare 403 for bad/unfunded keys — treat as dead slot.
                if response.status_code in {401, 403}:
                    raise self._slot_failed(credits=False, reason="dead_key")
                raise self._slot_failed(
                    credits=False,
                    reason=f"http_{response.status_code}",
                )
            data = response.json()
            text = _text_from_responses(data)
            if not text:
                self.last_error = "empty_response"
                return None
            usage = data.get("usage") or {}
            prompt_t = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            out_t = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            if prompt_t or out_t:
                self.last_tokens = prompt_t + out_t
                self._log_call_ok(prompt_tokens=prompt_t, output_tokens=out_t, token_source="usage")
            else:
                self.last_tokens += _estimate_tokens(text)
                self._log_call_ok(prompt_tokens=0, output_tokens=0, token_source="estimated")
            return text
        except (CreditsExhaustedError, SlotFailed):
            raise
        except Exception as exc:
            blob = _exception_blob(exc)
            self._raise_if_credits(_exception_status(exc), blob)
            if _is_transport(exc):
                raise self._slot_failed(credits=False, reason="transport") from exc
            self.last_error = self._safe_error(blob)
            logger.warning("%s_responses_failed %s", self.mode, self.last_error)
            raise self._slot_failed(credits=False, reason="generate_failed") from exc

    def _post_chat(self, body: dict[str, Any]) -> str | None:
        assert self._http is not None
        try:
            response = self._http.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if response.status_code >= 400:
                err_code, err_type = _openai_error_bits(response.text)
                logger.warning(
                    "%s_generate_failed status=%s code=%s type=%s",
                    self.mode,
                    response.status_code,
                    err_code or "-",
                    err_type or "-",
                )
                self._raise_if_credits(response.status_code, response.text)
                self.last_error = self._safe_error(response.text)
                return None
            data = response.json()
            choices = data.get("choices") or []
            message = (choices[0] or {}).get("message") if choices else {}
            text = str((message or {}).get("content") or "").strip()
            if not text:
                self.last_error = "empty_response"
                return None
            usage = data.get("usage") or {}
            prompt_t = int(usage.get("prompt_tokens") or 0)
            out_t = int(usage.get("completion_tokens") or 0)
            if prompt_t or out_t:
                self.last_tokens = prompt_t + out_t
                self._log_call_ok(prompt_tokens=prompt_t, output_tokens=out_t, token_source="usage")
            else:
                self.last_tokens += _estimate_tokens(text)
                self._log_call_ok(prompt_tokens=0, output_tokens=0, token_source="estimated")
            return text
        except (CreditsExhaustedError, SlotFailed):
            raise
        except Exception as exc:
            blob = _exception_blob(exc)
            self._raise_if_credits(_exception_status(exc), blob)
            if _is_transport(exc):
                raise self._slot_failed(credits=False, reason="transport") from exc
            self.last_error = self._safe_error(blob)
            logger.warning("%s_generate_failed %s", self.mode, self.last_error)
            raise self._slot_failed(credits=False, reason="generate_failed") from exc


def _secret_value(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, SecretStr):
        return raw.get_secret_value().strip()
    return str(raw).strip()


def _model_for(provider: str, model: str | None) -> str:
    name = (provider or "").strip().lower()
    override = (model or "").strip()
    if override:
        return override
    if name == "gemini":
        return settings.gemini_model or DEFAULT_MODELS["gemini"]
    if name == "openai":
        return settings.openai_model or DEFAULT_MODELS["openai"]
    if name == "grok":
        return settings.grok_model or DEFAULT_MODELS["grok"]
    return DEFAULT_MODELS.get(name, settings.gemini_model)  # type: ignore[arg-type]


def _provider_order(preferred: str) -> list[str]:
    name = (preferred or "").strip().lower()
    rest = [p for p in PROVIDERS if p != name]
    return [name, *rest] if name in PROVIDERS else list(PROVIDERS)


def _is_transport(exc: Exception) -> bool:
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))


@contextmanager
def _pin_gemini_env(api_key: str) -> Iterator[None]:
    """Force google-genai to use one key, not the semicolon pool in process env."""
    saved = {name: os.environ.get(name) for name in _GEMINI_ENV}
    try:
        for name in _GEMINI_ENV:
            os.environ[name] = api_key
        yield
    finally:
        for name, prior in saved.items():
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior


def _exception_status(exc: Exception) -> int | None:
    for attr in ("code", "status_code"):
        raw = getattr(exc, attr, None)
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)
    return None


def _exception_blob(exc: Exception) -> str:
    parts = [str(exc)]
    for attr in ("message", "status", "details"):
        value = getattr(exc, attr, None)
        if value and str(value) not in parts[0]:
            parts.append(str(value))
    return " ".join(parts)


def _openai_error_bits(body: str) -> tuple[str | None, str | None]:
    try:
        err = (json.loads(body or "{}") or {}).get("error") or {}
        code = err.get("code")
        typ = err.get("type")
        return (str(code) if code is not None else None, str(typ) if typ is not None else None)
    except Exception:
        return None, None


def _text_from_responses(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") and item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"output_text", "text"} and part.get("text"):
                chunks.append(str(part["text"]))
    return "\n".join(chunks).strip()


def _platform_slots(preferred: str = "", model: str | None = None) -> list[_Slot]:
    slots: list[_Slot] = []
    seen: set[str] = set()
    preferred_name = (preferred or "").strip().lower()
    for name in _provider_order(preferred):
        for key in settings.platform_keys(name):
            if key in seen:
                continue
            seen.add(key)
            slots.append(_Slot(name, key, "platform", model if name == preferred_name else None))
    return slots


def _visitor_key_lists(cred: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {name: [] for name in PROVIDERS}
    provider = str(getattr(cred, "provider", "") or "").strip().lower()
    primary = _secret_value(getattr(cred, "api_key", None) or getattr(cred, "apiKey", None))
    if provider in PROVIDERS:
        out[provider].extend(split_api_keys(primary))
    keys = getattr(cred, "keys", None)
    if keys is None:
        return {name: values for name, values in out.items() if values}
    if isinstance(keys, dict):
        items = keys.items()
    else:
        items = ((name, getattr(keys, name, None)) for name in PROVIDERS)
    for name, raw in items:
        provider_name = str(name or "").strip().lower()
        if provider_name not in PROVIDERS:
            continue
        for secret in split_api_keys(_secret_value(raw)):
            if secret not in out[provider_name]:
                out[provider_name].append(secret)
    return {name: values for name, values in out.items() if values}


def _slots_for_credential(cred: Any) -> list[_Slot]:
    provider = str(getattr(cred, "provider", "") or "").strip().lower()
    model = getattr(cred, "model", None)
    model_s = str(model).strip() if model else None
    visitor = _visitor_key_lists(cred)
    slots: list[_Slot] = []
    seen: set[str] = set()
    for name in _provider_order(provider):
        for user_key in visitor.get(name, []):
            if user_key in seen:
                continue
            seen.add(user_key)
            slots.append(_Slot(name, user_key, "byok", model_s if name == provider else None))
        for platform_key in settings.platform_keys(name):
            if platform_key in seen:
                continue
            seen.add(platform_key)
            slots.append(_Slot(name, platform_key, "platform", model_s if name == provider else None))
    return slots


def _env_fallback(provider: str, model: str) -> tuple[str, str, str]:
    name = (provider or "").strip().lower()
    if name in PROVIDERS:
        key = settings.platform_key(name)
        default = DEFAULT_MODELS[name]
        if name == "gemini":
            default = model or settings.gemini_model
        elif name == "openai":
            default = model or settings.openai_model
        elif name == "grok":
            default = model or settings.grok_model
        return name, key, default
    if settings.google_api_key:
        return "gemini", settings.google_api_key, model or settings.gemini_model
    if settings.openai_api_key:
        return "openai", settings.openai_api_key, model or settings.openai_model
    if settings.grok_secret():
        return "grok", settings.grok_secret(), model or settings.grok_model
    return provider, "", model


def parse_json(raw: str) -> Any:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).removesuffix("```").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = JSON_RE.search(raw)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def enable_langsmith() -> bool:
    key = (settings.langchain_api_key or os.getenv("LANGCHAIN_API_KEY") or "").strip()
    if not key:
        return False
    os.environ["LANGCHAIN_API_KEY"] = key
    os.environ["LANGSMITH_API_KEY"] = key
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project or "kiln"
    os.environ["LANGSMITH_PROJECT"] = settings.langchain_project or "kiln"
    logger.info("langsmith_tracing_enabled project=%s", os.environ["LANGCHAIN_PROJECT"])
    return True


def bind_llm(client: LLMClient | None):
    return _bound.set(client)


def reset_llm(token) -> None:
    _bound.reset(token)


def get_llm() -> LLMClient:
    return _bound.get() or _default_llm


class _LLMProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(get_llm(), name)

    @property
    def mode(self) -> str:
        return get_llm().mode

    @property
    def available(self) -> bool:
        return get_llm().available

    @property
    def last_tokens(self) -> int:
        return get_llm().last_tokens

    @property
    def last_error(self) -> str | None:
        return get_llm().last_error

    @property
    def last_call_ok(self) -> bool:
        return get_llm().last_call_ok

    @property
    def model(self) -> str:
        return get_llm().model


_default_llm = LLMClient()
llm = _LLMProxy()
tracing_enabled = False
