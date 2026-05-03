from __future__ import annotations

from enum import Enum
from typing import Annotated, Optional
from datetime import datetime
import typing
import uuid

from pydantic import BaseModel, Field, model_validator
from vocode import state
from vocode.runner import proto as runner_proto


class RunnerReqDisplayOpts(BaseModel):
    collapse: Optional[bool] = Field(default=None)
    collapse_lines: Optional[int] = Field(default=None)
    visible: Optional[bool] = Field(default=None)
    tool_collapse: Optional[bool] = Field(default=None)
    alert: Optional[bool] = Field(default=None)


class BasePacketKind(str, Enum):
    ACK = "ack"
    RUNNER_REQ = "runner_req"
    UI_STATE = "ui_state"
    STEP_DELETED = "step_deleted"
    BRANCH_CHANGED = "branch_changed"
    BRANCH_LIST = "branch_list"
    HISTORY_VIEW_DIFF = "history_view_diff"
    USER_INPUT = "user_input"
    INPUT_PROMPT = "input_prompt"
    STOP_REQ = "stop_req"
    AUTOCOMPLETE_REQ = "autocomplete_req"
    AUTOCOMPLETE_RESP = "autocomplete_resp"
    TEXT_MESSAGE = "text_message"
    LOG_REQ = "log_req"
    LOG_RESP = "log_resp"
    PROGRESS = "progress"


class AckPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.ACK] = Field(default=BasePacketKind.ACK)


class RunnerReqPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.RUNNER_REQ] = Field(
        default=BasePacketKind.RUNNER_REQ
    )
    workflow_id: str
    workflow_name: str
    workflow_execution_id: str
    step: state.Step
    input_required: bool = Field(default=False)
    display: Optional[RunnerReqDisplayOpts] = Field(default=None)


class StepDeletedPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.STEP_DELETED] = Field(
        default=BasePacketKind.STEP_DELETED
    )
    step_ids: list[str] = Field(default_factory=list)


class BranchSummary(BaseModel):
    branch_id: str
    head_step_id: Optional[str] = Field(default=None)
    base_step_id: Optional[str] = Field(default=None)
    label: Optional[str] = Field(default=None)
    created_at: datetime
    is_active: bool = Field(default=False)


class BranchChangedPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.BRANCH_CHANGED] = Field(
        default=BasePacketKind.BRANCH_CHANGED
    )
    workflow_execution_id: str
    active_branch_id: str
    created_branch_id: Optional[str] = Field(default=None)


class BranchListPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.BRANCH_LIST] = Field(
        default=BasePacketKind.BRANCH_LIST
    )
    workflow_execution_id: str
    branches: list[BranchSummary] = Field(default_factory=list)


class HistoryViewDiffPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.HISTORY_VIEW_DIFF] = Field(
        default=BasePacketKind.HISTORY_VIEW_DIFF
    )
    workflow_execution_id: str
    removed_step_ids: list[str] = Field(default_factory=list)
    upserted_step_ids: list[str] = Field(default_factory=list)


class UserInputPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.USER_INPUT] = Field(
        default=BasePacketKind.USER_INPUT
    )
    message: state.Message = Field(...)


class UIServerStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"


class InputPromptPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.INPUT_PROMPT] = Field(
        default=BasePacketKind.INPUT_PROMPT
    )
    title: Optional[str] = Field(default=None)
    subtitle: Optional[str] = Field(default=None)


class StopReqPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.STOP_REQ] = Field(
        default=BasePacketKind.STOP_REQ
    )


class RunnerStackFrame(BaseModel):
    workflow_name: str
    workflow_execution_id: str
    node_name: str
    status: state.RunnerStatus
    node_execution_id: Optional[str] = Field(default=None)


class UIServerStatePacket(BaseModel):
    kind: typing.Literal[BasePacketKind.UI_STATE] = Field(
        default=BasePacketKind.UI_STATE
    )
    status: UIServerStatus
    runners: list[RunnerStackFrame] = Field(default_factory=list)
    active_node_started_at: Optional[datetime] = Field(default=None)
    last_user_input_at: Optional[datetime] = Field(default=None)
    active_workflow_llm_usage: Optional[state.LLMUsageStats] = Field(default=None)
    last_step_llm_usage: Optional[state.LLMUsageStats] = Field(default=None)
    project_llm_usage: Optional[state.LLMUsageStats] = Field(default=None)


class AutocompleteReqPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.AUTOCOMPLETE_REQ] = Field(
        default=BasePacketKind.AUTOCOMPLETE_REQ
    )
    text: str
    row: int
    col: int


class AutocompleteRespPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.AUTOCOMPLETE_RESP] = Field(
        default=BasePacketKind.AUTOCOMPLETE_RESP
    )
    items: list["AutocompleteItem"] = Field(default_factory=list)


class AutocompleteItem(BaseModel):
    title: str
    replace_start: int
    replace_text: str
    insert_text: str


class TextMessageFormat(str, Enum):
    PLAIN = "plain"
    RICH_TEXT = "rich_text"
    MARKDOWN = "markdown"


class TextMessagePacket(BaseModel):
    kind: typing.Literal[BasePacketKind.TEXT_MESSAGE] = Field(
        default=BasePacketKind.TEXT_MESSAGE
    )
    text: str
    format: TextMessageFormat = Field(default=TextMessageFormat.PLAIN)


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LogEntry(BaseModel):
    index: int
    logger_name: str
    level: LogLevel
    level_name: str
    message: str
    created: float


class LogReqPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.LOG_REQ] = Field(default=BasePacketKind.LOG_REQ)
    offset: int = Field(default=0)
    limit: Optional[int] = Field(default=None)


class LogRespPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.LOG_RESP] = Field(
        default=BasePacketKind.LOG_RESP
    )
    offset: int
    total: int
    entries: list[LogEntry] = Field(default_factory=list)


class ProgressMode(str, Enum):
    DETERMINISTIC = "deterministic"
    INDETERMINATE = "indeterminate"


class ProgressBarType(str, Enum):
    BAR = "bar"
    SPINNER = "spinner"
    PULSE = "pulse"


class ProgressStatus(str, Enum):
    START = "start"
    UPDATE = "update"
    END = "end"


class ProgressOnComplete(str, Enum):
    HIDE = "hide"
    MESSAGE = "message"


class ProgressPacket(BaseModel):
    kind: typing.Literal[BasePacketKind.PROGRESS] = Field(
        default=BasePacketKind.PROGRESS
    )
    progress_id: Optional[str] = Field(default=None)
    status: ProgressStatus
    title: Optional[str] = Field(default=None)
    message: Optional[str] = Field(default=None)
    mode: ProgressMode = Field(default=ProgressMode.DETERMINISTIC)
    bar_type: ProgressBarType = Field(default=ProgressBarType.BAR)
    completed: Optional[float] = Field(default=None)
    total: Optional[float] = Field(default=None)
    unit: Optional[str] = Field(default=None)
    done: Optional[bool] = Field(default=None)
    on_complete: Optional[ProgressOnComplete] = Field(default=None)
    complete_message: Optional[str] = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def _ensure_progress_id(cls, data):
        if not isinstance(data, dict):
            return data
        pid = data.get("progress_id")
        if pid is None:
            data["progress_id"] = f"progress:{uuid.uuid4().hex}"
            return data
        if isinstance(pid, str) and pid.strip() == "":
            data["progress_id"] = f"progress:{uuid.uuid4().hex}"
        return data


BasePacket = Annotated[
    typing.Union[
        AckPacket,
        RunnerReqPacket,
        StepDeletedPacket,
        BranchChangedPacket,
        BranchListPacket,
        HistoryViewDiffPacket,
        UserInputPacket,
        InputPromptPacket,
        UIServerStatePacket,
        StopReqPacket,
        AutocompleteReqPacket,
        AutocompleteRespPacket,
        TextMessagePacket,
        LogReqPacket,
        LogRespPacket,
        ProgressPacket,
    ],
    Field(discriminator="kind"),
]


class BasePacketEnvelope(BaseModel):
    msg_id: int
    payload: BasePacket
    source_msg_id: Optional[int] = Field(default=None)
