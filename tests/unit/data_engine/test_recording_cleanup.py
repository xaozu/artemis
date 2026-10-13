"""用临时数据库和真实临时文件验证清理边界，不操作用户的历史资料。"""

import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from artemis.data_engine import recording_cleanup as cleanup
from artemis.data_engine.models import SessionMetadata, VideoRecordingRecord
from artemis.data_engine.storage import StorageManager

NOW = 2_000_000_000.0


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup.DeviceExecutionLock, "get_active_owners", lambda: {})
    return StorageManager(tmp_path / "traces" / "data_engine.db", tmp_path / "traces")


def recording(storage, *, days=8, name=None, status="ready", session_status="completed", size=100):
    """构造有明确结束时间的分段录屏，包含需要保留的诊断文件。"""
    sid, vid = uuid4(), uuid4()
    folder = storage.base_trace_dir / (name or f"task_{sid}_PASS_2033-05-18T00-00-00")
    folder.mkdir()
    end = NOW - days * 86400
    for filename in ("recording.mp4", "recording_001.mp4", "recording.mkv", "recording.json"):
        path = folder / filename
        path.write_bytes(b"v" * size)
        os.utime(path, (end, end))
    (folder / "report.md").write_text("任务结果")
    (folder / "screenshot.jpeg").write_bytes(b"screenshot")
    storage.create_session(
        SessionMetadata(
            session_id=sid,
            initial_goal="清理验证",
            start_time=end - 60,
            end_time=end,
            status=session_status,
        )
    )
    storage.create_video_recording(
        VideoRecordingRecord(
            video_id=vid,
            session_id=sid,
            device_id="phone",
            start_time=end - 60,
            end_time=end,
            local_video_path=str(folder / "recording.mp4"),
            status=status,
        )
    )
    return sid, vid, folder


def count(storage, table):
    with storage._get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_clear_history_cleans_mcp_web_orphans_and_related_tables(storage):
    _, _, mcp = recording(storage)
    _, _, web = recording(storage, name="web_123456_abcdef12_TESTFAIL_2033-05-18T00-00-00")
    orphan = storage.base_trace_dir / f"task_{uuid4()}_FAIL_2033-05-18T00-00-00"
    orphan.mkdir()
    (orphan / "recording_002.mkv").write_bytes(b"old")
    protected = storage.base_trace_dir / "manual_workflows"
    protected.mkdir()
    (protected / "template.json").write_text("保留模板")
    with storage._get_connection() as conn:
        conn.execute("INSERT INTO history_chunks (chunk_id, session_id) VALUES ('chunk', 'old')")
        conn.commit()
    report = storage.clear_all_data()
    assert report.deleted_files >= 13
    assert not mcp.exists() and not web.exists() and not orphan.exists()
    assert (protected / "template.json").exists()
    assert storage.db_path.exists()
    assert (
        count(storage, "video_recordings")
        == count(storage, "history_chunks")
        == count(storage, "sessions")
        == 0
    )
    assert storage.clear_all_data().deleted_files == 0


@pytest.mark.parametrize(
    "session_status,status",
    [("running", "ready"), ("completed", "recording"), ("completed", "finalizing")],
)
def test_clear_rejects_active_or_finalizing_tasks(storage, session_status, status):
    _, _, folder = recording(storage, session_status=session_status, status=status)
    with pytest.raises(cleanup.CleanupBusyError):
        storage.clear_all_data()
    assert (folder / "recording.mp4").exists()
    assert count(storage, "sessions") == 1


def test_clear_failure_preserves_database_for_retry(storage, monkeypatch):
    _, _, folder = recording(storage)
    original = Path.unlink

    def fail_one(path, *args, **kwargs):
        if path.name == "recording.mp4":
            raise PermissionError("文件被占用")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_one)
    with pytest.raises(cleanup.CleanupFailedError) as error:
        storage.clear_all_data()
    assert error.value.report.errors
    assert count(storage, "video_recordings") == 1
    assert (folder / "recording.mp4").exists()
    monkeypatch.setattr(Path, "unlink", original)
    storage.clear_all_data()
    assert count(storage, "sessions") == 0 and not folder.exists()


def test_symlink_and_external_reference_never_deleted(storage, tmp_path):
    sid, vid, folder = recording(storage)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"protected")
    (folder / "external.mp4").symlink_to(outside)
    with pytest.raises(cleanup.CleanupFailedError):
        storage.clear_all_data()
    assert outside.read_bytes() == b"protected"
    with storage._get_connection() as conn:
        conn.execute(
            "UPDATE video_recordings SET local_video_path = ? WHERE video_id = ?",
            (str(outside), str(vid)),
        )
        conn.commit()
    with pytest.raises(cleanup.CleanupFailedError):
        storage.clear_all_data()
    assert outside.exists() and count(storage, "sessions") == 1


def test_recycle_expired_segments_preserves_results_and_updates_state(storage):
    sid, vid, folder = recording(storage)
    report = storage.recycle_recordings(now=NOW)
    assert report.freed_bytes == 400 and report.recycled_recordings == 1
    assert report.remaining_bytes == 0
    assert not (folder / "recording_001.mp4").exists()
    assert (folder / "report.md").exists() and (folder / "screenshot.jpeg").exists()
    assert storage.get_video_recording(vid).status == "expired"
    assert storage.get_video_recording(vid).local_video_path is None
    assert count(storage, "sessions") == 1
    assert storage.recycle_recordings(now=NOW).deleted_files == 0


def test_capacity_recycles_oldest_first(storage):
    _, old_vid, old = recording(storage, days=3)
    _, new_vid, new = recording(storage, days=2)
    report = storage.recycle_recordings(now=NOW, max_bytes=400)
    assert report.recycled_recordings == 1 and report.remaining_bytes == 400
    assert not (old / "recording.mp4").exists() and (new / "recording.mp4").exists()
    assert storage.get_video_recording(old_vid).status == "expired"
    assert storage.get_video_recording(new_vid).status == "ready"


def test_auto_protects_active_and_recent_recordings(storage, monkeypatch):
    sid, vid, active = recording(storage)
    _, _, recent = recording(storage, days=0)
    monkeypatch.setattr(
        cleanup.DeviceExecutionLock,
        "get_active_owners",
        lambda: {"phone": SimpleNamespace(session_id=str(sid))},
    )
    report = storage.recycle_recordings(now=NOW, max_bytes=1)
    assert report.recycled_recordings == 0 and report.remaining_bytes == 800
    assert (active / "recording.mp4").exists() and (recent / "recording.mp4").exists()


def test_single_delete_matches_exact_name(storage):
    sid, _, deleted = recording(storage, name="case")
    _, _, retained = recording(storage, name="case_extra")
    storage.delete_session(sid)
    assert not deleted.exists() and retained.exists()
    assert count(storage, "sessions") == 1


def test_auto_cleans_legacy_session_path_without_recording_row(storage):
    sid, vid, folder = recording(storage, name="sdk_case")
    with storage._get_connection() as conn:
        conn.execute(
            "UPDATE sessions SET video_filepath = ? WHERE session_id = ?",
            (str(folder / "recording.mp4"), str(sid)),
        )
        conn.execute("DELETE FROM video_recordings WHERE video_id = ?", (str(vid),))
        conn.commit()
    assert storage.recycle_recordings(now=NOW).recycled_recordings == 1
    with storage._get_connection() as conn:
        assert conn.execute("SELECT status FROM video_recordings").fetchone()[0] == "expired"
        assert conn.execute("SELECT video_filepath FROM sessions").fetchone()[0] is None


def test_auto_failure_keeps_reference_and_retries(storage, monkeypatch):
    _, vid, folder = recording(storage)
    original = Path.unlink

    def fail_one(path, *args, **kwargs):
        if path.name == "recording.mp4":
            raise PermissionError("文件被占用")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_one)
    report = storage.recycle_recordings(now=NOW)
    assert report.errors and report.remaining_bytes == 100
    assert storage.get_video_recording(vid).status == "ready"
    monkeypatch.setattr(Path, "unlink", original)
    assert storage.recycle_recordings(now=NOW).recycled_recordings == 1
    assert storage.get_video_recording(vid).status == "expired"


def test_auto_recycles_orphan_but_preserves_other_resources(storage):
    folder = storage.base_trace_dir / f"task_{uuid4()}_PASS_2033-05-18T00-00-00"
    folder.mkdir()
    video = folder / "recording.mp4"
    video.write_bytes(b"orphan")
    os.utime(video, (NOW - 9 * 86400, NOW - 9 * 86400))
    (folder / "stdout.log").write_text("诊断")
    assert storage.recycle_recordings(now=NOW).recycled_recordings == 1
    assert not video.exists() and (folder / "stdout.log").exists()


def test_conversion_and_cleanup_share_exclusive_file_lock(storage):
    """同一进程不同文件描述符也不能同时转换和删除录屏。"""
    with cleanup.recording_files_lock(storage.base_trace_dir):
        with pytest.raises(cleanup.CleanupBusyError):
            with cleanup.recording_files_lock(storage.base_trace_dir, timeout=0):
                pytest.fail("录屏锁不应允许同时进入")
    with cleanup.recording_files_lock(storage.base_trace_dir, timeout=0):
        pass


def test_auto_rechecks_owner_before_deletion(storage, monkeypatch):
    sid, _, folder = recording(storage)
    responses = iter([{}, {"phone": SimpleNamespace(session_id=str(sid))}])
    monkeypatch.setattr(cleanup.DeviceExecutionLock, "get_active_owners", lambda: next(responses))
    assert storage.recycle_recordings(now=NOW).deleted_files == 0
    assert (folder / "recording.mp4").exists()


def test_retention_uses_task_end_after_video_recovery(storage):
    _, _, folder = recording(storage, days=8)
    # 前一天恢复过格式，但任务已经结束八天，仍应按任务结束时间回收。
    for path in folder.glob("recording*.*"):
        os.utime(path, (NOW - 86400, NOW - 86400))
    assert storage.recycle_recordings(now=NOW).recycled_recordings == 1


def test_auto_marks_legacy_session_without_any_video_reference(storage):
    sid, vid, folder = recording(storage)
    with storage._get_connection() as conn:
        conn.execute("DELETE FROM video_recordings WHERE video_id = ?", (str(vid),))
        conn.commit()
    assert storage.recycle_recordings(now=NOW).recycled_recordings == 1
    with storage._get_connection() as conn:
        row = conn.execute("SELECT session_id, status FROM video_recordings").fetchone()
        assert tuple(row) == (str(sid), "expired")


def test_reused_sdk_task_name_does_not_expire_another_recording(storage):
    _, old_vid, old = recording(storage, days=8, name="sdk_case_PASS_2033-05-01T00-00-00")
    _, new_vid, new = recording(storage, days=2, name="sdk_case_PASS_2033-05-07T00-00-00")
    report = storage.recycle_recordings(now=NOW, max_bytes=400)
    assert report.recycled_recordings == 1
    assert not (old / "recording.mp4").exists()
    assert (new / "recording.mp4").exists()
    assert storage.get_video_recording(old_vid).status == "expired"
    assert storage.get_video_recording(new_vid).status == "ready"


def test_single_delete_reused_name_preserves_other_session(storage):
    sid, _, old = recording(storage, name="sdk_case_PASS_2033-05-01T00-00-00")
    _, _, new = recording(storage, name="sdk_case_PASS_2033-05-07T00-00-00")
    storage.delete_session(sid)
    assert not old.exists() and new.exists()


def test_single_delete_protects_device_before_session_is_registered(storage, monkeypatch):
    sid, _, folder = recording(storage)
    monkeypatch.setattr(
        cleanup.DeviceExecutionLock,
        "get_active_owners",
        lambda: {"phone": SimpleNamespace(session_id=None)},
    )
    with pytest.raises(cleanup.CleanupBusyError):
        storage.delete_session(sid)
    assert (folder / "recording.mp4").exists()


def test_orphan_rechecks_device_owner_before_deletion(storage, monkeypatch):
    _, vid, folder = recording(storage)
    with storage._get_connection() as conn:
        conn.execute("DELETE FROM video_recordings")
        conn.execute("DELETE FROM sessions")
        conn.commit()
    responses = iter([{}, {"phone": SimpleNamespace(session_id=None)}])
    monkeypatch.setattr(cleanup.DeviceExecutionLock, "get_active_owners", lambda: next(responses))
    assert storage.recycle_recordings(now=NOW).deleted_files == 0
    assert (folder / "recording.mp4").exists()


def test_unreadable_folder_does_not_block_other_expired_videos(storage, monkeypatch):
    _, _, bad = recording(storage)
    _, _, good = recording(storage)
    original = Path.iterdir

    def unreadable(path):
        if path == bad:
            raise PermissionError("目录不可读")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    report = storage.recycle_recordings(now=NOW)
    assert report.errors and report.recycled_recordings == 1
    assert (bad / "recording.mp4").exists() and not (good / "recording.mp4").exists()
