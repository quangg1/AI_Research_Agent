from __future__ import annotations

import json
import os
import re
from typing import Any

from app.config import settings
from app.observability.logging import logger

JSON_RE = re.compile(r"\{[\s\S]*\}|\[[\s\S]*\]")


class LLMClient:
    def __init__(self) -> None:
        self.model = settings.gemini_model
        self._client = None
        self.last_tokens = 0
        self.last_error: str | None = None
        self.last_call_ok = False
        self.mode = "heuristic"
        if settings.google_api_key:
            try:
                from google import genai

                self._client = genai.Client(api_key=settings.google_api_key)
                self.mode = "gemini"
            except Exception as exc:  # pragma: no cover
                logger.warning("gemini_init_failed %s", exc)
                self.last_error = str(exc)
                self.mode = "heuristic"

    @property
    def available(self) -> bool:
        return self._client is not None

    def generate(self, prompt: str, system: str = "", max_tokens: int = 2048, json_mode: bool = False) -> str:
        self.last_tokens = _estimate_tokens(system + prompt)
        self.last_error = None
        self.last_call_ok = False
        if not self._client:
            self.last_error = "no_gemini_client"
            return ""
        contents = prompt if not system else f"{system}\n\n{prompt}"
        try:
            kwargs: dict[str, Any] = {"model": self.model, "contents": contents}
            cfg: dict[str, Any] = {"max_output_tokens": max_tokens}
            if json_mode:
                cfg["response_mime_type"] = "application/json"
            kwargs["config"] = cfg
            response = self._client.models.generate_content(**kwargs)
            text = (response.text or "").strip()
            if not text:
                self.last_error = "empty_response"
                return ""
            usage = getattr(response, "usage_metadata", None)
            if usage:
                prompt_t = int(getattr(usage, "prompt_token_count", 0) or 0)
                out_t = int(getattr(usage, "candidates_token_count", 0) or 0)
                self.last_tokens = prompt_t + out_t or self.last_tokens + _estimate_tokens(text)
            else:
                self.last_tokens += _estimate_tokens(text)
            self.last_call_ok = True
            return text
        except Exception as exc:
            self.last_error = str(exc)[:240]
            logger.warning("gemini_generate_failed %s", exc)
            return ""

    def generate_json(self, prompt: str, system: str = "", max_tokens: int = 4096) -> Any:
        raw = self.generate(prompt + "\n\nReturn JSON only.", system=system, json_mode=True, max_tokens=max_tokens)
        return parse_json(raw)


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


llm = LLMClient()
tracing_enabled = False
