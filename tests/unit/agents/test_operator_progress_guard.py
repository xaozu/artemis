"""Operator 连续无可执行动作轮次的上限守卫（移植自上游 PR #226）。

模型返回成功的 HTTP 响应不代表允许无限次空转：既无可执行动作、计划也未完成的轮次
跨轮累计，达到上限即以非成功结果终止。
"""

from pydantic import ValidationError
import pytest

from artemis.agents.operator.progress_guard import (
    OperatorNoActionLimitError,
    OperatorProgressGuard,
    configured_progress_guard,
)
from artemis.config.agent import AgentGlobalConfig, ExecutionConfig


@pytest.mark.parametrize("actions", [None, []])
def test_consecutive_no_decisions_are_bounded_across_turns(actions):
    """连续无决策轮次跨轮累计，达到上限时抛出 operator_no_action_limit。"""
    guard = OperatorProgressGuard(max_no_action_turns=4)
    for _ in range(3):
        guard.record_turn(actions, "- [/] Observe the page")
    with pytest.raises(OperatorNoActionLimitError, match="operator_no_action_limit"):
        guard.record_turn(actions, "- [/] Observe the page")
    assert guard.consecutive_no_action_turns == 4


def test_observation_then_action_resets_the_streak():
    """出现可执行动作即清零计数。"""
    guard = OperatorProgressGuard(max_no_action_turns=3)
    guard.record_turn(None, "- [/] Inspect the page")
    guard.record_turn([], "- [/] Inspect the page")
    guard.record_turn([{"action": "wait", "seconds": 1}], "- [/] Inspect the page")
    assert guard.consecutive_no_action_turns == 0
    guard.record_turn(None, "- [/] Inspect the page")
    assert guard.consecutive_no_action_turns == 1


def test_notes_and_unchanged_task_do_not_reset_empty_turn_budget():
    """只改子项笔记、顶层任务未完成时不能重置空转预算。"""
    guard = OperatorProgressGuard(max_no_action_turns=2)
    guard.record_turn(None, "- [/] Inspect the page\n  - [ ] Read another note")
    with pytest.raises(OperatorNoActionLimitError):
        guard.record_turn(None, "- [/] Inspect the page\n  - [ ] Read a different note")


@pytest.mark.parametrize("plan", [None, "", "- [!] Unable to proceed"])
def test_missing_or_blocked_plan_is_not_success(plan):
    """缺失或受阻的计划不算完成。"""
    with pytest.raises(OperatorNoActionLimitError):
        OperatorProgressGuard(max_no_action_turns=1).record_turn(None, plan)


def test_completed_plan_can_reach_existing_graph_settlement():
    """计划全部完成时不计数，交给图的正常结算流程。"""
    guard = OperatorProgressGuard(max_no_action_turns=2)
    guard.record_turn(None, "- [/] Observe the page")
    guard.record_turn(None, "- [x] Observe the page")
    assert guard.consecutive_no_action_turns == 0


def test_continuous_plan_requires_user_stop_to_bypass_guard():
    """持续型计划没有用户停止信号就不算完成。"""
    plan = "- [x] [Loop:continuous] Watch the page"
    with pytest.raises(OperatorNoActionLimitError):
        OperatorProgressGuard(max_no_action_turns=1).record_turn(None, plan)
    OperatorProgressGuard(max_no_action_turns=1).record_turn(None, plan, user_stopped=True)


def test_pro_execution_setting_configures_the_guard(monkeypatch):
    """pro.execution.max_no_action_turns 决定守卫阈值。"""
    config = AgentGlobalConfig.model_validate({"pro": {"execution": {"max_no_action_turns": 3}}})
    assert config.pro.execution.max_no_action_turns == 3
    monkeypatch.setattr("artemis.config.load_agent_config", lambda: config)
    assert configured_progress_guard().max_no_action_turns == 3


def test_default_no_action_limit_is_four(monkeypatch):
    """默认阈值为 4。"""
    config = AgentGlobalConfig()
    assert config.pro.execution.max_no_action_turns == 4
    monkeypatch.setattr("artemis.config.load_agent_config", lambda: config)
    assert configured_progress_guard().max_no_action_turns == 4


@pytest.mark.parametrize("value", [0, -1, 21])
def test_budget_cannot_be_disabled_or_grow_unbounded(value):
    """阈值不能关闭也不能无限放大（1..20）。"""
    with pytest.raises(ValidationError):
        ExecutionConfig(max_no_action_turns=value)
