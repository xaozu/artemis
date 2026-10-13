"""统一清理会话文件与录屏；只处理 traces 根目录内能够确认归属的文件。"""

from dataclasses import dataclass, field
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from artemis.runtime.device_lock import DeviceExecutionLock
from artemis.runtime.process_probe import pid_is_alive
from artemis.utils.logger import get_logger

logger = get_logger(__name__)

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


def recycle_recordings(
    conn: sqlite3.Connection, root: Path, *, retention_days: float, max_bytes: int, now: float
) -> CleanupReport:
    """按结束时间及容量回收录屏；保留会话、截图、日志、笔记和分析证据。"""
    report = CleanupReport()
    sessions = {str(r["session_id"]): r for r in _read_rows(conn, "sessions")}
    active, devices = _active_sessions(list(sessions.values()))
    recordings = _read_rows(conn, "video_recordings")
    # 兼容旧版本只有 sessions.video_filepath、没有 video_recordings 行的录屏。
    registered = {(str(r.get("session_id")), r.get("local_video_path")) for r in recordings}
    for sid, session in sessions.items():
        value = session.get("video_filepath")
        if value and (sid, value) not in registered:
            recordings.append(
                {
                    "video_id": None,
                    "session_id": sid,
                    "local_video_path": value,
                    "status": "ready",
                    "start_time": session.get("start_time"),
                    "end_time": session.get("end_time"),
                }
            )
    folders: dict[Path, list[dict]] = {}
    blocked: set[Path] = set()
    entries = list(root.iterdir())
    rows_by_folder: dict[Path, list[dict]] = {}
    for row in recordings:
        value = row.get("local_video_path")
        if not value:
            continue
        matches = _reference_folders(value, root, entries)
        if len(matches) > 1:
            blocked.update(matches)
            report.errors.append(f"旧录屏路径对应多个同名目录，暂不回收：{value}")
        for folder in matches:
            rows_by_folder.setdefault(folder.absolute(), []).append(row)
    for folder in entries:
        if folder.name in _RESOURCE_FOLDERS or not folder.is_dir() or not _safe_path(folder, root):
            continue
        rows = rows_by_folder.get(folder.absolute(), [])
        inferred_sid = trace_name(folder.name).removeprefix("task_")
        if not rows and inferred_sid in sessions:
            session = sessions[inferred_sid]
            rows = [
                {
                    "video_id": None,
                    "session_id": inferred_sid,
                    "status": "ready",
                    "start_time": session.get("start_time"),
                    "end_time": session.get("end_time"),
                }
            ]
        if not rows and not _OWNED_NAME.fullmatch(trace_name(folder.name)):
            continue
        folders[folder] = rows
        try:
            if (
                (not rows and devices)
                or any(
                    str(r.get("session_id")) in active
                    or r.get("device_id") in devices
                    or r.get("status") in _PENDING_RECORDING
                    for r in rows
                )
                or trace_name(folder.name) in active
                or _folder_is_live(folder, now)
            ):
                blocked.add(folder)
        except OSError as exc:
            blocked.add(folder)
            report.errors.append(f"检查录屏目录 {folder} 失败：{exc}")
    candidates = []
    total = 0
    for folder, rows in folders.items():
        try:
            files = [p for p in folder.iterdir() if _RECORDING_FILE.fullmatch(p.name)]
            if any(not _safe_path(p, root) or not p.is_file() for p in files):
                blocked.add(folder)
                continue
            size = sum(p.stat().st_size for p in files)
            last_written = max((p.stat().st_mtime for p in files), default=now)
        except OSError as exc:
            report.errors.append(f"读取录屏目录 {folder} 失败：{exc}")
            continue
        if not files:
            continue
        total += size
        if folder in blocked:
            continue
        end_times = [float(r.get("end_time") or 0) for r in rows] + [
            float(sessions.get(str(r.get("session_id")), {}).get("end_time") or 0) for r in rows
        ]
        # 保留期按任务结束时间计算；无引用文件才使用最后修改时间。
        ended = max([value for value in end_times if value > 0] or [last_written])
        # 新整理的文件至少静置一小时，给任务登记与异常恢复留出时间。
        if now - last_written >= 3600:
            candidates.append((ended, folder, files, size, rows))
    for ended, folder, files, _size, rows in sorted(candidates, key=lambda item: item[0]):
        if now - ended < retention_days * 86400 and total <= max_bytes:
            continue
        # 设备占用跨进程变化，删除前再核对；SQLite 写事务阻止并发更新会话状态。
        latest_active, latest_devices = _active_sessions(list(sessions.values()))
        if (
            (not rows and latest_devices)
            or any(
                str(r.get("session_id")) in latest_active or r.get("device_id") in latest_devices
                for r in rows
            )
            or _folder_is_live(folder, now)
        ):
            continue
        errors_before = len(report.errors)
        freed_before = report.freed_bytes
        remove_paths(files, root, report)
        total -= report.freed_bytes - freed_before
        if len(report.errors) != errors_before:
            continue
        for row in rows:
            if row["video_id"]:
                conn.execute(
                    "UPDATE video_recordings SET status = 'expired', local_video_path = NULL, error = NULL WHERE video_id = ?",
                    (row["video_id"],),
                )
            else:
                conn.execute(
                    "INSERT INTO video_recordings (video_id, session_id, start_time, end_time, status) VALUES (?, ?, ?, ?, 'expired')",
                    (str(uuid4()), row["session_id"], row.get("start_time"), row.get("end_time")),
                )
            session = sessions.get(str(row["session_id"]), {})
            value = session.get("video_filepath")
            if value and folder in _reference_folders(value, root, entries):
                conn.execute(
                    "UPDATE sessions SET video_filepath = NULL WHERE session_id = ?",
                    (row["session_id"],),
                )
                session["video_filepath"] = None
        report.recycled_recordings += 1
    report.remaining_bytes = total
    logger.info(f"录屏自动回收结果：{report.as_dict()}")
    return report
