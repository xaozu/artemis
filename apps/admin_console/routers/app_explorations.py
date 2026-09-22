"""App 分叉探索、角色凭据、模块回放与文档接口。"""

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from artemis.runtime import device_pool

try:
    from admin_console.services.app_exploration_service import app_exploration_service
except ImportError:
    from apps.admin_console.services.app_exploration_service import app_exploration_service


router = APIRouter(prefix="/api/app-explorations", tags=["app-explorations"])


class CreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    package: str = Field(min_length=3, max_length=200)
    device_serial: str = Field(min_length=1)
    max_depth: int = Field(default=4, ge=1, le=8)


class CredentialRequest(BaseModel):
    role: str = Field(min_length=1, max_length=50)
    username: str = Field(min_length=1, max_length=300)
    password: str = Field(min_length=1, max_length=300)


class ModuleRequest(BaseModel):
    module: str = Field(min_length=1, max_length=50)


class InputRequest(BaseModel):
    value: str = Field(min_length=1, max_length=300)


class RunRequest(BaseModel):
    role: str = Field(min_length=1, max_length=50)
    mode: str = Field(default="explore", pattern="^(explore|replay)$")
    module: str | None = Field(default=None, max_length=50)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(422, detail=str(exc))
    return HTTPException(409, detail=str(exc))


async def _device_ready(serial: str) -> None:
    devices = await device_pool.list_devices_async()
    if not any(device.serial == serial and device.state == "device" for device in devices):
        raise HTTPException(409, detail="设备未连接或未授权")


@router.get("")
async def list_tasks():
    """列出本机保存的探索树摘要和运行状态。"""
    return await asyncio.to_thread(app_exploration_service.list_tasks)


@router.post("")
async def create(request: CreateRequest):
    """在用户明确选定的在线设备上创建探索任务。"""
    await _device_ready(request.device_serial)
    try:
        return await asyncio.to_thread(
            app_exploration_service.create,
            request.name,
            request.package,
            request.device_serial,
            request.max_depth,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/{task_id}")
async def get(task_id: str):
    """读取已保存的页面和分支，不返回加密凭据。"""
    try:
        return await asyncio.to_thread(app_exploration_service.get, task_id)
    except Exception as exc:
        raise _error(exc) from exc


@router.put("/{task_id}/roles")
async def save_role(task_id: str, request: CredentialRequest):
    """密码只进入加密文件，响应中不包含输入值。"""
    try:
        return await asyncio.to_thread(
            app_exploration_service.save_role,
            task_id,
            request.role,
            request.username,
            request.password,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.put("/{task_id}/edges/{edge_id}/module")
async def update_module(task_id: str, edge_id: str, request: ModuleRequest):
    """保存人工调整后的分支模块。"""
    try:
        return await asyncio.to_thread(
            app_exploration_service.update_module, task_id, edge_id, request.module
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{task_id}/edges/{edge_id}/retry")
async def retry(task_id: str, edge_id: str):
    """把已补齐前提条件的受阻分支重新排队。"""
    try:
        return await asyncio.to_thread(app_exploration_service.resolve_blocker, task_id, edge_id)
    except Exception as exc:
        raise _error(exc) from exc


@router.put("/{task_id}/edges/{edge_id}/input")
async def set_input(task_id: str, edge_id: str, request: InputRequest):
    """为输入入口保存普通可变参数，密码由角色凭据接口处理。"""
    try:
        return await asyncio.to_thread(
            app_exploration_service.set_input, task_id, edge_id, request.value
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{task_id}/runs")
async def start(task_id: str, request: RunRequest):
    """开始探索或回放指定角色的模块与整包。"""
    try:
        task = await asyncio.to_thread(app_exploration_service.get, task_id)
        await _device_ready(task["device_serial"])
        return await asyncio.to_thread(
            app_exploration_service.start, task_id, request.role, request.mode, request.module
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{task_id}/runs/stop")
async def stop(task_id: str):
    """请求后台循环在下一次动作前停止。"""
    try:
        return await asyncio.to_thread(app_exploration_service.stop, task_id)
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/{task_id}/document", response_class=PlainTextResponse)
async def document(task_id: str):
    """导出当前探索树的 Markdown 文档。"""
    try:
        return await asyncio.to_thread(app_exploration_service.markdown, task_id)
    except Exception as exc:
        raise _error(exc) from exc
