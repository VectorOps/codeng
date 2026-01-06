from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator, Optional, TYPE_CHECKING

from pydantic import Field, field_validator

from vocode import models, state
from vocode.runner.base import BaseExecutor, ExecutorInput

if TYPE_CHECKING:
    from vocode.project import Project


class FileReadNode(models.Node):
    type: str = "file_read"

    files: list[str] = Field(
        ...,
        description="Relative file path or list of paths to read from project root.",
    )

    prepend_template: Optional[str] = Field(
        default="User provided {filename}:\n",
        description="Template prepended before each file content; use None to disable.",
    )

    @field_validator("files", mode="before")
    @classmethod
    def _coerce_files(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [v]
        return list(v)  # type: ignore[arg-type]


class FileReadExecutor(BaseExecutor):
    type = "file_read"

    def __init__(self, config: FileReadNode, project: "Project"):
        super().__init__(config=config, project=project)
        self.config = config

    def _validate_relpath(self, rel: str) -> Path:
        p = Path(rel)
        if p.is_absolute():
            raise ValueError(f"Path must be relative to project: {rel}")

        try:
            base = self.project.base_path  # type: ignore[attr-defined]
        except Exception:
            raise RuntimeError("FileReadExecutor requires project.base_path")

        base_path = Path(base).resolve()
        full = (base_path / p).resolve()

        try:
            _ = full.relative_to(base_path)
        except Exception:
            raise ValueError(f"Path escapes project root: {rel}")

        if not full.exists():
            raise FileNotFoundError(f"File does not exist: {rel}")
        if not full.is_file():
            raise IsADirectoryError(f"Not a file: {rel}")

        return full

    @staticmethod
    def _read_file_sync(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            data = path.read_bytes()
            return data.decode("utf-8", errors="replace")

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        cfg = self.config
        contents: list[str] = []

        for rel in cfg.files:
            full_path = self._validate_relpath(rel)
            text = await asyncio.to_thread(self._read_file_sync, full_path)

            if cfg.prepend_template is not None:
                prefix = cfg.prepend_template.format(filename=full_path.name)
                contents.append(prefix)

            contents.append(text)

        combined = "".join(contents)

        outcome_name: Optional[str] = None
        outcomes = cfg.outcomes or []
        if len(outcomes) == 1:
            outcome_name = outcomes[0].name
        elif len(outcomes) > 1:
            names = [o.name for o in outcomes]
            for pref in ("next", "success"):
                if pref in names:
                    outcome_name = pref
                    break
            if outcome_name is None:
                outcome_name = outcomes[0].name

        message = state.Message(
            role=models.Role.ASSISTANT,
            text=combined,
        )
        step = state.Step(
            execution=inp.execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=message,
            is_complete=True,
            is_final=True,
            outcome_name=outcome_name,
        )
        yield step