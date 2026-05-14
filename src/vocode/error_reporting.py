from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
import pydantic

from vocode import ui_events


class ConfigLoadError(Exception):
    def __init__(
        self,
        *,
        config_path: Path,
        stage: str,
        message: str,
        source_path: Optional[Path] = None,
        details: Optional[str] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.config_path = config_path
        self.stage = stage
        self.message = message
        self.source_path = source_path
        self.details = details
        self.__cause__ = cause


class WorkflowValidationError(Exception):
    def __init__(
        self,
        *,
        workflow_name: str,
        message: str,
        details: Optional[str] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.workflow_name = workflow_name
        self.message = message
        self.details = details
        self.__cause__ = cause


def format_pydantic_validation_error(error: pydantic.ValidationError) -> str:
    lines: list[str] = []
    for item in error.errors():
        loc = item.get("loc", ())
        path = ".".join(str(part) for part in loc) if loc else "<root>"
        message = str(item.get("msg", "Invalid value"))
        lines.append(f"- {path}: {message}")
    return "\n".join(lines)


def build_config_load_error(
    *,
    config_path: Path,
    stage: str,
    error: BaseException,
    source_path: Optional[Path] = None,
) -> ConfigLoadError:
    if isinstance(error, ConfigLoadError):
        return error

    if isinstance(error, yaml.YAMLError):
        message = "Invalid YAML syntax"
        details = str(error)
        problem_mark = getattr(error, "problem_mark", None)
        if problem_mark is not None:
            line = int(problem_mark.line) + 1
            column = int(problem_mark.column) + 1
            details = f"line {line}, column {column}: {details}"
        return ConfigLoadError(
            config_path=config_path,
            stage=stage,
            message=message,
            source_path=source_path,
            details=details,
            cause=error,
        )

    if isinstance(error, pydantic.ValidationError):
        return ConfigLoadError(
            config_path=config_path,
            stage=stage,
            message="Configuration validation failed",
            source_path=source_path,
            details=format_pydantic_validation_error(error),
            cause=error,
        )

    return ConfigLoadError(
        config_path=config_path,
        stage=stage,
        message=str(error) or "Configuration loading failed",
        source_path=source_path,
        cause=error,
    )


def format_config_load_error_text(error: ConfigLoadError) -> str:
    lines = [f"Configuration error while {error.stage}: {error.message}"]
    lines.append(f"Config: {error.config_path}")
    if error.source_path is not None and error.source_path != error.config_path:
        lines.append(f"Source: {error.source_path}")
    if error.details:
        lines.append("")
        lines.append(error.details)
    return "\n".join(lines)


def build_config_load_ui_event(error: ConfigLoadError) -> ui_events.ProjectUIEvent:
    return ui_events.ProjectUIEvent(
        severity=ui_events.UIEventSeverity.ERROR,
        title="Configuration load failed",
        source=(str(error.source_path) if error.source_path is not None else None),
        message=error.message,
        details=format_config_load_error_text(error),
    )


def build_workflow_validation_error(
    workflow_name: str,
    error: BaseException,
) -> WorkflowValidationError:
    if isinstance(error, WorkflowValidationError):
        return error
    details: Optional[str] = None
    if isinstance(error, pydantic.ValidationError):
        details = format_pydantic_validation_error(error)
    return WorkflowValidationError(
        workflow_name=workflow_name,
        message=str(error) or f"Workflow '{workflow_name}' is invalid",
        details=details,
        cause=error,
    )


def build_workflow_validation_ui_event(
    error: WorkflowValidationError,
) -> ui_events.ProjectUIEvent:
    return ui_events.ProjectUIEvent(
        severity=ui_events.UIEventSeverity.ERROR,
        title="Workflow validation failed",
        source=error.workflow_name,
        message=f"Workflow '{error.workflow_name}' could not start: {error.message}",
        details=error.details,
    )
