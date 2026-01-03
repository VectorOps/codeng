from __future__ import annotations

from typing import AsyncIterator, List, Optional, Dict, Any, Final
import json
import re
import asyncio
from pydantic import BaseModel, Field

from vocode import models
from vocode import settings


class LLMNode(models.Node):
    type: str = "llm"

    model: str
    system: Optional[str] = None
    system_append: Optional[str] = Field(
        default=None,
        description="Optional content appended to the main system prompt before preprocessors are applied.",
    )
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    outcome_strategy: models.OutcomeStrategy = Field(default=models.OutcomeStrategy.TAG)
    # Structured tool specs with short-hand coercion from strings
    tools: List[settings.ToolSpec] = Field(
        default_factory=list,
        description="Enabled tools (supports string or object spec)",
    )
    extra: Dict[str, Any] = Field(default_factory=dict)
    preprocessors: List[models.PreprocessorSpec] = Field(
        default_factory=list,
        description="Pre-execution preprocessors applied to the LLM system prompt",
    )
    max_rounds: int = Field(
        default=32,
        ge=0,
        description=(
            "Maximum number of LLM tool-call rounds allowed for this node before failing. "
            "0 means unlimited; used to prevent infinite tool loops. Defaults to 32."
        ),
    )

    # Optional reasoning effort level, passed through to the LLM provider.
    reasoning_effort: Optional[str] = Field(
        default=None,
        description=(
            "Optional reasoning effort level for reasoning-capable models. "
            "Supported values: 'none', 'minimal', 'low', 'medium', 'high'."
        ),
    )
    # TODO: validator
