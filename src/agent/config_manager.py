"""
Agent 配置管理器 - 管理 Agent 定义的外部配置

参照 SkillConfigManager 和 RuleConfigManager 的设计模式
"""
import asyncio
import json
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)


class _ConfigFileHandler(FileSystemEventHandler):
    """配置文件变化处理器（带防抖）"""

    def __init__(self, callback, debounce_seconds: float = 1.0):
        super().__init__()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_modified(self, event):
        if event.is_directory:
            return
        # 只处理 agents_config.json 文件
        if Path(event.src_path).name == "agents_config.json":
            logger.debug(f"检测到配置文件变化: {event.src_path}")
            self._schedule_callback()

    def _schedule_callback(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_seconds, self._execute_callback)
            self._timer.daemon = True
            self._timer.start()

    def _execute_callback(self):
        try:
            self.callback()
        except Exception as e:
            logger.error(f"执行配置重新加载回调失败: {e}")

    def cancel(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class AgentConfigManager:
    """
    管理 Agent 定义的外部配置

    配置存储在 config/agents_config.json 中，用于：
    - 覆盖内置 Agent 定义
    - 添加自定义 Agent 类型
    - 修改 Agent 的工具、模型、提示词等
    """

    def __init__(self, config_file: Path, config_store=None):
        self.config_file = config_file
        self._config_store = config_store
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._config = self._load()

        # 文件监听器
        self._observer: Observer | None = None
        self._file_handler: _ConfigFileHandler | None = None
        self._running = False

        # 保存主线程事件循环引用，供 watchdog 线程安全调度
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None

    def _load(self) -> dict:
        """加载配置文件"""
        if self._config_store is not None:
            config = self._config_store.load()
        elif not self.config_file.exists():
            logger.warning(f"Agent 配置文件不存在: {self.config_file}，使用空配置启动")
            return {"agents": {}}
        else:
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"加载 agents 配置失败: {e}")
                raise

        try:
            # 验证配置格式
            if "agents" not in config:
                logger.error("配置文件缺少 'agents' 字段")
                raise ValueError("配置文件格式错误: 缺少 'agents' 字段")

            if not isinstance(config["agents"], dict):
                logger.error("配置文件 'agents' 字段必须是字典")
                raise ValueError("配置文件格式错误: 'agents' 字段必须是字典")

            logger.info(f"成功加载 {len(config['agents'])} 个 Agent 定义")
            return config

        except (TypeError, ValueError) as e:
            logger.error(f"加载 agents 配置失败: {e}")
            raise

    def _save_config(self, config: dict):
        """保存配置文件（原子写入：先写临时文件再替换，防止崩溃导致配置损坏）"""
        try:
            import os
            config["last_updated"] = datetime.now(timezone.utc).isoformat()
            if self._config_store is not None:
                self._config_store.save(config)
                logger.info("Agents 分层配置已保存")
                return
            tmp_path = str(self.config_file) + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.config_file)
            logger.info(f"Agents 配置已保存到 {self.config_file}")
        except (IOError, OSError) as e:
            logger.error(f"保存 agents 配置失败: {e}")
            raise

    def _save(self):
        """保存当前配置"""
        self._save_config(self._config)

    def get_agent_config(self, agent_type: str) -> dict | None:
        """
        获取指定 agent 的配置

        Args:
            agent_type: agent 类型

        Returns:
            agent 配置字典，不存在返回 None
        """
        with self._lock:
            return self._config.get("agents", {}).get(agent_type)

    def get_all_agents(self) -> dict[str, dict]:
        """
        获取所有 agent 配置

        Returns:
            {agent_type: config} 字典
        """
        with self._lock:
            return dict(self._config.get("agents", {}))

    def has_config(self) -> bool:
        """
        判断是否有有效的配置文件

        Returns:
            True 如果配置文件存在且有 agents
        """
        with self._lock:
            return len(self._config.get("agents", {})) > 0

    def update_agent(self, agent_type: str, updates: dict) -> bool:
        """
        更新 agent 定义

        Args:
            agent_type: agent 类型
            updates: 要更新的字段

        Returns:
            True 如果更新成功
        """
        try:
            with self._lock:
                agents = self._config.get("agents", {})

                if agent_type not in agents:
                    # 如果不存在，创建新配置
                    agents[agent_type] = {}

                agents[agent_type].update(updates)
                self._config["agents"] = agents
                self._save()
            logger.info(f"Agent {agent_type} 已更新")
            return True
        except Exception as e:
            logger.error(f"更新 agent 失败: {e}")
            return False

    def add_agent(self, agent_type: str, definition: dict) -> bool:
        """
        添加新 agent 定义

        Args:
            agent_type: agent 类型
            definition: agent 定义

        Returns:
            True 如果添加成功
        """
        try:
            with self._lock:
                agents = self._config.get("agents", {})

                if agent_type in agents:
                    logger.error(f"Agent {agent_type} 已存在")
                    return False

                # 设置默认值
                definition.setdefault("description", "")
                definition.setdefault("prompt_template", "subagent")
                definition.setdefault("tools", None)
                definition.setdefault("disallowed_tools", None)
                definition.setdefault("model", None)
                definition.setdefault("max_turns", 10)
                definition.setdefault("system_prompt_template", "")
                definition.setdefault("model_params", None)

                agents[agent_type] = definition
                self._config["agents"] = agents
                self._save()
            logger.info(f"已添加 agent {agent_type}")
            return True
        except Exception as e:
            logger.error(f"添加 agent 失败: {e}")
            return False

    def delete_agent(self, agent_type: str) -> bool:
        """
        删除 agent 定义（仅删除配置文件中的，内置定义不受影响）

        Args:
            agent_type: agent 类型

        Returns:
            True 如果删除成功
        """
        try:
            with self._lock:
                agents = self._config.get("agents", {})

                if agent_type not in agents:
                    logger.warning(f"Agent {agent_type} 不存在于配置中")
                    return False

                del agents[agent_type]
                self._config["agents"] = agents
                self._save()
            logger.info(f"已删除 agent {agent_type} 的配置")
            return True
        except Exception as e:
            logger.error(f"删除 agent 失败: {e}")
            return False

    def reload(self):
        """重新加载配置文件"""
        try:
            new_config = self._load()
            with self._lock:
                self._config = new_config
            logger.info("Agents 配置已重新加载")
            return True
        except Exception as e:
            logger.error(f"重新加载配置失败，使用上次的有效配置: {e}")
            return False

    def _on_config_file_changed(self):
        """配置文件变化时的回调"""
        logger.info("Agent 配置文件已变化，正在重新加载...")
        success = self.reload()

        # 通过 WebSocket 事件通知前端
        try:
            from src.web.event_bus import event_bus
            import asyncio

            async def _emit_event():
                await event_bus.emit_event({
                    "type": "system",
                    "subtype": "agent_config_reloaded",
                    "success": success,
                    "message": "Agent 配置已更新" if success else "Agent 配置重新加载失败，使用上次的有效配置"
                })

            # 从 watchdog 线程安全调度到主事件循环
            if self._main_loop is not None and self._main_loop.is_running():
                self._main_loop.call_soon_threadsafe(
                    asyncio.ensure_future, _emit_event()
                )
            else:
                # 移除 asyncio.run fallback：在无运行事件循环时创建新循环可能与主循环冲突
                logger.warning("无法发送配置重载事件：无运行中的事件循环")

        except Exception as e:
            logger.error(f"发送配置重新加载事件失败: {e}")

    def start_file_watcher(self, debounce_seconds: float = 1.0) -> bool:
        """
        启动文件监听器

        Args:
            debounce_seconds: 防抖时间（秒）

        Returns:
            True 如果启动成功
        """
        try:
            if self._running:
                logger.warning("文件监听器已在运行")
                return True

            # 创建文件处理器
            self._file_handler = _ConfigFileHandler(
                callback=self._on_config_file_changed,
                debounce_seconds=debounce_seconds
            )

            # 创建观察者
            self._observer = Observer()
            self._observer.schedule(
                self._file_handler,
                str(self.config_file.parent),
                recursive=False
            )

            # 启动观察者
            self._observer.start()
            self._running = True

            logger.info(f"Agent 配置文件监听器已启动，监控: {self.config_file}")
            return True

        except Exception as e:
            logger.error(f"启动文件监听器失败: {e}")
            return False

    def stop_file_watcher(self):
        """停止文件监听器"""
        try:
            if not self._running:
                return

            # 停止观察者
            if self._observer:
                self._observer.stop()
                self._observer.join(timeout=5)
                if self._observer.is_alive():
                    logger.warning("文件监听器停止超时（5s），线程可能仍在运行")
                self._observer = None

            # 取消待执行的回调
            if self._file_handler:
                self._file_handler.cancel()
                self._file_handler = None

            self._running = False
            logger.info("Agent 配置文件监听器已停止")

        except Exception as e:
            logger.error(f"停止文件监听器失败: {e}")

    def get_file_watcher_status(self) -> dict:
        """获取文件监听器状态"""
        observer_alive = self._observer.is_alive() if self._observer else False
        return {
            "running": self._running,
            "observer_alive": observer_alive,
            "config_file": str(self.config_file),
            "watched_dir": str(self.config_file.parent),
        }

    def get_config(self) -> dict:
        """获取完整配置"""
        return self._config.copy()
