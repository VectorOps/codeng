import typing
from enum import Enum
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from vocode import state
from vocode import settings as vocode_settings
from vocode.lib.date import utcnow
from . import proto as runner_proto


class ToolCallExecutionMode(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


class ToolRoundRequest(BaseModel):
    workflow_execution: state.WorkflowExecution
    node_execution: state.NodeExecution
    assistant_step: state.Step
    assistant_message: state.Message
    tool_calls: list[state.ToolCallReq]
    llm_usage: typing.Optional[state.LLMUsageStats] = None


class ToolCallPlanItem(BaseModel):
    request: state.ToolCallReq
    tool_spec: typing.Optional[vocode_settings.ToolSpec] = None
    tool_step: state.Step
    auto_approved: bool = False
    execution_mode: ToolCallExecutionMode = Field(
        default=ToolCallExecutionMode.PARALLEL
    )


class ToolRoundResult(BaseModel):
    assistant_step: state.Step
    assistant_message: state.Message
    tool_steps: list[state.Step]
    tool_responses: list[state.ToolCallResp]


class ToolCallOrchestrator:
    def __init__(
        self,
        get_tool_spec_for_request: Callable[
            [state.ToolCallReq], typing.Optional[vocode_settings.ToolSpec]
        ],
        is_tool_call_auto_approved: Callable[[state.ToolCallReq], bool],
        create_tool_prompt_step: Callable[
            [
                state.NodeExecution,
                state.ToolCallReq,
                typing.Optional[state.LLMUsageStats],
            ],
            state.Step,
        ],
    ) -> None:
        self._get_tool_spec_for_request = get_tool_spec_for_request
        self._is_tool_call_auto_approved = is_tool_call_auto_approved
        self._create_tool_prompt_step = create_tool_prompt_step

    def build_plan(self, req: ToolRoundRequest) -> list[ToolCallPlanItem]:
        items: list[ToolCallPlanItem] = []
        for tool_req in req.tool_calls:
            tool_spec = self._get_tool_spec_for_request(tool_req)
            auto_approved = self._is_tool_call_auto_approved(tool_req)
            if auto_approved:
                tool_req.auto_approved = True
                tool_req.status = state.ToolCallReqStatus.PENDING_EXECUTION
            else:
                tool_req.status = state.ToolCallReqStatus.REQUIRES_CONFIRMATION
            tool_step = self._create_tool_prompt_step(
                req.node_execution,
                tool_req,
                req.llm_usage,
            )
            items.append(
                ToolCallPlanItem(
                    request=tool_req,
                    tool_spec=tool_spec,
                    tool_step=tool_step,
                    auto_approved=auto_approved,
                    execution_mode=self._resolve_execution_mode(tool_spec),
                )
            )
        return items

    def process_approval_response(
        self,
        item: ToolCallPlanItem,
        response_step: typing.Optional[state.Step],
        tool_responses: list[state.ToolCallResp],
    ) -> bool:
        req = item.request
        if response_step is None:
            return False
        if response_step.type == state.StepType.APPROVAL:
            req.status = state.ToolCallReqStatus.PENDING_EXECUTION
            return True
        if response_step.type == state.StepType.REJECTION:
            user_text = ""
            if response_step.message is not None:
                user_text = response_step.message.text.strip()
            rejection_text = (
                "The tool call was rejected by the user. "
                f"User provided reason: {user_text}"
            )
            tool_responses.append(
                state.ToolCallResp(
                    id=req.id,
                    status=state.ToolCallStatus.REJECTED,
                    name=req.name,
                    result={"message": rejection_text},
                )
            )
            req.status = state.ToolCallReqStatus.REJECTED
            item.tool_step.is_final = True
            return True
        return False

    def mark_executing(self, items: list[ToolCallPlanItem]) -> list[state.Step]:
        steps: list[state.Step] = []
        for item in items:
            item.request.status = state.ToolCallReqStatus.EXECUTING
            steps.append(item.tool_step)
        return steps

    async def execute_plan(
        self,
        items: list[ToolCallPlanItem],
        execute_approved_tool_calls: Callable[
            [list[state.ToolCallReq]], Awaitable[list[runner_proto.ToolExecResult]]
        ],
    ) -> list[runner_proto.ToolExecResult]:
        if not items:
            return []
        results: list[runner_proto.ToolExecResult] = []
        group: list[ToolCallPlanItem] = []
        current_mode: typing.Optional[ToolCallExecutionMode] = None
        for item in items:
            if current_mode is None:
                current_mode = item.execution_mode
            if item.execution_mode != current_mode:
                batch = await execute_approved_tool_calls(
                    [group_item.request for group_item in group]
                )
                results.extend(batch)
                group = []
                current_mode = item.execution_mode
            group.append(item)
        if group:
            batch = await execute_approved_tool_calls(
                [group_item.request for group_item in group]
            )
            results.extend(batch)
        return results

    def mark_complete(self, items: list[ToolCallPlanItem]) -> list[state.Step]:
        steps: list[state.Step] = []
        for item in items:
            item.request.status = state.ToolCallReqStatus.COMPLETE
            item.request.handled_at = utcnow()
            item.tool_step.is_final = True
            steps.append(item.tool_step)
        return steps

    def finalize_round(
        self,
        req: ToolRoundRequest,
        plan: list[ToolCallPlanItem],
        tool_responses: list[state.ToolCallResp],
    ) -> ToolRoundResult:
        req.assistant_message.tool_call_responses = list(tool_responses)

        resp_by_id: dict[str, list[state.ToolCallResp]] = {}
        for response in tool_responses:
            existing = resp_by_id.get(response.id)
            if existing is None:
                existing = []
                resp_by_id[response.id] = existing
            existing.append(response)

        tool_steps: list[state.Step] = []
        for item in plan:
            per_req = resp_by_id.get(item.request.id)
            if per_req is None:
                continue
            tool_message = item.tool_step.message
            if tool_message is None:
                continue
            tool_message.tool_call_responses = list(per_req)
            tool_steps.append(item.tool_step)

        return ToolRoundResult(
            assistant_step=req.assistant_step,
            assistant_message=req.assistant_message,
            tool_steps=tool_steps,
            tool_responses=list(tool_responses),
        )

    def _resolve_execution_mode(
        self,
        tool_spec: typing.Optional[vocode_settings.ToolSpec],
    ) -> ToolCallExecutionMode:
        if tool_spec is None:
            return ToolCallExecutionMode.PARALLEL
        config = tool_spec.config
        if not isinstance(config, dict):
            return ToolCallExecutionMode.PARALLEL
        parallel = config.get("parallel")
        if parallel is False:
            return ToolCallExecutionMode.SEQUENTIAL
        return ToolCallExecutionMode.PARALLEL
