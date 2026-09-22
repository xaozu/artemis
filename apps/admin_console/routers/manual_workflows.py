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

"""实体手机操作录制和步骤模板的 HTTP 接口。"""

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from artemis.runtime import device_pool

try:
    from admin_console.services.manual_workflow_service import WorkflowStep, manual_workflow_service
except ImportError:
    from apps.admin_console.services.manual_workflow_service import WorkflowStep, manual_workflow_service


router = APIRouter(prefix="/api/manual-workflows", tags=["manual-workflows"])


class StartRecordingRequest(BaseModel):
    device_serial: str = Field(min_length=1)


class SaveWorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=500)


class RunWorkflowRequest(BaseModel):
    device_serial: str = Field(min_length=1)
    parameters: dict[str, str] = Field(default_factory=dict)


async def _ensure_device(serial: str) -> None:
    """占用设备前先检查指定手机确实在线。"""
    devices = await device_pool.list_devices_async()
    if not any(device.serial == serial and device.state == "device" for device in devices):
        raise HTTPException(status_code=409, detail=f"Device {serial} is not connected and ready")


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


@router.get("")
async def list_workflows():
    """列出用户保存的步骤模板。"""
    return await asyncio.to_thread(manual_workflow_service.list_templates)


@router.post("/recordings")
async def start_recording(request: StartRecordingRequest):
    """开始记录指定手机的实体触摸。"""
    await _ensure_device(request.device_serial)
    try:
        return await asyncio.to_thread(manual_workflow_service.start_recording, request.device_serial)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/recordings/{recording_id}")
async def recording_state(recording_id: str):
    """用户继续操作手机时，返回最新动作。"""
    try:
        return manual_workflow_service.recording_state(recording_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/recordings/{recording_id}/stop")
async def stop_recording(recording_id: str):
    """停止录制，保留步骤供编辑和保存。"""
    try:
        return await asyncio.to_thread(manual_workflow_service.stop_recording, recording_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.delete("/recordings/{recording_id}")
async def discard_recording(recording_id: str):
    """丢弃录制草稿并释放设备。"""
    try:
        await asyncio.to_thread(manual_workflow_service.discard_recording, recording_id)
        return {"deleted": True}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/recordings/{recording_id}/save")
async def save_recording(recording_id: str, request: SaveWorkflowRequest):
    """保存修改后的步骤顺序和输入参数。"""
    try:
        return await asyncio.to_thread(
            manual_workflow_service.save_recording, recording_id, request.name, request.steps
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/{template_id}")
async def get_workflow(template_id: str):
    try:
        return await asyncio.to_thread(manual_workflow_service.get_template, template_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.put("/{template_id}")
async def update_workflow(template_id: str, request: SaveWorkflowRequest):
    """修改现有模板，保留原录制设备信息。"""
    try:
        return await asyncio.to_thread(
            manual_workflow_service.update_template, template_id, request.name, request.steps
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.delete("/{template_id}")
async def delete_workflow(template_id: str):
    try:
        await asyncio.to_thread(manual_workflow_service.delete_template, template_id)
        return {"deleted": True}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/{template_id}/runs")
async def run_workflow(template_id: str, request: RunWorkflowRequest):
    """占用设备后依次回放步骤。"""
    await _ensure_device(request.device_serial)
    try:
        return await asyncio.to_thread(
            manual_workflow_service.start_replay,
            template_id,
            request.device_serial,
            request.parameters,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/runs/{run_id}/status")
async def run_status(run_id: str):
    """返回当前执行步骤或首个失败原因。"""
    try:
        return manual_workflow_service.run_state(run_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    """在下一步操作前停止回放。"""
    try:
        return manual_workflow_service.cancel_replay(run_id)
    except Exception as exc:
        raise _http_error(exc) from exc
