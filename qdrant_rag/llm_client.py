"""
Provider-neutral LLM helpers for RAG answers, QA generation, and RAGAS.
"""
from __future__ import annotations

from typing import Any

from config import (
    ANTHROPIC_API_KEY,
    LLM_MODEL,
    LLM_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_MAX_OUTPUT_TOKENS,
    OPENAI_REASONING_EFFORT,
    RAGAS_OPENAI_MODEL,
)


OPENAI_PROVIDERS = {"chatgpt", "openai", "gpt"}
CLAUDE_PROVIDERS = {"claude", "anthropic"}


class LLMRateLimitError(Exception):
    """Raised when the selected provider returns a rate limit error."""


class LLMAPIError(Exception):
    """Raised when the selected provider returns a non-rate-limit API error."""


def normalized_provider() -> str:
    provider = LLM_PROVIDER.lower()
    if provider in OPENAI_PROVIDERS:
        return "chatgpt"
    if provider in CLAUDE_PROVIDERS:
        return "claude"
    raise ValueError(
        f"지원하지 않는 LLM_PROVIDER={LLM_PROVIDER!r}. "
        "claude 또는 chatgpt를 사용하세요."
    )


def required_api_key_name() -> str:
    return "OPENAI_API_KEY" if normalized_provider() == "chatgpt" else "ANTHROPIC_API_KEY"


def is_llm_configured() -> bool:
    return bool(OPENAI_API_KEY if normalized_provider() == "chatgpt" else ANTHROPIC_API_KEY)


class LLMClient:
    def __init__(self) -> None:
        self.provider = normalized_provider()
        if self.provider == "chatgpt":
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")
            from openai import OpenAI

            self.client: Any = OpenAI(api_key=OPENAI_API_KEY)
        else:
            if not ANTHROPIC_API_KEY:
                raise ValueError("ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.")
            import anthropic

            self._anthropic = anthropic
            self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def generate(self, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        if self.provider == "chatgpt":
            return self._generate_openai(system_prompt, user_prompt, max_tokens)
        return self._generate_anthropic(system_prompt, user_prompt, max_tokens)

    def _generate_anthropic(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> str:
        try:
            response = self.client.messages.create(
                model=LLM_MODEL,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text.strip()
        except self._anthropic.RateLimitError as exc:
            raise LLMRateLimitError(str(exc)) from exc
        except self._anthropic.APIError as exc:
            raise LLMAPIError(str(exc)) from exc

    def _generate_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> str:
        from openai import APIError, RateLimitError

        try:
            create_kwargs = {
                "model": LLM_MODEL,
                "instructions": system_prompt,
                "input": user_prompt,
                "max_output_tokens": max(max_tokens, OPENAI_MAX_OUTPUT_TOKENS),
            }
            if LLM_MODEL.startswith(("gpt-5", "o1", "o3", "o4")):
                create_kwargs["reasoning"] = {"effort": OPENAI_REASONING_EFFORT}

            response = self.client.responses.create(**create_kwargs)
            return self._extract_openai_text(response)
        except RateLimitError as exc:
            raise LLMRateLimitError(str(exc)) from exc
        except APIError as exc:
            raise LLMAPIError(str(exc)) from exc

    def _extract_openai_text(self, response: Any) -> str:
        status = getattr(response, "status", "unknown")
        incomplete = getattr(response, "incomplete_details", None)
        reason = getattr(incomplete, "reason", None) if incomplete else None

        output_text = getattr(response, "output_text", None)
        if output_text and output_text.strip():
            text = output_text.strip()
            if status == "incomplete":
                raise self._openai_incomplete_error(status, reason, text)
            return text

        parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())

        if parts:
            text = "\n".join(parts).strip()
            if status == "incomplete":
                raise self._openai_incomplete_error(status, reason, text)
            return text

        detail = f" status={status}"
        if reason:
            detail += f", reason={reason}"
        raise LLMAPIError(
            "OpenAI 응답에 출력 텍스트가 없습니다."
            f"{detail}. LLM_MODEL/OPENAI_MODEL 또는 OPENAI_MAX_OUTPUT_TOKENS 설정을 확인하세요."
        )

    def _openai_incomplete_error(self, status: str, reason: str | None, text: str) -> LLMAPIError:
        detail = f"status={status}"
        if reason:
            detail += f", reason={reason}"
        preview = text[:120].replace("\n", " ")
        return LLMAPIError(
            "OpenAI 응답이 중간에 끊겼습니다. "
            f"{detail}. OPENAI_MAX_OUTPUT_TOKENS 값을 더 키워보세요. partial={preview!r}"
        )


def build_langchain_llm() -> Any:
    if normalized_provider() == "chatgpt":
        from langchain_openai import ChatOpenAI

        model = RAGAS_OPENAI_MODEL if LLM_MODEL.startswith(("gpt-5", "o1", "o3", "o4")) else LLM_MODEL
        return ChatOpenAI(model=model, api_key=OPENAI_API_KEY)

    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=LLM_MODEL, api_key=ANTHROPIC_API_KEY)
