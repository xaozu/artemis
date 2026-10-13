"""验证回收任务的生命周期与 HTTP 清理结果，不接触真实历史目录。"""

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from artemis.data_engine.recording_cleanup import CleanupFailedError, CleanupReport
from apps.admin_console.routers import media, sessions
from apps.admin_console.services.recording_cleanup_service import RecordingCleanupService, settings


@pytest.mark.asyncio
async def test_disabled_service_does_not_collect(monkeypatch):
    monkeypatch.setattr(settings, "ARTEMIS_RECORDING_CLEANUP_ENABLED", False)
    service = RecordingCleanupService()
    collect = MagicMock()
    monkeypatch.setattr(service, "_collect", collect)
    service.start()
    await asyncio.sleep(0)
    await service.stop()
    collect.assert_not_called()


@pytest.mark.asyncio
async def test_service_waits_for_recovery_and_stops_without_duplicate_runs(monkeypatch):
    monkeypatch.setattr(settings, "ARTEMIS_RECORDING_CLEANUP_ENABLED", True)
    monkeypatch.setattr(settings, "ARTEMIS_RECORDING_CLEANUP_INTERVAL_SECONDS", 3600)
    recovered = asyncio.Event()
    collected = asyncio.Event()
    loop = asyncio.get_running_loop()

    async def recover():
        await recovered.wait()

    recovery_task = asyncio.create_task(recover())
    service = RecordingCleanupService()
    calls = []

    def collect():
        calls.append(True)
        loop.call_soon_threadsafe(collected.set)

    monkeypatch.setattr(service, "_collect", collect)
    service.start(recovery_task)
    service.start(recovery_task)
    await asyncio.sleep(0)
    assert not calls
    recovered.set()
    await asyncio.wait_for(collected.wait(), timeout=3)
    await service.stop()
    assert calls == [True] and service._task is None


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


@pytest.mark.asyncio
async def test_expired_recording_never_resolves_old_disk_index(monkeypatch):
    repo = MagicMock()
    repo.get_video_recording_for_session.return_value = {"status": "expired"}
    repo.get_session_by_id.return_value = {"session_id": "old", "status": "completed"}
    monkeypatch.setattr(media, "session_repo", repo)
    monkeypatch.setattr(
        media.media_service, "build_video_index", MagicMock(return_value={"old": "stale.mp4"})
    )
    resolve = MagicMock()
    monkeypatch.setattr(media.media_service, "resolve_video_url", resolve)
    response = await media.get_session_video("old")
    assert response["status"] == "expired" and response["video_url"] is None
    assert "自动回收" in response["message"]
    resolve.assert_not_called()


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


@pytest.mark.asyncio
async def test_service_stop_waits_for_inflight_file_operation(monkeypatch):
    import threading

    monkeypatch.setattr(settings, "ARTEMIS_RECORDING_CLEANUP_ENABLED", True)
    entered, finished = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    service = RecordingCleanupService()

    def collect():
        loop.call_soon_threadsafe(entered.set)
        release.wait(timeout=3)
        loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(service, "_collect", collect)
    service.start()
    await asyncio.wait_for(entered.wait(), timeout=3)
    stop_task = asyncio.create_task(service.stop())
    await asyncio.sleep(0)
    assert not stop_task.done()
    release.set()
    await asyncio.wait_for(stop_task, timeout=3)
    assert finished.is_set()
