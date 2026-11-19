"""目标检测必须使用 utils.object_detector 配置的模型（移植自上游 PR #158）。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.agents.object_detector import object_detector as od

_MODULE = "artemis.agents.object_detector.object_detector"


@pytest.mark.asyncio
async def test_detector_uses_the_utils_object_detector_model():
    """object_detector 属于 utils 节点，取模型时必须带 is_utils=True，否则配置不生效。"""
    detector_llm = MagicMock(name="object_detector_llm")
    get_llm = MagicMock(return_value=detector_llm)
    detect = AsyncMock(return_value=[])

    with patch(f"{_MODULE}.get_llm", get_llm), patch(f"{_MODULE}._detect_single_label", detect):
        await od._run_object_detection(MagicMock(), image_bytes=b"img", queries=["OK button"])

    get_llm.assert_called_once()
    assert get_llm.call_args.kwargs.get("name") == "object_detector"
    assert get_llm.call_args.kwargs.get("is_utils") is True
    assert detect.call_args.args[0] is detector_llm


@pytest.mark.asyncio
async def test_detector_falls_back_to_the_operator_model_with_a_warning():
    """检测模型不可用时回退到 operator 模型，并打 warning，不再静默吞掉。"""
    operator_llm = MagicMock(name="operator_llm")

    def fake_get_llm(ctx, *, name, **kwargs):
        if name == "object_detector":
            raise ValueError("detector model not configured")
        return operator_llm

    detect = AsyncMock(return_value=[])
    with (
        patch(f"{_MODULE}.get_llm", side_effect=fake_get_llm),
        patch(f"{_MODULE}._detect_single_label", detect),
        patch(f"{_MODULE}.logger") as logger,
    ):
        await od._run_object_detection(MagicMock(), image_bytes=b"img", queries=["OK button"])

    assert detect.call_args.args[0] is operator_llm
    logger.warning.assert_called_once()
    assert "operator" in logger.warning.call_args.args[0]
