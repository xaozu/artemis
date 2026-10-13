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


def test_single_delete_matches_exact_name(storage):
    sid, _, deleted = recording(storage, name="case")
    _, _, retained = recording(storage, name="case_extra")
    storage.delete_session(sid)
    assert not deleted.exists() and retained.exists()
    assert count(storage, "sessions") == 1


def test_conversion_and_cleanup_share_exclusive_file_lock(storage):
    """同一进程不同文件描述符也不能同时转换和删除录屏。"""
    with cleanup.recording_files_lock(storage.base_trace_dir):
        with pytest.raises(cleanup.CleanupBusyError):
            with cleanup.recording_files_lock(storage.base_trace_dir, timeout=0):
                pytest.fail("录屏锁不应允许同时进入")
    with cleanup.recording_files_lock(storage.base_trace_dir, timeout=0):
        pass


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
