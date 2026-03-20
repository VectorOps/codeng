from __future__ import annotations

import gzip
from pathlib import Path

from vocode import state


def dumps_gzip(execution: state.WorkflowExecution) -> bytes:
    raw = execution.model_dump_json().encode("utf-8")
    return gzip.compress(raw)


def loads_gzip(data: bytes) -> state.WorkflowExecution:
    raw = gzip.decompress(data).decode("utf-8")
    execution = state.WorkflowExecution.model_validate_json(raw)
    return execution.attach_runtime_refs()


def save_to_path(path: Path, execution: state.WorkflowExecution) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dumps_gzip(execution)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def load_from_path(path: Path) -> state.WorkflowExecution:
    data = path.read_bytes()
    return loads_gzip(data)
