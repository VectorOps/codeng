from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from vocode import state


class HistoryMutationResult(BaseModel):
    changed: bool = Field(default=False)
    active_branch_id: Optional[UUID] = Field(default=None)
    created_branch_id: Optional[UUID] = Field(default=None)
    branch_head_step_id: Optional[UUID] = Field(default=None)
    resume_step_id: Optional[UUID] = Field(default=None)
    removed_visible_step_ids: List[UUID] = Field(default_factory=list)
    upserted_steps: List[state.Step] = Field(default_factory=list)
