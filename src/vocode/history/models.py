from __future__ import annotations

from enum import Enum
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from vocode import state


class HistoryMutationKind(str, Enum):
    FORK = "fork"
    SWITCH_BRANCH = "switch_branch"


class HistoryBranchSummary(BaseModel):
    id: UUID
    head_step_id: Optional[UUID] = Field(default=None)
    base_step_id: Optional[UUID] = Field(default=None)
    label: Optional[str] = Field(default=None)
    created_at: datetime
    is_active: bool = Field(default=False)


class HistoryMutationResult(BaseModel):
    changed: bool = Field(default=False)
    mutation_kind: Optional[HistoryMutationKind] = Field(default=None)
    active_branch_id: Optional[UUID] = Field(default=None)
    created_branch_id: Optional[UUID] = Field(default=None)
    removed_step_ids: List[UUID] = Field(default_factory=list)
    upserted_step_ids: List[UUID] = Field(default_factory=list)
    upserted_steps: List[state.Step] = Field(default_factory=list)
    branch_summaries: List[HistoryBranchSummary] = Field(default_factory=list)
