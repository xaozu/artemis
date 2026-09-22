# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""记录实体手机触摸，并按用户修改后的步骤模板回放。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any
from uuid import uuid4

from adbutils import AdbClient
from PIL import Image
from pydantic import BaseModel, Field, field_validator, model_validator

from artemis.clients.ui_automator_client import UIAutomatorClient
from artemis.config import TRACES_PATH
from artemis.drivers.android.adb_driver import AndroidAdbDriver
from artemis.runtime.adb_endpoint import adb_command, current_adb_endpoint
from artemis.runtime.device_lock import DeviceBusyError, DeviceExecutionLock


class WorkflowStep(BaseModel):
    """单个固定动作；输入文本可在每次执行时替换。"""

    type: str
    delay_ms: int = Field(default=0, ge=0, le=30000)
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    x2: float | None = Field(default=None, ge=0, le=1)
    y2: float | None = Field(default=None, ge=0, le=1)
    duration_ms: int = Field(default=100, ge=1, le=10000)
    text: str = Field(default="", max_length=2000)
    parameter: str | None = Field(default=None, max_length=50)
    clear_existing: bool = True

    @model_validator(mode="after")
    def validate_action(self) -> WorkflowStep:
        if self.type not in {"tap", "long_press", "swipe", "text", "back", "home", "assert_text", "wait"}:
            raise ValueError("Unsupported workflow step")
        if self.type in {"tap", "long_press", "swipe"} and (self.x is None or self.y is None):
            raise ValueError("Touch steps require coordinates")
        if self.type == "swipe" and (self.x2 is None or self.y2 is None):
            raise ValueError("Swipe steps require end coordinates")
        if self.type == "assert_text" and not self.text.strip():
            raise ValueError("Text assertion cannot be empty")
        if self.parameter and (self.type != "text" or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", self.parameter)):
            raise ValueError("Parameter names must be letters, digits or underscores and start with a letter")
        return self


class WorkflowTemplate(BaseModel):
    """已保存的录制；触摸坐标按竖屏宽高归一化。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field(min_length=1, max_length=100)
    device_serial: str
    screen_width: int = Field(gt=0)
    screen_height: int = Field(gt=0)
    created_at: float = Field(default_factory=time.time)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=500)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Workflow name cannot be empty")
        return value


class TouchRecorder:
    """读取指定触摸屏的事件流，不接管用户的实体操作。"""

    _timestamp = re.compile(r"\[\s*([\d.]+)\]")
    _value = re.compile(r"(?:EV_ABS\s+)?(ABS_MT_[A-Z_]+|BTN_TOUCH)\s+([0-9a-fA-F]+|DOWN|UP)")

    def __init__(self, serial: str, path: str, max_x: int, max_y: int, lock: DeviceExecutionLock):
        self.id = uuid4().hex
        self.serial = serial
        self.path = path
        self.max_x = max_x
        self.max_y = max_y
        self.lock = lock
        self.width, self.height = _screen_size(serial)
        if self.width >= self.height:
            raise ValueError("Current recording supports portrait screen only")
        self.steps: list[WorkflowStep] = []
        self._mutex = threading.Lock()
        self._slot = 0
        self._x: int | None = None
        self._y: int | None = None
        self._start_x: int | None = None
        self._start_y: int | None = None
        self._start_at: float | None = None
        self._last_end: float | None = None
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self._expiry: threading.Timer | None = None

    def start(self) -> None:
        """开始读取指定设备的原始触摸事件。"""
        self.process = subprocess.Popen(
            adb_command(["-s", self.serial, "shell", "getevent", "-lt", self.path]),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        time.sleep(0.2)
        if self.process.poll() is not None:
            raise RuntimeError("Unable to read touchscreen events; check USB debugging permissions")
        self.thread = threading.Thread(target=self._read, name=f"touch-record-{self.id[:8]}", daemon=True)
        self.thread.start()
        # 浏览器意外关闭时也要自动释放设备，避免长期挡住自动化任务。
        self._expiry = threading.Timer(15 * 60, self.stop)
        self._expiry.daemon = True
        self._expiry.start()

    def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            self._consume(line)

    def _consume(self, line: str) -> None:
        match = self._value.search(line)
        if not match:
            return
        key, raw = match.groups()
        stamp = self._timestamp.search(line)
        when = float(stamp.group(1)) if stamp else time.monotonic()
        if key == "ABS_MT_SLOT":
            self._slot = int(raw, 16)
            return
        if self._slot != 0:
            return  # 多指手势不能简化成一条单指轨迹。
        if key == "ABS_MT_TRACKING_ID":
            if int(raw, 16) in {0xFFFFFFFF, 0xFFFF}:
                self._finish(when)
            else:
                self._start_at = when
                self._start_x = self._x = None
                self._start_y = self._y = None
        elif key == "ABS_MT_POSITION_X":
            self._x = int(raw, 16)
            if self._start_at is not None and self._start_x is None:
                self._start_x = self._x
        elif key == "ABS_MT_POSITION_Y":
            self._y = int(raw, 16)
            if self._start_at is not None and self._start_y is None:
                self._start_y = self._y
        elif key == "BTN_TOUCH":
            if raw == "DOWN" and self._start_at is None:
                self._start_at = when
            elif raw == "UP":
                self._finish(when)

    def _finish(self, when: float) -> None:
        if self._start_at is None or self._start_x is None or self._start_y is None:
            self._start_at = None
            return
        x2 = self._x if self._x is not None else self._start_x
        y2 = self._y if self._y is not None else self._start_y
        duration = max(1, int((when - self._start_at) * 1000))
        distance = ((x2 - self._start_x) ** 2 + (y2 - self._start_y) ** 2) ** 0.5
        delay = 0 if self._last_end is None else min(30000, max(0, int((self._start_at - self._last_end) * 1000)))
        common = {
            "x": min(1, max(0, self._start_x / max(1, self.max_x))),
            "y": min(1, max(0, self._start_y / max(1, self.max_y))),
            "duration_ms": min(10000, duration),
            "delay_ms": delay,
        }
        if distance >= max(self.max_x, self.max_y) * 0.015:
            step = WorkflowStep(
                type="swipe",
                x2=min(1, max(0, x2 / max(1, self.max_x))),
                y2=min(1, max(0, y2 / max(1, self.max_y))),
                **common,
            )
        else:
            step = WorkflowStep(type="long_press" if duration >= 500 else "tap", **common)
        with self._mutex:
            self.steps.append(step)
        self._last_end = when
        self._start_at = None

    def snapshot(self) -> list[WorkflowStep]:
        """录制线程仍可能追加动作，因此返回当前步骤的副本。"""
        with self._mutex:
            return list(self.steps)

    def stop(self) -> None:
        """即使 ADB 已断开，也停止读取并释放设备占用。"""
        if self._expiry:
            self._expiry.cancel()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.thread:
            self.thread.join(timeout=1)
        self.lock.release()


def _screen_size(serial: str) -> tuple[int, int]:
    """用截图尺寸计算触摸比例，供回放时还原坐标。"""
    from io import BytesIO

    result = subprocess.run(
        adb_command(["-s", serial, "exec-out", "screencap", "-p"]),
        capture_output=True,
        timeout=15,
        check=True,
    )
    with Image.open(BytesIO(result.stdout)) as image:
        return image.size


def _touchscreen(serial: str) -> tuple[str, int, int]:
    """找出实体触摸屏输入节点及其坐标范围。"""
    result = subprocess.run(
        adb_command(["-s", serial, "shell", "getevent", "-lp"]),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    for block in re.split(r"(?=add device \d+: )", result.stdout):
        node = re.search(r"add device \d+: (/dev/input/event\d+)", block)
        x = re.search(r"ABS_MT_POSITION_X\s*: value \d+, min \d+, max (\d+)", block)
        y = re.search(r"ABS_MT_POSITION_Y\s*: value \d+, min \d+, max (\d+)", block)
        if node and x and y and "INPUT_PROP_DIRECT" in block:
            return node.group(1), int(x.group(1)), int(y.group(1))
    raise RuntimeError("No readable physical touchscreen event device found")


class ManualWorkflowService:
    """管理正在录制的设备，并持久保存用户编辑后的模板。"""

    def __init__(self, directory: Path | None = None):
        self.directory = directory or (Path(TRACES_PATH) / "manual_workflows")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._recordings: dict[str, TouchRecorder] = {}
        self._runs: dict[str, dict[str, Any]] = {}
        self._run_cancel: dict[str, threading.Event] = {}
        self._mutex = threading.Lock()

    def start_recording(self, serial: str) -> dict[str, Any]:
        """录制时占用 ARTEMIS 共用的设备锁，防止任务同时操作手机。"""
        lock = DeviceExecutionLock(device_id=serial, description="Physical workflow recording", ingress="web")
        try:
            lock.acquire(blocking=False)
        except DeviceBusyError as exc:
            raise RuntimeError(str(exc)) from exc
        try:
            node, max_x, max_y = _touchscreen(serial)
            recording = TouchRecorder(serial, node, max_x, max_y, lock)
            recording.start()
        except Exception:
            lock.release()
            raise
        with self._mutex:
            self._recordings[recording.id] = recording
        return self.recording_state(recording.id)

    def recording_state(self, recording_id: str) -> dict[str, Any]:
        recording = self._recordings.get(recording_id)
        if recording is None:
            raise KeyError("Recording not found")
        if recording.process is not None and recording.process.poll() is not None:
            recording.stop()
        return {
            "id": recording.id,
            "device_serial": recording.serial,
            "screen_width": recording.width,
            "screen_height": recording.height,
            "active": recording.process is not None and recording.process.poll() is None,
            "steps": [step.model_dump() for step in recording.snapshot()],
        }

    def stop_recording(self, recording_id: str) -> dict[str, Any]:
        recording = self._recordings.get(recording_id)
        if recording is None:
            raise KeyError("Recording not found")
        recording.stop()
        return self.recording_state(recording_id)

    def discard_recording(self, recording_id: str) -> None:
        """丢弃未保存的录制并释放设备，以便重新开始。"""
        recording = self._recordings.pop(recording_id, None)
        if recording is None:
            raise KeyError("Recording not found")
        recording.stop()

    def save_recording(self, recording_id: str, name: str, steps: list[WorkflowStep]) -> WorkflowTemplate:
        """保存用户审阅的步骤，包括手动设置的输入参数。"""
        recording = self._recordings.get(recording_id)
        if recording is None:
            raise KeyError("Recording not found")
        if recording.process is not None and recording.process.poll() is None:
            raise ValueError("Stop recording before saving")
        template = WorkflowTemplate(
            name=name,
            device_serial=recording.serial,
            screen_width=recording.width,
            screen_height=recording.height,
            steps=steps,
        )
        self._write(template)
        self._recordings.pop(recording_id, None)
        return template

    def _path(self, template_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", template_id):
            raise KeyError("Workflow not found")
        return self.directory / f"{template_id}.json"

    def _write(self, template: WorkflowTemplate) -> None:
        path = self._path(template.id)
        temporary = path.with_name(f".{template.id}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(template.model_dump_json(indent=2), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def list_templates(self) -> list[WorkflowTemplate]:
        """列出有效模板；损坏的单个文件不影响其他模板。"""
        templates = []
        for path in self.directory.glob("*.json"):
            try:
                templates.append(WorkflowTemplate.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(templates, key=lambda item: item.created_at, reverse=True)

    def get_template(self, template_id: str) -> WorkflowTemplate:
        try:
            return WorkflowTemplate.model_validate_json(self._path(template_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise KeyError("Workflow not found") from exc

    def update_template(self, template_id: str, name: str, steps: list[WorkflowStep]) -> WorkflowTemplate:
        previous = self.get_template(template_id)
        updated = previous.model_copy(update={"name": name, "steps": steps})
        updated = WorkflowTemplate.model_validate(updated.model_dump())
        self._write(updated)
        return updated

    def delete_template(self, template_id: str) -> None:
        path = self._path(template_id)
        if not path.exists():
            raise KeyError("Workflow not found")
        path.unlink()

    def start_replay(self, template_id: str, serial: str, parameters: dict[str, str]) -> dict[str, Any]:
        """在后台按顺序执行模板，并提供当前步骤。"""
        template = self.get_template(template_id)
        expected = {step.parameter for step in template.steps if step.parameter}
        if set(parameters) - expected:
            raise ValueError("Unknown workflow parameter")
        lock = DeviceExecutionLock(device_id=serial, description=f"Workflow replay: {template.name}", ingress="web")
        try:
            lock.acquire(blocking=False)
        except DeviceBusyError as exc:
            raise RuntimeError(str(exc)) from exc
        run_id = uuid4().hex
        status: dict[str, Any] = {"id": run_id, "status": "running", "step_index": 0, "step_count": len(template.steps), "error": None}
        cancel = threading.Event()
        with self._mutex:
            self._runs[run_id] = status
            self._run_cancel[run_id] = cancel
        thread = threading.Thread(
            target=self._replay, args=(template, serial, parameters, lock, status, cancel), daemon=True,
            name=f"workflow-replay-{run_id[:8]}",
        )
        thread.start()
        return dict(status)

    def run_state(self, run_id: str) -> dict[str, Any]:
        status = self._runs.get(run_id)
        if status is None:
            raise KeyError("Run not found")
        return dict(status)

    def cancel_replay(self, run_id: str) -> dict[str, Any]:
        """请求回放在下一步操作或等待前停止。"""
        cancel = self._run_cancel.get(run_id)
        if cancel is None:
            raise KeyError("Run not found")
        cancel.set()
        return self.run_state(run_id)

    def _replay(
        self,
        template: WorkflowTemplate,
        serial: str,
        parameters: dict[str, str],
        lock: DeviceExecutionLock,
        status: dict[str, Any],
        cancel: threading.Event,
    ) -> None:
        try:
            width, height = _screen_size(serial)
            if width >= height:
                raise RuntimeError("Replay requires portrait screen")
            endpoint = current_adb_endpoint()
            adb_client = AdbClient(host=endpoint.host, port=endpoint.port)
            ui_client = UIAutomatorClient(device_id=serial)
            driver = AndroidAdbDriver(serial, adb_client, ui_client, width, height)
            for index, step in enumerate(template.steps):
                if cancel.is_set():
                    status["status"] = "cancelled"
                    return
                status["step_index"] = index + 1
                if cancel.wait(step.delay_ms / 1000):
                    status["status"] = "cancelled"
                    return
                if step.type == "wait":
                    if cancel.wait(step.duration_ms / 1000):
                        status["status"] = "cancelled"
                        return
                    continue
                if step.type == "assert_text":
                    deadline = time.monotonic() + max(1, step.duration_ms / 1000)
                    while True:
                        if cancel.is_set():
                            status["status"] = "cancelled"
                            return
                        screen = ui_client.get_screen_data()
                        # 只核对实际可见的文字和无障碍描述，避免匹配到 XML 属性名。
                        if any(
                            step.text in str(element.get("text", ""))
                            or step.text in str(element.get("content-desc", ""))
                            for element in screen.elements
                        ):
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f"Step {index + 1}: screen text not found: {step.text}")
                        cancel.wait(0.5)
                    continue
                x = round((step.x or 0) * (width - 1))
                y = round((step.y or 0) * (height - 1))
                if step.type == "tap":
                    success = asyncio.run(driver.tap(x, y))
                elif step.type == "long_press":
                    success = asyncio.run(driver.long_press(x, y, step.duration_ms))
                elif step.type == "swipe":
                    success = asyncio.run(driver.swipe(x, y, round((step.x2 or 0) * (width - 1)), round((step.y2 or 0) * (height - 1)), step.duration_ms))
                elif step.type == "text":
                    value = parameters.get(step.parameter, step.text) if step.parameter else step.text
                    success = asyncio.run(driver.input_text(value, clear_existing=step.clear_existing))
                else:
                    success = asyncio.run(driver.press_key(step.type))
                if not success:
                    raise RuntimeError(f"Step {index + 1} failed: {step.type}")
            status["status"] = "completed"
        except Exception as exc:
            status["status"] = "failed"
            status["error"] = str(exc)
        finally:
            lock.release()
            self._run_cancel.pop(status["id"], None)


manual_workflow_service = ManualWorkflowService()
