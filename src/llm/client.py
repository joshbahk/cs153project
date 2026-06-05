"""DigitalOcean/OpenAI-compatible chat-completions client."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


class LLMClientError(RuntimeError):
    pass


@dataclass(slots=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "LLMUsage":
        return cls(
            prompt_tokens=int(payload.get("prompt_tokens") or payload.get("input_tokens") or 0),
            completion_tokens=int(payload.get("completion_tokens") or payload.get("output_tokens") or 0),
            total_tokens=int(payload.get("total_tokens") or 0),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(slots=True)
class LLMChatResult:
    content: str
    model: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    raw_payload: dict[str, Any] = field(default_factory=dict)


class DigitalOceanLLMClient:
    """Thin client for DigitalOcean Gradient serverless inference.

    The API is OpenAI-compatible at https://inference.do-ai.run/v1/chat/completions.
    A model access key or DigitalOcean token is passed as a Bearer token.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://inference.do-ai.run",
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key:
            raise LLMClientError("LLM simulation is enabled, but no DigitalOcean model access key was configured.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMChatResult:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(f"{self._base_url}/v1/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500] if exc.response is not None else str(exc)
            raise LLMClientError(f"LLM provider returned HTTP {exc.response.status_code}: {detail}") from exc
        except Exception as exc:
            raise LLMClientError(f"LLM provider request failed: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMClientError("LLM provider response did not include choices.")
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        if not content:
            raise LLMClientError("LLM provider response was empty.")
        return LLMChatResult(
            content=content,
            model=str(data.get("model") or model),
            usage=LLMUsage.from_payload(data.get("usage") or {}),
            raw_payload=data,
        )
