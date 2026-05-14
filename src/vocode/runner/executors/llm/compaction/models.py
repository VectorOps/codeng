from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class CompactionSettings(BaseModel):
    enabled: bool = True
    trigger_threshold_ratio: float = 0.5
    keep_recent_ratio: float = 0.35
    summary_model: Optional[str] = None
    summary_provider: Optional[str] = None
    summary_temperature: Optional[float] = None
    summary_reasoning_effort: Optional[str] = None
    prompt_system: Optional[str] = None
    prompt_instructions: Optional[str] = None


class CompactionSummaryState(BaseModel):
    compacted_step_ids: List[UUID] = Field(default_factory=list)
    tokens_before: int
    tokens_after_estimate: Optional[int] = None
    trigger_threshold_ratio: float
    summary_version: str = "v1"


class LLMExecutionCompactionState(BaseModel):
    latest_compaction_step_id: Optional[UUID] = None
    compaction_count: int = 0
    last_compaction_tokens_before: Optional[int] = None


class CompactionPreparationResult(BaseModel):
    prompt_messages_count: int = 0
    estimated_context_tokens: int = 0
    input_token_limit: Optional[int] = None
    should_compact: bool = False
    settings: CompactionSettings = Field(default_factory=CompactionSettings)
    current_model: Optional[str] = None
    current_temperature: Optional[float] = None
    current_reasoning_effort: Optional[str] = None
    provider_options: Dict[str, Any] = Field(default_factory=dict)
