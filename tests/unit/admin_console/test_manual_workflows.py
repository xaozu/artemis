"""物理触摸录制与参数化回放的核心行为验证。"""

import threading

import pytest
from pydantic import ValidationError

from apps.admin_console.services import manual_workflow_service as workflow_module
from apps.admin_console.services.manual_workflow_service import (
    ManualWorkflowService,
    TouchRecorder,
    WorkflowStep,
    WorkflowTemplate,
)


def _recorder_for_events() -> TouchRecorder:
    """仅构造事件解析状态，避免单元测试接管真实手机。"""
    recorder = object.__new__(TouchRecorder)
    recorder.max_x = 1079
    recorder.max_y = 2159
    recorder.steps = []
    recorder._mutex = threading.Lock()
    recorder._slot = 0
    recorder._x = recorder._y = None
    recorder._start_x = recorder._start_y = None
    recorder._start_at = recorder._last_end = None
    return recorder


def test_physical_touch_stream_preserves_tap_and_swipe_order():
    recorder = _recorder_for_events()
    lines = [
        "[ 10.000000] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID 00000001",
        "[ 10.001000] /dev/input/event2: EV_ABS ABS_MT_POSITION_X 00000100",
        "[ 10.002000] /dev/input/event2: EV_ABS ABS_MT_POSITION_Y 00000200",
        "[ 10.090000] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID ffffffff",
        "[ 11.000000] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID 00000002",
        "[ 11.001000] /dev/input/event2: EV_ABS ABS_MT_POSITION_X 00000100",
        "[ 11.002000] /dev/input/event2: EV_ABS ABS_MT_POSITION_Y 00000500",
        "[ 11.160000] /dev/input/event2: EV_ABS ABS_MT_POSITION_Y 00000100",
        "[ 11.200000] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID ffffffff",
    ]
    for line in lines:
        recorder._consume(line)

    assert [step.type for step in recorder.snapshot()] == ["tap", "swipe"]
    assert recorder.steps[0].x == pytest.approx(256 / 1079)
    assert recorder.steps[1].y2 == pytest.approx(256 / 2159)
    assert recorder.steps[1].delay_ms == 910


def test_workflow_step_rejects_unsafe_parameter_or_incomplete_touch():
    with pytest.raises(ValidationError):
        WorkflowStep(type="tap", x=0.5)
    with pytest.raises(ValidationError):
        WorkflowStep(type="text", parameter="$(bad)")


def test_parameterized_text_replay_uses_runtime_value_and_releases_lock(tmp_path, monkeypatch):
    service = ManualWorkflowService(tmp_path)
    template = WorkflowTemplate(
        name="搜索商品", device_serial="device-1", screen_width=1080, screen_height=2160,
        steps=[WorkflowStep(type="text", text="旧词", parameter="keyword")],
    )
    service._write(template)
    assert service.get_template(template.id).steps[0].parameter == "keyword"

    entered: list[str] = []

    class FakeDriver:
        def __init__(self, *_args):
            pass

        async def input_text(self, value, clear_existing=True):
            entered.append(value)
            return clear_existing

    class FakeLock:
        released = False

        def release(self):
            self.released = True

    monkeypatch.setattr(workflow_module, "_screen_size", lambda _serial: (1080, 2160))
    monkeypatch.setattr(workflow_module, "AdbClient", lambda **_kwargs: object())
    monkeypatch.setattr(workflow_module, "UIAutomatorClient", lambda **_kwargs: object())
    monkeypatch.setattr(workflow_module, "AndroidAdbDriver", FakeDriver)
    lock = FakeLock()
    status = {"id": "run-1", "status": "running", "step_index": 0, "step_count": 1, "error": None}
    service._replay(template, "device-1", {"keyword": "新关键词"}, lock, status, threading.Event())

    assert entered == ["新关键词"]
    assert status["status"] == "completed"
    assert lock.released is True
