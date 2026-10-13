"""验证回收任务的生命周期与 HTTP 清理结果，不接触真实历史目录。"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from artemis.data_engine.recording_cleanup import CleanupFailedError, CleanupReport
from apps.admin_console.routers import sessions


@pytest.mark.asyncio
async def test_cleanup_endpoint_refuses_queued_tasks(monkeypatch):
    monkeypatch.setattr(sessions.state, "queue_items", [{"status": "pending"}])
    with pytest.raises(HTTPException) as error:
        await sessions.cleanup_history_endpoint()
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_cleanup_endpoint_reports_partial_failure(monkeypatch):
    from artemis.data_engine.storage import StorageManager

    monkeypatch.setattr(sessions.state, "queue_items", [])
    monkeypatch.setattr(sessions.state, "active_runs", {})
    monkeypatch.setattr(sessions.state, "current_process", None)
    report = CleanupReport(deleted_files=2, freed_bytes=100, errors=["文件被占用"])

    def fail(_storage):
        raise CleanupFailedError(report)

    monkeypatch.setattr(StorageManager, "__init__", lambda *args, **kwargs: None)
    monkeypatch.setattr(StorageManager, "clear_all_data", fail)
    with pytest.raises(HTTPException) as error:
        await sessions.cleanup_history_endpoint()
    assert error.value.status_code == 500
    assert error.value.detail["errors"] == ["文件被占用"]
    assert error.value.detail["freed_bytes"] == 100


def test_startup_missing_recording_becomes_failed_instead_of_blocking_cleanup(
    monkeypatch, tmp_path
):
    from apps.admin_console.services import task_queue_service as module

    repo = MagicMock()
    repo.get_unfinalized_video_recordings.return_value = [
        {"session_id": "old", "local_video_path": str(tmp_path / "recording.mkv")}
    ]
    monkeypatch.setattr(module, "session_repo", repo)
    monkeypatch.setattr(module.DeviceExecutionLock, "get_active_owners", lambda: {})
    monkeypatch.setattr(module.media_service, "recover_orphaned_recording", lambda *args: None)
    assert module.TaskQueueService.recover_orphaned_recordings_on_launch() == 0
    repo.mark_recording_failed_if_pending.assert_called_once()
