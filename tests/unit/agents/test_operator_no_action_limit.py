"""真实 OperatorNode 在连续无可执行动作时必须终止，并保留普通回复（移植自上游 PR #226）。

此处只按错误信息匹配 RuntimeError，不依赖守卫模块本身，用于从图节点层面验证行为。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage
import pytest

from artemis.agents.operator.operator import OperatorNode
from artemis.config.agent import AgentGlobalConfig, MemoryTranscriptConfig
from artemis.context import ArtemisContext
from artemis.graph.state import State


@pytest.fixture
def fake_operator(monkeypatch):
    ctx = MagicMock(spec=ArtemisContext)
    ctx.execution_setup = None
    ctx.data_engine = None
    ctx.trace_id = "unit-progress-guard"
    ctx.actuator = None
    ctx.transcript_ledger = None
    monkeypatch.setattr("artemis.config.load_agent_config", lambda: AgentGlobalConfig())
    node = OperatorNode(ctx, transcript_config=MemoryTranscriptConfig(enabled=False))
    node._get_history_and_plan = lambda: ([], "- [/] Inspect the page")
    node._read_task_plan_text = lambda: "- [/] Inspect the page"
    node._build_prompt = AsyncMock(side_effect=lambda *a, **k: [HumanMessage(content="Observe.")])
    node._stage_transcript_turn = MagicMock()
    registry = SimpleNamespace(active_task_summaries=lambda: [], pop_unnotified_finished=lambda: [])
    monkeypatch.setattr(
        "artemis.agents.operator.operator.get_adb_task_registry", lambda ctx: registry
    )
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    monkeypatch.setattr("artemis.agents.operator.operator.get_llm", lambda **kwargs: llm)
    state = State.initial("Inspect a mock page")
    state.latest_ui_hierarchy = []
    state.operator_raw_data = {
        "screenshot_b64": "AA==",
        "xml_hierarchy": [],
        "width": 100,
        "height": 200,
    }
    return node, state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content", ["I cannot proceed without the required input.", "[]", "null", ""]
)
async def test_actual_operator_repeated_plain_replies_fail_and_keep_feedback(
    fake_operator, monkeypatch, content
):
    """连续普通回复（拒答、请求输入、空内容）达到上限后失败，且回复保留在转录中。"""
    node, state = fake_operator
    response = AIMessage(content=content)
    completion = AsyncMock(return_value=response)
    monkeypatch.setattr("artemis.agents.operator.operator.acomplete", completion)
    for _ in range(3):
        result = await node(state)
        assert result["structured_decisions"] is None
        assert response in node._stage_transcript_turn.call_args.args[0]
    with pytest.raises(RuntimeError, match="operator_no_action_limit.*task is not successful"):
        await node(state)
    # 没有隐藏的强制工具重试
    assert completion.await_count == 4
    assert response in node._stage_transcript_turn.call_args.args[0]


@pytest.mark.asyncio
async def test_cancelled_or_pending_turn_does_not_consume_guard_budget(fake_operator, monkeypatch):
    """被取消或仍在等待模型的轮次不消耗预算。"""
    node, state = fake_operator
    pause = asyncio.Event()

    async def pending_model(*args, **kwargs):
        await pause.wait()
        return AIMessage(content="")

    monkeypatch.setattr("artemis.agents.operator.operator.acomplete", pending_model)
    task = asyncio.create_task(node(state))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # 取消后再连续空转 3 轮仍未达到默认上限 4
    monkeypatch.setattr(
        "artemis.agents.operator.operator.acomplete", AsyncMock(return_value=AIMessage(content=""))
    )
    for _ in range(3):
        await node(state)


@pytest.mark.asyncio
async def test_helper_only_tool_budget_is_also_bounded_across_graph_turns(
    fake_operator, monkeypatch
):
    """只调用辅助工具耗尽单轮预算的轮次也跨图轮次计数。"""
    node, state = fake_operator
    node._invoke_llm_loop = AsyncMock(return_value=(None, None, None, True))
    for _ in range(3):
        result = await node(state)
        assert result["operator_tool_limit_exceeded"] is True
    with pytest.raises(RuntimeError, match="operator_no_action_limit"):
        await node(state)
