import pytest

from vocode import models, state
from vocode.history.manager import HistoryManager
from vocode.runner.base import ExecutorInput
from vocode.runner.executors.exec_agent import RunAgentExecutor, RunAgentNode
from tests.stub_project import StubProject


@pytest.mark.asyncio
async def test_run_agent_executor_flow() -> None:
    history = HistoryManager()
    node = RunAgentNode(
        name="agent_node",
        type="run_agent",
        workflow="child_workflow",
        initial_text="Hello Child",
        outcomes=[models.OutcomeSlot(name="default")],
    )

    project = StubProject()
    executor = RunAgentExecutor(config=node, project=project)  # type: ignore

    run = state.WorkflowExecution(workflow_name="parent_workflow")
    execution = history.create_node_execution(
        run,
        node=node.name,
        status=state.RunStatus.RUNNING,
    )
    inp = ExecutorInput(execution=execution, run=run)

    steps: list[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert len(steps) == 1
    step1 = steps[0]
    assert step1.type == state.StepType.WORKFLOW_REQUEST
    assert step1.message is not None
    assert step1.message.text == "Hello Child"
    assert step1.is_complete is True

    history.upsert_step(run, step1)

    steps_retry: list[state.Step] = []
    async for step in executor.run(inp):
        steps_retry.append(step)

    assert len(steps_retry) == 1
    step2 = steps_retry[0]
    assert step2.type == state.StepType.WORKFLOW_REQUEST
    assert step2.message is not None
    assert step2.message.text == "Hello Child"

    result_msg = state.Message(
        role=models.Role.ASSISTANT,
        text="Child Result",
    )
    history.add_message(run, result_msg)
    result_step = history.create_step(
        run,
        execution_id=execution.id,
        type=state.StepType.WORKFLOW_RESULT,
        message_id=result_msg.id,
        is_complete=True,
    )

    steps_final: list[state.Step] = []
    async for step in executor.run(inp):
        steps_final.append(step)

    assert len(steps_final) == 1
    final_step = steps_final[0]
    assert final_step.type == state.StepType.OUTPUT_MESSAGE
    assert final_step.message is not None
    assert final_step.message.text == "Child Result"
    assert final_step.is_complete is True
    assert final_step.is_final is True
