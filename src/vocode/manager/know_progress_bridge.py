from __future__ import annotations

import asyncio
from typing import Optional

from vocode.project import Project

from . import proto as manager_proto
from .progress_emitter import ProgressEmitter


class KnowProgressBridge:
    def __init__(
        self,
        *,
        project: Project,
        progress_emitter: ProgressEmitter,
    ) -> None:
        self._project = project
        self._progress_emitter = progress_emitter
        self._repo_label_by_id: dict[str, str] = {}

    def make_progress_callback(
        self,
        *,
        progress_id: str,
        title: str,
        message: Optional[str] = None,
        unit: Optional[str] = "files",
    ):
        def _cb(evt) -> None:
            async def _emit() -> None:
                try:
                    processed = float(evt.processed_files)
                except Exception:
                    processed = None
                try:
                    total_files = float(evt.total_files)
                except Exception:
                    total_files = None

                mode = manager_proto.ProgressMode.INDETERMINATE
                bar_type = manager_proto.ProgressBarType.PULSE
                total = None
                if total_files is not None and total_files > 0:
                    total = total_files
                    mode = manager_proto.ProgressMode.DETERMINISTIC
                    bar_type = manager_proto.ProgressBarType.BAR

                try:
                    repo_id = str(evt.repo_id)
                except Exception:
                    repo_id = None

                label = None
                if repo_id:
                    label = self._repo_label_by_id.get(repo_id)
                    if not label:
                        try:
                            repo_list = (
                                await self._project.know.pm.data.repo.get_by_ids(
                                    [repo_id]
                                )
                            )
                        except Exception:
                            repo_list = None
                        if repo_list:
                            repo = repo_list[0]
                            root = repo.root_path or ""
                            if root:
                                label = f"{repo.name} ({root})"
                            else:
                                label = repo.name
                            self._repo_label_by_id[repo_id] = label

                resolved_message = message
                if resolved_message is None:
                    if label:
                        resolved_message = label
                    elif repo_id is not None:
                        resolved_message = repo_id

                await self._progress_emitter.emit_update(
                    progress_id=progress_id,
                    title=title,
                    message=resolved_message,
                    mode=mode,
                    bar_type=bar_type,
                    completed=processed,
                    total=total,
                    unit=unit,
                    done=(
                        True
                        if (
                            processed is not None
                            and total is not None
                            and processed >= total
                        )
                        else None
                    ),
                )

            asyncio.create_task(_emit())

        return _cb
