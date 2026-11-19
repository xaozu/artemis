"""限制 Operator 连续多轮未产出可执行决策（移植自上游 PR #226）。"""

from dataclasses import dataclass

from artemis.utils.plan_grammar import parse_plan


class OperatorNoActionLimitError(RuntimeError):
    """连续无可执行动作达到上限：属于非成功结果，绝不代表任务已完成。"""

    reason_code = "operator_no_action_limit"


@dataclass
class OperatorProgressGuard:
    """跨图轮次统计 Operator 连续无可执行动作的轮数。"""

    max_no_action_turns: int = 4
    consecutive_no_action_turns: int = 0

    def record_turn(
        self,
        actions: list[dict] | None,
        plan_text: str | None,
        *,
        user_stopped: bool = False,
    ) -> None:
        """记录一轮结果；连续无动作且计划未完成的轮数达到上限时抛出异常。"""
        if actions:
            self.consecutive_no_action_turns = 0
            return
        snapshot = parse_plan(plan_text or "")
        # 计划已完成时交给图的正常结算与断言流程；持续型任务没有用户停止信号不算完成
        if snapshot.all_top_level_done and (not snapshot.continuous_top_level or user_stopped):
            self.consecutive_no_action_turns = 0
            return
        self.consecutive_no_action_turns += 1
        if self.consecutive_no_action_turns >= self.max_no_action_turns:
            raise OperatorNoActionLimitError(
                "operator_no_action_limit: stopped after "
                f"{self.consecutive_no_action_turns} consecutive turns without an executable "
                "action or a completed plan. The task is not successful; review the last "
                "ordinary assistant response and any missing input or execution constraint."
            )


def configured_progress_guard() -> OperatorProgressGuard:
    """按 pro.execution.max_no_action_turns 构造守卫。"""
    from artemis.config import load_agent_config

    return OperatorProgressGuard(
        max_no_action_turns=load_agent_config().pro.execution.max_no_action_turns
    )
