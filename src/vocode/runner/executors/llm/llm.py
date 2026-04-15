from typing import AsyncIterator, List, Dict, Any, Optional
from typing import Final
from collections.abc import Awaitable
import asyncio
import json
import connect
from pydantic import BaseModel, Field

from vocode import state, models
from vocode.logger import logger
from . import helpers as llm_helpers
from .preprocessors import base as pre_base
from ... import base as runner_base


INCLUDED_STEP_TYPES: Final = (
    state.StepType.OUTPUT_MESSAGE,
    state.StepType.INPUT_MESSAGE,
)


def _min_deadline(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None:
        return b
    if b is None:
        return a
    return a if a <= b else b


class ToolCallProviderState(BaseModel):
    provider_meta: Dict[str, Any] = Field(default_factory=dict)
    protocol_state: Dict[str, Any] = Field(default_factory=dict)
    reasoning_signature: Optional[str] = None


class LLMStepState(BaseModel):
    provider_meta: Dict[str, Any] = Field(default_factory=dict)
    protocol_state: Dict[str, Any] = Field(default_factory=dict)
    reasoning_signature: Optional[str] = None


@runner_base.ExecutorFactory.register("llm")
class LLMExecutor(runner_base.BaseExecutor):
    async def init(self):
        return None

    async def _ensure_provider_authorization(self) -> Optional[str]:
        provider = None
        if "/" in self.config.model:
            provider, _, _ = self.config.model.partition("/")
        if provider != "chatgpt":
            return None
        has_auth = await self.project.credentials.has_active_authorization(provider)
        if has_auth:
            return None
        return (
            "Missing authorization for chatgpt. "
            "Run /auth login chatgpt to authenticate or provide environment credentials."
        )

    def _is_tool_round_limit_reached(
        self,
        execution: state.NodeExecution,
    ) -> bool:
        if self.config.max_rounds == 0:
            return False

        llm_output_steps = 0
        for step in execution.iter_steps():
            if step.type != state.StepType.OUTPUT_MESSAGE:
                continue
            if not step.is_complete:
                continue
            if step.message is None:
                continue
            if not step.message.tool_call_requests:
                continue
            llm_output_steps += 1

        return llm_output_steps >= self.config.max_rounds

    def _iter_prompt_messages(
        self,
        inp: runner_base.ExecutorInput,
    ) -> List[tuple[state.Message, Optional[state.Step]]]:
        execution = inp.execution
        return list(runner_base.iter_execution_messages(execution))

    def build_connect_messages(
        self,
        inp: runner_base.ExecutorInput,
    ) -> tuple[Optional[str], List[connect.Message]]:
        cfg = self.config

        collected_messages: List[state.Message] = []
        message_steps: Dict[str, Optional[state.Step]] = {}

        system_prompt = llm_helpers.build_system_prompt(cfg)
        if system_prompt:
            system_message = state.Message(
                role=models.Role.SYSTEM,
                text=system_prompt,
            )
            collected_messages.append(system_message)
            message_steps[str(system_message.id)] = None

        for msg, step in self._iter_prompt_messages(inp):
            if step is not None and step.type not in INCLUDED_STEP_TYPES:
                continue
            collected_messages.append(msg)
            message_steps[str(msg.id)] = step

        if cfg.preprocessors:
            processed_messages = pre_base.apply_preprocessors(
                cfg.preprocessors,
                self.project,
                collected_messages,
            )
        else:
            processed_messages = collected_messages

        connect_messages: List[connect.Message] = []
        final_system_prompt: Optional[str] = None

        for msg in processed_messages:
            if msg.role == models.Role.SYSTEM:
                if final_system_prompt is None:
                    final_system_prompt = msg.text
                continue
            step = message_steps.get(str(msg.id))
            connect_messages.extend(self._build_connect_messages_for_message(msg, step))

        if not connect_messages:
            for msg in inp.execution.input_messages:
                connect_messages.extend(
                    self._build_connect_messages_for_message(msg, None)
                )

        return final_system_prompt, connect_messages

    def _build_connect_messages_for_message(
        self,
        msg: state.Message,
        step: Optional[state.Step],
    ) -> List[connect.Message]:
        if msg.role == models.Role.USER:
            return [connect.UserMessage(content=msg.text)]

        if msg.role == models.Role.ASSISTANT:
            messages: List[connect.Message] = []
            content: List[connect.AssistantContentBlock] = []
            if msg.text:
                content.append(connect.TextBlock(text=msg.text))

            if msg.tool_call_requests:
                for req in msg.tool_call_requests:
                    annotations: Optional[Dict[str, Any]] = None
                    if req.state is not None and req.state.reasoning_signature:
                        annotations = {
                            "reasoning_signature": req.state.reasoning_signature,
                        }
                    provider_meta = {}
                    protocol_meta = {}
                    if req.state is not None:
                        provider_meta = dict(req.state.provider_meta)
                        protocol_meta = dict(req.state.protocol_state)
                    content.append(
                        connect.ToolCallBlock(
                            id=req.id,
                            name=req.name,
                            arguments=dict(req.arguments or {}),
                            provider_meta=provider_meta,
                            protocol_meta=protocol_meta,
                            annotations=annotations,
                        )
                    )

            provider_meta: Dict[str, Any] = {}
            protocol_meta: Dict[str, Any] = {}
            if (
                step is not None
                and step.state is not None
                and isinstance(step.state, LLMStepState)
            ):
                provider_meta = dict(step.state.provider_meta)
                protocol_meta = dict(step.state.protocol_state)

            if content:
                messages.append(
                    connect.AssistantMessage(
                        content=content,
                        provider_meta=provider_meta,
                        protocol_meta=protocol_meta,
                    )
                )

            for resp in msg.tool_call_responses:
                result_text = ""
                if resp.result is not None:
                    result_text = json.dumps(resp.result)
                messages.append(
                    connect.ToolResultMessage(
                        tool_call_id=resp.id,
                        tool_name=resp.name,
                        content=[connect.TextBlock(text=result_text)],
                    )
                )

            return messages

        tool_messages: List[connect.Message] = []
        for resp in msg.tool_call_responses:
            result_text = ""
            if resp.result is not None:
                result_text = json.dumps(resp.result)
            tool_messages.append(
                connect.ToolResultMessage(
                    tool_call_id=resp.id,
                    tool_name=resp.name,
                    content=[connect.TextBlock(text=result_text)],
                )
            )
        return tool_messages

    def _build_step_from_message(
        self,
        base_step: state.Step,
        *,
        role: models.Role,
        step_type: state.StepType,
        text: str,
        usage: Optional[state.LLMUsageStats] = None,
        tool_call_requests: Optional[List[state.ToolCallReq]] = None,
        tool_call_responses: Optional[List[state.ToolCallResp]] = None,
        is_complete: bool = False,
        outcome_name: Optional[str] = None,
        step_state: Optional[BaseModel] = None,
    ) -> state.Step:
        workflow_execution = base_step._workflow_execution
        history = self.project.history
        message = base_step.message
        if message is None:
            message = state.Message(
                role=role,
                text=text,
                tool_call_requests=tool_call_requests or [],
                tool_call_responses=tool_call_responses or [],
            )
            if workflow_execution is not None:
                history.upsert_message(workflow_execution, message)
        else:
            message.role = role
            message.text = text
            message.tool_call_requests = tool_call_requests or []
            message.tool_call_responses = tool_call_responses or []
            if workflow_execution is not None:
                history.upsert_message(workflow_execution, message)

        update: Dict[str, Any] = {
            "type": step_type,
            "message_id": message.id,
            "is_complete": is_complete,
        }
        if usage is not None:
            update["llm_usage"] = usage
        if outcome_name is not None:
            update["outcome_name"] = outcome_name
        if step_state is not None:
            update["state"] = step_state

        return base_step.model_copy(update=update)

    async def _build_connect_tools(
        self, effective_specs
    ) -> Optional[List[connect.ToolSpec]]:
        cfg = self.config
        if not effective_specs:
            return None

        tools: List[connect.ToolSpec] = []
        project_tools = getattr(self.project, "tools", {}) or {}

        for spec in cfg.tools or []:
            tool_name = spec.name
            eff_spec = effective_specs.get(tool_name)
            if eff_spec is None or not eff_spec.enabled:
                continue
            tool = project_tools.get(tool_name)
            if tool is None:
                continue
            function_schema = await tool.openapi_spec(eff_spec)
            input_schema = function_schema.get("parameters") or function_schema.get(
                "input_schema"
            )
            if not isinstance(input_schema, dict):
                continue
            description = function_schema.get("description") or tool_name
            tools.append(
                connect.ToolSpec.external(
                    name=function_schema.get("name") or tool_name,
                    description=description,
                    input_schema=input_schema,
                )
            )

        return tools or None

    def _copy_metadata(
        self,
        provider_meta: Optional[Dict[str, Any]],
        protocol_state: Optional[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        copied_provider_meta: Dict[str, Any] = {}
        copied_protocol_state: Dict[str, Any] = {}
        if provider_meta:
            copied_provider_meta = dict(provider_meta)
        if protocol_state:
            copied_protocol_state = dict(protocol_state)
        return copied_provider_meta, copied_protocol_state

    def _format_connect_error_message(self, error: connect.ConnectError) -> str:
        error_info = error.error
        parts = [f"LLM error: {error_info.message}"]
        details: list[str] = []
        if error_info.provider:
            details.append(f"provider={error_info.provider}")
        if error_info.api_family:
            details.append(f"api_family={error_info.api_family}")
        if error_info.status_code is not None:
            details.append(f"status_code={error_info.status_code}")
        if error_info.code:
            details.append(f"code={error_info.code}")
        details.append(f"retryable={error_info.retryable}")
        if details:
            parts.append(" ".join(details))
        if error_info.raw is not None:
            try:
                raw_text = json.dumps(
                    error_info.raw, ensure_ascii=False, sort_keys=True, default=str
                )
            except Exception:
                raw_text = str(error_info.raw)
            if raw_text:
                parts.append(f"raw={raw_text}")
        return "\n".join(parts)

    async def run(self, inp: runner_base.ExecutorInput) -> AsyncIterator[state.Step]:
        cfg = self.config
        auth_error = await self._ensure_provider_authorization()
        if auth_error is not None:
            error_step = self.project.history.upsert_step(
                inp.run,
                state.Step(
                    execution_id=inp.execution.id,
                    type=state.StepType.REJECTION,
                    is_complete=True,
                    workflow_execution=inp.run,
                ),
            )
            yield self._build_step_from_message(
                error_step,
                role=models.Role.SYSTEM,
                step_type=state.StepType.REJECTION,
                text=auth_error,
                is_complete=True,
            )
            return
        if self._is_tool_round_limit_reached(inp.execution):
            error_step = self.project.history.upsert_step(
                inp.run,
                state.Step(
                    execution_id=inp.execution.id,
                    type=state.StepType.REJECTION,
                    is_complete=True,
                    workflow_execution=inp.run,
                ),
            )
            yield self._build_step_from_message(
                error_step,
                role=models.Role.SYSTEM,
                step_type=state.StepType.REJECTION,
                text=(f"LLM node '{cfg.name}' exceeded max_rounds={cfg.max_rounds}"),
                is_complete=True,
            )
            return

        system_prompt, connect_messages = self.build_connect_messages(inp)
        effective_specs = llm_helpers.build_effective_tool_specs(self.project, cfg)
        tools = await self._build_connect_tools(effective_specs)
        outcome_names = llm_helpers.get_outcome_names(cfg)
        if (
            len(outcome_names) > 1
            and cfg.outcome_strategy == models.OutcomeStrategy.FUNCTION
        ):
            outcome_desc_bullets = llm_helpers.get_outcome_desc_bullets(cfg)
            outcome_choice_desc = llm_helpers.get_outcome_choice_desc(
                cfg,
                outcome_desc_bullets,
            )
            choose_tool = llm_helpers.build_choose_outcome_tool(
                outcome_names,
                outcome_desc_bullets,
                outcome_choice_desc,
            )
            if tools is None:
                tools = []
            choose_function = choose_tool["function"]
            tools.append(
                connect.ToolSpec.external(
                    name=choose_function["name"],
                    description=choose_function["description"],
                    input_schema=choose_function["parameters"],
                )
            )

        step = self.project.history.upsert_step(
            inp.run,
            state.Step(
                execution_id=inp.execution.id,
                type=state.StepType.OUTPUT_MESSAGE,
            ),
        )

        logger.debug(
            "LLM request",
            model=cfg.model,
            system_prompt=system_prompt,
            messages=connect_messages,
            tools=tools,
        )

        max_retries = 3
        attempt = 0
        assistant_partial = ""
        final_response: Optional[connect.AssistantMessage] = None

        while True:
            try:
                assistant_partial = ""
                final_response = None
                request = connect.GenerateRequest(
                    messages=connect_messages,
                    system_prompt=system_prompt,
                    tools=tools or [],
                    tool_choice="auto" if tools else None,
                    temperature=cfg.temperature,
                    max_output_tokens=cfg.max_tokens,
                    reasoning=(
                        connect.ReasoningConfig(effort=cfg.reasoning_effort)
                        if cfg.reasoning_effort is not None
                        else None
                    ),
                )
                options = connect.RequestOptions(
                    provider_options=dict(cfg.extra or {}),
                )

                loop = asyncio.get_running_loop()
                start_deadline: Optional[float] = None
                if cfg.start_timeout is not None:
                    start_deadline = loop.time() + float(cfg.start_timeout)
                response_deadline: Optional[float] = None
                if cfg.response_timeout is not None:
                    response_deadline = loop.time() + float(cfg.response_timeout)

                async def _await_with_deadline(
                    awaitable: Awaitable[Any], deadline: Optional[float]
                ) -> Any:
                    if deadline is None:
                        return await awaitable
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    return await asyncio.wait_for(awaitable, timeout=remaining)

                has_visible_content = False

                async with connect.AsyncLLMClient(
                    credential_manager=self.project.credentials
                ) as client:
                    stream_handle = client.stream(
                        cfg.model,
                        request,
                        options=options,
                    )
                    stream_iter = stream_handle.__aiter__()

                    def _effective_deadline() -> Optional[float]:
                        if has_visible_content:
                            return response_deadline
                        return _min_deadline(start_deadline, response_deadline)

                    while True:
                        try:
                            event = await _await_with_deadline(
                                stream_iter.__anext__(), _effective_deadline()
                            )
                        except StopAsyncIteration:
                            break

                        if event.type == "error":
                            raise connect.exception_from_error_info(event.error)

                        if event.type == "response_end":
                            final_response = event.response
                            break

                        if event.type == "text_delta" and event.delta:
                            if not has_visible_content:
                                has_visible_content = True
                            assistant_partial += event.delta
                            interim_step = self._build_step_from_message(
                                step,
                                role=models.Role.ASSISTANT,
                                step_type=state.StepType.OUTPUT_MESSAGE,
                                text=assistant_partial,
                            )
                            step = interim_step
                            yield interim_step
                            continue

                        if (
                            event.type == "text_end"
                            and event.text
                            and not assistant_partial
                        ):
                            if not has_visible_content:
                                has_visible_content = True
                            assistant_partial = event.text
                            interim_step = self._build_step_from_message(
                                step,
                                role=models.Role.ASSISTANT,
                                step_type=state.StepType.OUTPUT_MESSAGE,
                                text=assistant_partial,
                            )
                            step = interim_step
                            yield interim_step
                            continue

                        if event.type in {
                            "reasoning_delta",
                            "reasoning_end",
                            "tool_call_delta",
                            "tool_call_end",
                        }:
                            has_visible_content = True

                    if final_response is None:
                        final_response = await _await_with_deadline(
                            stream_handle.final_response(),
                            response_deadline,
                        )

                break
            except asyncio.TimeoutError as e:
                logger.error("LLM timeout", err=e)
                if attempt < max_retries:
                    attempt += 1
                    continue

                error_step = self._build_step_from_message(
                    step,
                    role=models.Role.SYSTEM,
                    step_type=state.StepType.REJECTION,
                    text="LLM timeout",
                    is_complete=True,
                )
                yield error_step
                return
            except connect.RateLimitError as e:
                logger.warning("LLM rate limit retry", exc=e)
                if attempt < max_retries:
                    attempt += 1
                    await asyncio.sleep(60)
                    continue

                error_step = self._build_step_from_message(
                    step,
                    role=models.Role.SYSTEM,
                    step_type=state.StepType.REJECTION,
                    text=f"LLM error: {e}",
                    is_complete=True,
                )
                yield error_step
                return
            except connect.ConnectError as e:
                if e.error.retryable and attempt < max_retries:
                    attempt += 1
                    await asyncio.sleep(1 * (2 ** (attempt - 1)))
                    logger.warning(
                        "LLM retry",
                        attempt=attempt,
                        max_retries=max_retries,
                        status_code=e.error.status_code,
                        err=str(e),
                    )
                    continue

                rejection_text = self._format_connect_error_message(e)
                logger.error(
                    "LLM error",
                    status_code=e.error.status_code,
                    code=e.error.code,
                    provider=e.error.provider,
                    api_family=e.error.api_family,
                    err=e,
                )

                error_step = self._build_step_from_message(
                    step,
                    role=models.Role.SYSTEM,
                    step_type=state.StepType.REJECTION,
                    text=rejection_text,
                    is_complete=True,
                )
                yield error_step
                return
            except Exception as e:
                logger.error("LLM error", err=e)

                error_step = self._build_step_from_message(
                    step,
                    role=models.Role.SYSTEM,
                    step_type=state.StepType.REJECTION,
                    text=f"LLM error: {e}",
                    is_complete=True,
                )
                yield error_step
                return

        if final_response is None:
            raise RuntimeError("LLM response missing final response")

        logger.debug("LLM response", response=final_response)

        # Prefer the streamed text as the final assistant message,
        # but capture the assembled content for comparison / fallback.
        response_text = "".join(
            block.text for block in final_response.content if block.type == "text"
        )

        # Debug comparison of streamed vs assembled text
        if assistant_partial != response_text:
            logger.info(
                "LLM message text comparison",
                streamed=assistant_partial,
                assembled=response_text,
            )

        # Use streamed text as canonical; fall back to assembled if stream was empty
        assistant_text = assistant_partial if assistant_partial else response_text

        outcome_name: Optional[str] = None
        tool_call_reqs: List[state.ToolCallReq] = []
        tool_calls_data = [
            block for block in final_response.content if block.type == "tool_call"
        ]
        response_provider_meta, response_protocol_state = self._copy_metadata(
            final_response.provider_meta,
            final_response.protocol_state,
        )
        step_state = LLMStepState(
            provider_meta=response_provider_meta,
            protocol_state=response_protocol_state,
            reasoning_signature=next(
                (
                    block.signature
                    for block in final_response.content
                    if block.type == "reasoning" and block.signature
                ),
                None,
            ),
        )

        if (
            len(outcome_names) > 1
            and cfg.outcome_strategy == models.OutcomeStrategy.TAG
        ):
            parsed_outcome = llm_helpers.parse_outcome_from_text(
                assistant_text,
                outcome_names,
            )
            if parsed_outcome:
                outcome_name = parsed_outcome
                assistant_text = llm_helpers.strip_outcome_line(assistant_text)

        for tc in tool_calls_data:
            tc_id = tc.id
            tc_type = "function"
            func_name = tc.name
            arguments = dict(tc.arguments)
            if (
                len(outcome_names) > 1
                and cfg.outcome_strategy == models.OutcomeStrategy.FUNCTION
                and func_name == llm_helpers.CHOOSE_OUTCOME_TOOL_NAME
            ):
                cand = arguments.get("outcome")
                if isinstance(cand, str) and cand in outcome_names:
                    outcome_name = cand
                continue

            tool_spec = effective_specs.get(func_name)
            tool_provider_meta, tool_protocol_state = self._copy_metadata(
                tc.provider_meta,
                tc.protocol_meta,
            )

            tool_call_reqs.append(
                state.ToolCallReq(
                    id=tc_id,
                    type=tc_type,
                    name=func_name,
                    arguments=arguments,
                    tool_spec=tool_spec,
                    state=ToolCallProviderState(
                        provider_meta=tool_provider_meta,
                        protocol_state=tool_protocol_state,
                        reasoning_signature=(
                            tc.annotations.get("reasoning_signature")
                            if isinstance(tc.annotations, dict)
                            else None
                        ),
                    ),
                )
            )

        # Collect usage stats
        usage_obj = final_response.usage
        prompt_tokens = int(usage_obj.input_tokens or 0) + int(
            usage_obj.cache_read_tokens or 0
        )
        completion_tokens = int(usage_obj.output_tokens or 0)

        round_cost = 0.0
        try:
            model_spec = connect.default_model_registry.resolve(cfg.model)
            cost_val = connect.estimate_cost(model_spec, usage_obj)
            if cost_val is not None:
                round_cost = float(cost_val.total_cost)
        except Exception:
            round_cost = 0.0

        model_input_token_limit = llm_helpers.resolve_model_token_limit(cfg)

        usage_stats = state.LLMUsageStats(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_dollars=round_cost,
            model_name=cfg.model,
            input_token_limit=model_input_token_limit,
            output_token_limit=cfg.max_tokens,
        )

        final_outcome_name = outcome_name if len(outcome_names) > 1 else None

        message_step = self._build_step_from_message(
            step,
            role=models.Role.ASSISTANT,
            step_type=state.StepType.OUTPUT_MESSAGE,
            text=assistant_text,
            usage=usage_stats,
            tool_call_requests=tool_call_reqs or None,
            is_complete=True,
            outcome_name=final_outcome_name,
            step_state=step_state,
        )
        yield message_step
