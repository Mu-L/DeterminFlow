"""
文件系统监控器 - 监控skills和rules目录的变化

使用watchdog库实现文件系统监控，当目录内容变化时触发重新加载。
支持防抖机制，避免频繁重载。
"""
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logger = logging.getLogger(__name__)


class DebouncedEventHandler(FileSystemEventHandler):
    """
    带防抖功能的文件事件处理器

    当检测到文件变化时，等待指定时间后触发回调，
    避免短时间内多次变化导致频繁重载。
    """

    def __init__(self, callback: Callable[[], None], debounce_seconds: float = 1.0):
        """
        初始化事件处理器

        Args:
            callback: 触发回调函数
            debounce_seconds: 防抖时间（秒）
        """
        super().__init__()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent):
        """
        处理任何文件系统事件

        Args:
            event: 文件系统事件
        """
        # 忽略目录自身的事件
        if event.is_directory:
            return

        # 只处理SKILL.md和RULE.md文件
        src_path = Path(event.src_path)
        if src_path.name not in ('SKILL.md', 'RULE.md'):
            return

        logger.debug(f"检测到文件变化: {event.event_type} - {event.src_path}")
        self._schedule_callback()

    def _schedule_callback(self):
        """调度回调函数（带防抖）"""
        with self._lock:
            # 取消之前的定时器
            if self._timer is not None:
                self._timer.cancel()

            # 创建新的定时器
            self._timer = threading.Timer(self.debounce_seconds, self._execute_callback)
            self._timer.daemon = True
            self._timer.start()

    def _execute_callback(self):
        """执行回调函数"""
        try:
            logger.info("文件变化触发重新加载")
            self.callback()
        except Exception as e:
            logger.error(f"执行回调失败: {e}")

    def cancel(self):
        """取消待执行的回调"""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class DirectoryWatcher:
    """
    目录监控器

    监控指定目录的变化，当目录内容变化时触发回调。
    支持多个目录的监控。
    """

    def __init__(self, debounce_seconds: float = 1.0):
        """
        初始化目录监控器

        Args:
            debounce_seconds: 防抖时间（秒）
        """
        self.debounce_seconds = debounce_seconds
        self._observer: Observer | None = None
        self._watched_paths: dict[str, Any] = {}  # path -> watch object
        self._handlers: dict[str, DebouncedEventHandler] = {}  # path -> handler
        self._running = False

    def add_directory(self, directory: Path, callback: Callable[[], None]) -> bool:
        """
        添加目录监控

        Args:
            directory: 要监控的目录路径
            callback: 目录变化时的回调函数

        Returns:
            True 如果添加成功
        """
        try:
            if not directory.exists():
                logger.warning(f"目录不存在，跳过监控: {directory}")
                return False

            if not directory.is_dir():
                logger.warning(f"路径不是目录，跳过监控: {directory}")
                return False

            # 创建事件处理器
            handler = DebouncedEventHandler(callback, self.debounce_seconds)

            # 如果监控器正在运行，添加监控
            if self._running and self._observer:
                watch = self._observer.schedule(handler, str(directory), recursive=True)
                self._watched_paths[str(directory)] = watch

            self._handlers[str(directory)] = handler
            logger.info(f"已添加目录监控: {directory}")
            return True

        except Exception as e:
            logger.error(f"添加目录监控失败 {directory}: {e}")
            return False

    def remove_directory(self, directory: Path) -> bool:
        """
        移除目录监控

        Args:
            directory: 要移除监控的目录路径

        Returns:
            True 如果移除成功
        """
        try:
            dir_str = str(directory)

            # 取消监控
            if dir_str in self._watched_paths and self._observer:
                self._observer.unschedule(self._watched_paths[dir_str])
                del self._watched_paths[dir_str]

            # 取消待执行的回调
            if dir_str in self._handlers:
                self._handlers[dir_str].cancel()
                del self._handlers[dir_str]

            logger.info(f"已移除目录监控: {directory}")
            return True

        except Exception as e:
            logger.error(f"移除目录监控失败 {directory}: {e}")
            return False

    def start(self) -> bool:
        """
        启动目录监控

        Returns:
            True 如果启动成功
        """
        try:
            if self._running:
                logger.warning("监控器已在运行")
                return True

            # 创建观察者
            self._observer = Observer()

            # 为已注册的目录添加监控
            for dir_str, handler in self._handlers.items():
                watch = self._observer.schedule(handler, dir_str, recursive=True)
                self._watched_paths[dir_str] = watch

            # 启动观察者
            self._observer.start()
            self._running = True

            logger.info(f"目录监控器已启动，监控 {len(self._handlers)} 个目录")
            return True

        except Exception as e:
            logger.error(f"启动目录监控器失败: {e}")
            return False

    def stop(self):
        """停止目录监控"""
        try:
            if not self._running or not self._observer:
                return

            # 停止观察者
            self._observer.stop()
            self._observer.join(timeout=5)

            # 取消所有待执行的回调
            for handler in self._handlers.values():
                handler.cancel()

            self._observer = None
            self._running = False
            self._watched_paths.clear()

            logger.info("目录监控器已停止")

        except Exception as e:
            logger.error(f"停止目录监控器失败: {e}")

    def is_running(self) -> bool:
        """检查监控器是否正在运行"""
        return self._running

    def get_watched_directories(self) -> list[str]:
        """获取正在监控的目录列表"""
        return list(self._handlers.keys())


class SkillRuleWatcher:
    """
    Skills和Rules目录监控器

    专门用于监控skills和rules目录的变化。
    """

    def __init__(self, skills_dir: Path, rules_dir: Path,
                 skills_callback: Callable[[], None],
                 rules_callback: Callable[[], None],
                 debounce_seconds: float = 1.0):
        """
        初始化Skills和Rules目录监控器

        Args:
            skills_dir: skills目录路径
            rules_dir: rules目录路径
            skills_callback: skills目录变化时的回调
            rules_callback: rules目录变化时的回调
            debounce_seconds: 防抖时间（秒）
        """
        self.skills_dir = skills_dir
        self.rules_dir = rules_dir
        self.skills_callback = skills_callback
        self.rules_callback = rules_callback

        # 创建目录监控器
        self._watcher = DirectoryWatcher(debounce_seconds)

    def start(self) -> bool:
        """
        启动监控

        Returns:
            True 如果启动成功
        """
        # 添加skills目录监控
        self._watcher.add_directory(self.skills_dir, self.skills_callback)

        # 添加rules目录监控
        self._watcher.add_directory(self.rules_dir, self.rules_callback)

        # 启动监控器
        return self._watcher.start()

    def stop(self):
        """停止监控"""
        self._watcher.stop()

    def is_running(self) -> bool:
        """检查监控器是否正在运行"""
        return self._watcher.is_running()

    def get_status(self) -> dict:
        """获取监控器状态"""
        return {
            "running": self._watcher.is_running(),
            "skills_dir": str(self.skills_dir),
            "rules_dir": str(self.rules_dir),
            "watched_directories": self._watcher.get_watched_directories(),
        }