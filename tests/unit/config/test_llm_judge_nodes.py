"""nodes 下声明的可选评审节点在配置展开后必须保留（移植自上游 PR #222）。"""

from artemis.config.llm import LLMConfig, _expand_default_into_nodes, lightweight_judge_default

_DEFAULT = {
    "provider": "openai",
    "model": "gpt-main",
    "fallback": {"provider": "openai", "model": "gpt-fallback"},
}


def _parse(nodes: dict) -> LLMConfig:
    return LLMConfig.model_validate(
        _expand_default_into_nodes({"default": _DEFAULT, "nodes": nodes})
    )


def test_declared_judge_nodes_are_kept():
    cfg = _parse(
        {
            "validator_pixel_safety_net": {"model": "gpt-judge", "temperature": 0.0},
            "planner_validation": {"model": "gpt-judge", "temperature": 0.0},
        }
    )

    for node in ("validator_pixel_safety_net", "planner_validation"):
        judge = cfg.get_agent(node)
        assert judge.provider == "openai"
        assert judge.model == "gpt-judge"
        assert judge.temperature == 0.0
        # 未声明的字段继承 default，包括 fallback。
        assert judge.fallback.model == "gpt-fallback"


def test_declared_judge_node_fallback_is_merged_over_default():
    """只覆盖 fallback 的部分字段时，其余字段仍继承 default 的 fallback。"""
    cfg = _parse({"planner_validation": {"fallback": {"model": "gpt-judge-fallback"}}})

    judge = cfg.get_agent("planner_validation")
    assert judge.model == "gpt-main"
    assert judge.fallback.provider == "openai"
    assert judge.fallback.model == "gpt-judge-fallback"


def test_undeclared_judge_nodes_fall_back_to_the_lightweight_default():
    cfg = _parse({})

    assert cfg.validator_pixel_safety_net is None
    assert cfg.planner_validation is None
    assert cfg.get_agent("planner_validation") == lightweight_judge_default()
