"""Evidence-bound hypothesis generation through the Claude Messages API.

The request carries the same instructions, evidence serialization and output
schema as the OpenAI adapter, so the workflow's citation and abstention checks
apply unchanged. Provider text never reaches errors or traces; only numeric
usage, the model id, the request id and the stop reason are retained.
"""

from __future__ import annotations

from typing import Any

import anthropic
from pydantic import ValidationError

from .models import Evidence, Hypothesis
from .reasoning import (
    REASONING_INSTRUCTIONS,
    REASONING_PROMPT_REVISION,
    HypothesisOutput,
    ModelGenerationError,
    bind_hypotheses,
    evidence_context,
)

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
# Thinking shares this budget with the JSON answer. 16k stays well inside the
# SDK's non-streaming limit while leaving room for adaptive thinking.
DEFAULT_MAX_TOKENS = 16_000
_OUTPUT_SCHEMA = anthropic.transform_schema(HypothesisOutput)


def _usage(message: Any) -> dict[str, int | None]:
    usage = getattr(message, "usage", None)

    def count(name: str) -> int | None:
        value = getattr(usage, name, None)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    input_tokens = count("input_tokens")
    output_tokens = count("output_tokens")
    total = (
        input_tokens + output_tokens
        if input_tokens is not None and output_tokens is not None
        else None
    )
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total}


class AnthropicMessagesHypothesisGenerator:
    """Structured evidence-bound reasoning with Claude via the official SDK."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        base_url: str | None = None,
        effort: str | None = None,
        timeout_seconds: float = 120.0,
        max_retries: int = 1,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty model id")
        if effort is not None and effort not in EFFORT_LEVELS:
            raise ValueError(f"effort must be one of {sorted(EFFORT_LEVELS)}")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        if client is None and not api_key:
            raise ValueError("api_key is required when no client is supplied")
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._owns_client = client is None
        # The key is passed explicitly so the client never falls back to a
        # profile or token found on disk or elsewhere in the environment.
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url or None,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def generate(
        self,
        *,
        question: str,
        incident_type: str | None,
        evidence: list[Evidence],
    ) -> tuple[list[Hypothesis], dict[str, Any]]:
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA}
        }
        if self.effort is not None:
            output_config["effort"] = self.effort
        try:
            message = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=REASONING_INSTRUCTIONS,
                messages=[
                    {
                        "role": "user",
                        "content": evidence_context(
                            question=question,
                            incident_type=incident_type,
                            evidence=evidence,
                        ),
                    }
                ],
                output_config=output_config,
            )
        except anthropic.APIStatusError as error:
            raise ModelGenerationError(
                f"Claude Messages API rejected the request: {error.status_code}"
            ) from error
        except anthropic.APIError as error:
            # Connection failures and timeouts, after the SDK's own retries.
            raise ModelGenerationError("Claude Messages API request failed.") from error

        usage = _usage(message)
        if message.stop_reason != "end_turn":
            # refusal, max_tokens (truncated JSON) or anything unexpected.
            raise ModelGenerationError(
                f"Claude response did not complete: {message.stop_reason}", usage=usage
            )
        text = next(
            (block.text for block in message.content if block.type == "text"), None
        )
        if text is None:
            raise ModelGenerationError(
                "Claude response did not contain structured output text.", usage=usage
            )
        try:
            output = HypothesisOutput.model_validate_json(text)
        except ValidationError as error:
            # The API enforces the schema's shape; length and range limits are
            # checked here, and a violation is a failed generation, not data.
            raise ModelGenerationError(
                "Claude output did not satisfy the hypothesis contract.", usage=usage
            ) from error
        hypotheses = bind_hypotheses(output, evidence)
        return hypotheses, {
            "provider": "anthropic",
            "prompt_revision": REASONING_PROMPT_REVISION,
            "model": getattr(message, "model", None) or self.model,
            "response_id": getattr(message, "id", None),
            "hypothesis_count": len(hypotheses),
            "abstained": not hypotheses,
            **usage,
            "requested_max_output_tokens": self.max_tokens,
            "requested_effort": self.effort,
        }
