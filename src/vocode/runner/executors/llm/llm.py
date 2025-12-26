from typing import AsyncIterator, List, Dict, Any, Optional

import asyncio
import json
import litellm

from vocode import state, models
from vocode.logger import logger
from . import helpers as llm_helpers
from ...base import BaseExecutor, ExecutorInput


class LLMExecutor(BaseExecutor):
    def build_messages(self, inp: ExecutorInput) -> List[Dict]:
        """
        Generate an OpenAI-compatible conversation from execution state.
        """
        cfg = self.config
        execution = inp.execution

        messages: List[Dict[str, Any]] = []

        system_parts: List[str] = []
        if cfg.system:
            system_parts.append(cfg.system)
        if cfg.system_append:
            system_parts.append(cfg.system_append)
        outcome_names = llm_helpers.get_outcome_names(cfg)
        if len(outcome_names) > 1 and cfg.outcome_strategy == models.OutcomeStrategy.TAG:
            outcome_desc_bullets = llm_helpers.get_outcome_desc_bullets(cfg)
            tag_instruction = llm_helpers.build_tag_system_instruction(
                outcome_names,
                outcome_desc_bullets,
            )
            system_parts.append(tag_instruction)

        if system_parts:
            system_prompt = "\n\n".join(p for p in system_parts if p).strip()
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})

        def _append_message(msg: state.Message) -> None:
            role_value = msg.role.value
            base: Dict[str, Any] = {
                "role": role_value,
                "content": msg.text,
            }

            if msg.tool_call_requests:
                tool_calls: List[Dict[str, Any]] = []
                for req in msg.tool_call_requests:
                    tool_calls.append(
                        {
                            "id": req.id,
                            "type": req.type,
                            "function": {
                                "name": req.name,
                                "arguments": json.dumps(req.arguments or {}),
                            },
                        }
                    )
                if tool_calls:
                    base["tool_calls"] = tool_calls

            messages.append(base)

            if msg.tool_call_responses:
                for resp in msg.tool_call_responses:
                    tool_msg: Dict[str, Any] = {
                        "role": "tool",
                        "name": resp.name,
                        "content": "",
                    }
                    if resp.id is not None:
                        tool_msg["tool_call_id"] = resp.id
                    if resp.result is not None:
                        tool_msg["content"] = json.dumps(resp.result)
                    messages.append(tool_msg)

        for m in execution.input_messages:
            _append_message(m)

        for s in execution.steps:
            if s.message is not None:
                _append_message(s.message)

        return messages

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
    ) -> state.Step:
        message = state.Message(
            role=role,
            text=text,
            tool_call_requests=tool_call_requests or [],
            tool_call_responses=tool_call_responses or [],
        )

        update: Dict[str, Any] = {
            "type": step_type,
            "message": message,
        }
        if usage is not None:
            update["llm_usage"] = usage

        return base_step.model_copy(update=update)

    def _build_tools(self) -> Optional[List[Dict[str, Any]]]:
        cfg = self.config
        effective_specs = llm_helpers.build_effective_tool_specs(self.project, cfg)
        if not effective_specs:
            return None

        tools: List[Dict[str, Any]] = []
        for spec in effective_specs.values():
            if not spec.enabled:
                continue
            if not spec.config:
                continue
            tool_def = dict(spec.config)
            if tool_def:
                tools.append(tool_def)

        if not tools:
            return None
        return tools

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        cfg = self.config

        conv = self.build_messages(inp)
        tools = self._build_tools()
        outcome_names = llm_helpers.get_outcome_names(cfg)
        if len(outcome_names) > 1 and cfg.outcome_strategy == models.OutcomeStrategy.FUNCTION:
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
            tools.append(choose_tool)

        step = state.Step(
            execution=inp.execution,
            type=state.StepType.OUTPUT_MESSAGE,
        )

        extra_args = dict(cfg.extra or {})

        logger.debug("LLM request:", req=conv, tools=tools)

        max_retries = 3
        attempt = 0
        chunks: List[Any] = []

        while True:
            try:
                args = dict(extra_args)
                args.update(
                    {
                        "model": cfg.model,
                        "messages": conv,
                        "temperature": cfg.temperature,
                        "reasoning_effort": cfg.reasoning_effort,
                        "stream": True,
                        "stream_options": {
                            "include_usage": True,
                        },
                    }
                )
                if cfg.max_tokens is not None:
                    args["max_tokens"] = cfg.max_tokens
                if tools:
                    args.setdefault("tools", tools)
                    args.setdefault("tool_choice", "auto")

                completion_coro = litellm.acompletion(**args)

                task_name = f"llm.acompletion:{cfg.name}"
                stream_task = asyncio.create_task(completion_coro, name=task_name)
                stream = await stream_task

                chunks = []
                assistant_partial = ""

                async for chunk in stream:
                    chunks.append(chunk)
                    choice_list = chunk.choices
                    if not choice_list:
                        continue

                    choice0 = choice_list[0]
                    delta = choice0.delta
                    if not delta:
                        continue

                    content_piece = delta.content

                    if isinstance(content_piece, str) and content_piece:
                        assistant_partial += content_piece
                        interim_step = self._build_step_from_message(
                            step,
                            role=models.Role.ASSISTANT,
                            step_type=state.StepType.OUTPUT_MESSAGE,
                            text=assistant_partial,
                        )
                        yield interim_step

                break
            except Exception as e:
                status_code = None
                try:
                    status_code = e.status_code
                except Exception:
                    status_code = None

                try:
                    should_retry = bool(litellm._should_retry(status_code))
                except Exception:
                    should_retry = False

                if should_retry and attempt < max_retries:
                    attempt += 1
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

                    logger.warning(
                        "LLM retry",
                        attempt=attempt,
                        max_retries=max_retries,
                        status_code=status_code,
                        err=str(e),
                    )

                    continue

                logger.error("LLM error", status_code=status_code, err=e)

                error_step = self._build_step_from_message(
                    step,
                    role=models.Role.SYSTEM,
                    step_type=state.StepType.REJECTION,
                    text=f"LLM error: {e}",
                )
                yield error_step
                return

        response = litellm.stream_chunk_builder(
            chunks,
            messages=conv,
        )

        choices = response.choices
        if not choices:
            raise RuntimeError("LLM response missing choices")

        first_choice = choices[0]
        message_data = first_choice.message
        content = message_data.content

        assistant_text = ""
        if isinstance(content, str):
            assistant_text = content

        outcome_name: Optional[str] = None
        tool_call_reqs: List[state.ToolCallReq] = []
        tool_calls_data = message_data.tool_calls or []

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
            tc_type = tc.type
            func = tc.function

            func_name = func.name
            arguments_raw: str = "{}"
            raw = func.arguments
            if isinstance(raw, str):
                arguments_raw = raw

            try:
                arguments = json.loads(arguments_raw)
            except Exception:
                arguments = {}
            if (
                len(outcome_names) > 1
                and cfg.outcome_strategy == models.OutcomeStrategy.FUNCTION
                and func_name == llm_helpers.CHOOSE_OUTCOME_TOOL_NAME
            ):
                cand = arguments.get("outcome")
                if isinstance(cand, str) and cand in outcome_names:
                    outcome_name = cand
                continue

            tool_call_reqs.append(
                state.ToolCallReq(
                    id=tc_id,
                    type=tc_type,
                    name=func_name,
                    arguments=arguments,
                )
            )

        usage_obj = response.usage
        prompt_tokens = 0
        completion_tokens = 0
        if usage_obj is not None:
            prompt_tokens = int(usage_obj.prompt_tokens or 0)
            completion_tokens = int(usage_obj.completion_tokens or 0)

        usage_stats = state.LLMUsageStats(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        message_step = self._build_step_from_message(
            step,
            role=models.Role.ASSISTANT,
            step_type=state.StepType.OUTPUT_MESSAGE,
            text=assistant_text,
            usage=usage_stats,
            tool_call_requests=tool_call_reqs or None,
        )
        yield message_step

        final_outcome_name = outcome_name if len(outcome_names) > 1 else None

        completion_step = state.Step(
            execution=inp.execution,
            type=state.StepType.COMPLETION,
            message=None,
            outcome_name=final_outcome_name,
            state=None,
            llm_usage=usage_stats,
        )
        yield completion_step
