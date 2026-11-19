"""连续无进展轮次保护的配置默认值（移植自上游 PR #217）。"""

import pytest
from pydantic import ValidationError

from artemis.config.agent import FlashProfileConfig


def test_flash_max_silent_turns_defaults_to_five():
    """Flash 默认连续 5 轮无工具调用即终止。"""
    assert FlashProfileConfig().max_silent_turns == 5


def test_flash_max_silent_turns_accepts_zero_and_rejects_negative():
    """0 表示关闭保护，负数非法。"""
    assert FlashProfileConfig(max_silent_turns=0).max_silent_turns == 0
    with pytest.raises(ValidationError):
        FlashProfileConfig(max_silent_turns=-1)
