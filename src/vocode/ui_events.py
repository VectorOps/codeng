from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class UIEventSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ProjectUIEvent(BaseModel):
    severity: UIEventSeverity = Field(default=UIEventSeverity.INFO)
    message: str
    title: Optional[str] = Field(default=None)
    source: Optional[str] = Field(default=None)
    details: Optional[str] = Field(default=None)
