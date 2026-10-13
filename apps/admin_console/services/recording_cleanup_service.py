"""随服务启动和关闭的录屏回收任务；删除操作放在线程中，避免阻塞网页请求。"""

import asyncio

from artemis.config import DB_PATH, TRACES_PATH
from artemis.config.settings import settings
from artemis.data_engine.storage import StorageManager
from artemis.utils.logger import get_logger

logger = get_logger(__name__)


class RecordingCleanupService:
    """每小时检查录屏年龄和容量，关机时等待当前文件操作完成。"""

    def __init__(self):
        self._task: asyncio.Task | None = None

    def start(self, recovery_task: asyncio.Task | None = None) -> None:
        """先等异常录屏恢复，再启动周期回收；重复启动不会创建多个循环。"""
        if settings.ARTEMIS_RECORDING_CLEANUP_ENABLED and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._run(recovery_task))
            logger.info(
                f"录屏自动回收已开启：保留 {settings.ARTEMIS_RECORDING_RETENTION_DAYS:g} 天，"
                f"上限 {settings.ARTEMIS_RECORDING_MAX_GIB:g} GiB，"
                f"每 {settings.ARTEMIS_RECORDING_CLEANUP_INTERVAL_SECONDS:g} 秒检查一次"
            )

    async def stop(self) -> None:
        """取消等待并收尾，避免关闭服务后后台线程继续删除文件。"""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self, recovery_task: asyncio.Task | None) -> None:
        """等待恢复并按周期回收；一次失败不会停止后续检查。"""
        if recovery_task:
            try:
                await asyncio.shield(recovery_task)
            except Exception as exc:
                logger.error(f"录屏恢复失败，本轮暂不自动回收：{exc}", exc_info=True)
                await asyncio.sleep(settings.ARTEMIS_RECORDING_CLEANUP_INTERVAL_SECONDS)
        while True:
            operation = asyncio.create_task(asyncio.to_thread(self._collect))
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                # 线程不能被协程取消；必须等这次删除真正结束。
                try:
                    await operation
                except Exception as exc:
                    logger.error(f"关闭服务时录屏回收失败：{exc}")
                raise
            except Exception as exc:
                logger.error(f"录屏自动回收失败，下个周期重试：{exc}", exc_info=True)
            await asyncio.sleep(settings.ARTEMIS_RECORDING_CLEANUP_INTERVAL_SECONDS)

    @staticmethod
    def _collect():
        """读取已验证的配置，回收录屏并报告仍超容量的情况。"""
        storage = StorageManager(DB_PATH, TRACES_PATH)
        report = storage.recycle_recordings(
            retention_days=settings.ARTEMIS_RECORDING_RETENTION_DAYS,
            max_bytes=int(settings.ARTEMIS_RECORDING_MAX_GIB * 1024**3),
        )
        if report.errors:
            logger.warning(f"录屏回收有失败项：{report.errors}")
        if report.remaining_bytes > int(settings.ARTEMIS_RECORDING_MAX_GIB * 1024**3):
            logger.warning("录屏仍超容量：保留正在使用、刚写入或无法删除的文件，稍后重试")
        return report


recording_cleanup_service = RecordingCleanupService()
