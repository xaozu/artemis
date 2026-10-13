"""统一清理会话文件与录屏；只处理 traces 根目录内能够确认归属的文件。"""

from dataclasses import dataclass, field
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from artemis.runtime.device_lock import DeviceExecutionLock
from artemis.runtime.process_probe import pid_is_alive


# 旧目录的状态后缀必须完整匹配，避免 task_1 把 task_10 一并删除。
_SUFFIX = re.compile(
    r"_(?:PASS|FAIL|TESTFAIL|FAILED|CANCELLED|CANCELED|ASSERT_FAIL)_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$"
)
_UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
_OWNED_NAME = re.compile(rf"(?:{_UUID}|task_{_UUID}|web_\d+(?:_[0-9a-f]{{8}})?)$")
_RECORDING_FILE = re.compile(r"recording(?:_\d{3,}|_converted)?\.(?:mp4|mkv|webm|json)$")
_TERMINAL = {"completed", "success", "failed", "cancelled"}
_PENDING_RECORDING = {"recording", "finalizing", "processing"}
_RESOURCE_FOLDERS = {"images", "app_explorations", "manual_workflows", "replay"}


@dataclass
class CleanupReport:
    """向调用方返回实际释放空间及失败项；失败项保留关联记录供下次重试。"""

    deleted_files: int = 0
    freed_bytes: int = 0
    recycled_recordings: int = 0
    remaining_bytes: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """生成 API 与日志共用的清理结果。"""
        return vars(self).copy()


class CleanupBusyError(RuntimeError):
    """任务或录屏仍在使用数据，暂时不能清空历史。"""


class CleanupFailedError(RuntimeError):
    """部分文件未删除，不能向用户报告清空成功。"""

    def __init__(self, report: CleanupReport):
        self.report = report
        super().__init__(f"有 {len(report.errors)} 项文件清理失败，请稍后重试")


@contextmanager
def recording_files_lock(root: Path, *, timeout: float = 5):
    """跨进程串行化录屏转换和删除；进程退出后系统自动释放锁。"""
    lock_path = root / ".recording-files.lock"
    if root.is_symlink() or lock_path.is_symlink():
        raise CleanupBusyError("录屏管理目录含符号链接，暂时不能清理")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, PermissionError):
                if time.monotonic() >= deadline:
                    raise CleanupBusyError("录屏正在转换或清理，请稍后重试") from None
                time.sleep(0.05)
        yield
    finally:
        # 关闭文件描述符即可释放系统锁，锁文件保留，避免不同进程锁住不同文件。
        os.close(fd)


def trace_name(name: str) -> str:
    """去掉完整的任务结果后缀，用于精确匹配改名后的目录。"""
    return _SUFFIX.sub("", name)


def _safe_path(path: Path, root: Path) -> bool:
    """不跟随任何层级的符号链接，也不能删除根目录或根目录之外的路径。"""
    absolute = path.absolute()
    if root.is_symlink():
        return False
    try:
        relative = absolute.relative_to(root.absolute())
    except ValueError:
        return False
    if not relative.parts or ".." in relative.parts:
        return False
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return False
    return absolute.resolve().is_relative_to(root.resolve())


def remove_paths(paths: list[Path], root: Path, report: CleanupReport) -> None:
    """安全删除文件或目录；逐项记录失败，允许下次重复调用。"""
    for path in paths:
        try:
            if not _safe_path(path, root):
                report.errors.append(f"跳过不受管理或含符号链接的路径：{path}")
                continue
            if path.is_dir():
                remove_paths(list(path.iterdir()), root, report)
                if not any(path.iterdir()):
                    path.rmdir()
            elif path.exists():
                size = path.stat().st_size
                path.unlink()
                report.deleted_files += 1
                report.freed_bytes += size
        except OSError as exc:
            report.errors.append(f"删除 {path} 失败：{exc}")


def _read_rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]


def _active_sessions(sessions: list[dict]) -> tuple[set[str], set[str]]:
    """数据库状态与跨进程设备占用共同决定任务是否仍在运行。"""
    owners = DeviceExecutionLock.get_active_owners()
    session_ids = {str(o.session_id) for o in owners.values() if o.session_id}
    device_ids = set(owners) | {getattr(owner, "device_id", key) for key, owner in owners.items()}
    for row in sessions:
        if row.get("status") not in _TERMINAL:
            session_ids.add(str(row["session_id"]))
    return session_ids, device_ids


def _folder_is_live(folder: Path, now: float, *, protect_recent: bool = True) -> bool:
    """保护正在写入的文件及尚未写入数据库的 MCP 任务。"""
    status_path = folder / "status.json"
    if status_path.exists():
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            if (
                payload.get("status") not in _TERMINAL
                and payload.get("pid")
                and pid_is_alive(int(payload["pid"]))
            ):
                return True
            if payload.get("status") not in _TERMINAL and now - status_path.stat().st_mtime < 3600:
                return True
        except (OSError, ValueError, TypeError):
            return True
    return protect_recent and any(
        now - p.stat().st_mtime < 60
        for p in folder.iterdir()
        if _RECORDING_FILE.fullmatch(p.name) and p.is_file()
    )


def _reference_folders(value: str, root: Path, entries: list[Path]) -> list[Path]:
    """先匹配实际目录；只有未改名的旧路径才尝试查找唯一的结果目录。"""
    path = Path(value)
    if not _RECORDING_FILE.fullmatch(path.name) or path.parent.parent.absolute() != root.absolute():
        return []
    if path.parent.name in _RESOURCE_FOLDERS:
        return []
    if path.parent.exists() or path.parent.is_symlink():
        return [path.parent]
    # 已带结果后缀的目录不存在时，不能关联到另一轮同名任务。
    if trace_name(path.parent.name) != path.parent.name:
        return []
    return [
        entry
        for entry in entries
        if entry.is_dir()
        and entry.name not in _RESOURCE_FOLDERS
        and trace_name(entry.name) == path.parent.name
    ]


def history_paths(
    conn: sqlite3.Connection, root: Path, session_id: str | None = None
) -> list[Path]:
    """收集准确的会话目录与录屏引用，不把多轮同名 SDK 任务混为同一份记录。"""
    sessions = [
        r
        for r in _read_rows(conn, "sessions")
        if session_id is None or str(r["session_id"]) == session_id
    ]
    recordings = [
        r
        for r in _read_rows(conn, "video_recordings")
        if session_id is None or str(r["session_id"]) == session_id
    ]
    names = {str(r["session_id"]) for r in sessions}
    names.update(f"task_{r['session_id']}" for r in sessions)
    values = [r.get("local_video_path") for r in recordings]
    values += [r.get("video_filepath") for r in sessions]
    entries = list(root.iterdir())
    referenced = set()
    for value in values:
        if not value:
            continue
        folders = _reference_folders(value, root, entries)
        if session_id is not None and len(folders) > 1:
            raise CleanupBusyError(
                "旧录屏路径对应多个同名任务，无法确认归属，请通过清空历史统一处理"
            )
        referenced.update(p.absolute() for p in folders)
    result = []
    for path in entries:
        if (
            path.absolute() in referenced
            or (
                path.is_dir()
                and trace_name(path.name) in names
                and path.name not in _RESOURCE_FOLDERS
            )
        ) or (
            session_id is None
            and (
                _OWNED_NAME.fullmatch(trace_name(path.name))
                or path.name in {"images", "mcp_server.log"}
            )
        ):
            result.append(path)
    # 超出管理目录的引用必须报告未清理项，不能扩大删除范围。
    for value in values:
        if value:
            path = Path(value)
            if (
                _RECORDING_FILE.fullmatch(path.name)
                and path.exists()
                and not any(
                    parent.absolute() in {p.absolute() for p in result} for parent in path.parents
                )
            ):
                result.append(path)
    return result


def clear_session_files(conn: sqlite3.Connection, root: Path, session_id: str) -> CleanupReport:
    """单条会话删除与全量清空共用路径校验、文件删除和失败报告。"""
    active, devices = _active_sessions(_read_rows(conn, "sessions"))
    if session_id in active or any(
        str(r["session_id"]) == session_id
        and (r.get("status") in _PENDING_RECORDING or r.get("device_id") in devices)
        for r in _read_rows(conn, "video_recordings")
    ):
        raise CleanupBusyError("任务或录屏仍在运行，请先结束任务再删除")
    report = CleanupReport()
    paths = history_paths(conn, root, session_id)
    entries = list(root.iterdir())
    other_values = [
        r.get("local_video_path")
        for r in _read_rows(conn, "video_recordings")
        if str(r["session_id"]) != session_id
    ]
    other_values += [
        r.get("video_filepath")
        for r in _read_rows(conn, "sessions")
        if str(r["session_id"]) != session_id
    ]
    shared = {
        p.absolute()
        for value in other_values
        if value
        for p in _reference_folders(value, root, entries)
    }
    if any(p.absolute() in shared for p in paths if p.is_dir()):
        raise CleanupBusyError("录屏目录被其他会话共用，请通过清空历史统一处理")
    if any(
        p.is_dir() and not p.is_symlink() and _folder_is_live(p, time.time(), protect_recent=False)
        for p in paths
    ):
        raise CleanupBusyError("任务文件仍在使用，请稍后重试")
    remove_paths(paths, root, report)
    if report.errors:
        raise CleanupFailedError(report)
    return report


def clear_history_files(conn: sqlite3.Connection, root: Path) -> CleanupReport:
    """有任务或录屏未结束时拒绝清空；文件全部删除后才允许删数据库记录。"""
    sessions = _read_rows(conn, "sessions")
    active, devices = _active_sessions(sessions)
    recordings = _read_rows(conn, "video_recordings")
    if active or devices or any(r.get("status") in _PENDING_RECORDING for r in recordings):
        raise CleanupBusyError("有运行中或尚未整理完成的任务，请先结束任务再清空历史")
    paths = history_paths(conn, root)
    # 新任务可能先创建文件再登记会话；近期孤立录屏保留到下一次清理。
    if any(
        p.is_dir() and not p.is_symlink() and _folder_is_live(p, time.time(), protect_recent=False)
        for p in paths
        if p.name != "images"
    ):
        raise CleanupBusyError("录屏文件仍在使用或刚刚写入，请稍后再清空历史")
    report = CleanupReport()
    remove_paths(paths, root, report)
    if report.errors:
        raise CleanupFailedError(report)
    return report
