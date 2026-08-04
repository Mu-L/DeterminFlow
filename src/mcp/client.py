"""
MCP客户端 - 支持同时连接多个 MCP Server，通过工具路由表自动将调用分发到正确的 Server

特性：
- 多 Server 连接管理（connections dict）
- 工具名到 Server 名的路由映射
- 外部 MCP Server（通用 command/args/env）
- 外部 Server 通过 config/mcp_servers.json（标准 MCP 格式）配置
- 外部 Server 连接失败不阻塞系统启动
"""
import sys
import json
import asyncio
import logging
from pathlib import Path
from contextlib import AsyncExitStack

from mcp import StdioServerParameters, ClientSession
from mcp.client.stdio import stdio_client

from src.environment import get_determinflow_env

logger = logging.getLogger(__name__)


class MCPClient:
    """MCP客户端，管理与多个 MCP Server 的 stdio 连接"""

    def __init__(self):
        # 多 Server 连接池：server_name → ClientSession
        self.connections: dict[str, ClientSession] = {}
        self._exit_stacks: dict[str, AsyncExitStack] = {}
        # 工具路由表：tool_name → server_name
        self._tool_routing: dict[str, str] = {}
        # 全局工具列表（MCP 原始格式）
        self._tools: list[dict] = []

        # 向后兼容：保留 session 属性（指向第一个连接的 Server）
        self._primary_server: str | None = None

    @property
    def session(self) -> ClientSession | None:
        """向后兼容：返回主 Server 的 session"""
        if self._primary_server:
            return self.connections.get(self._primary_server)
        return None

    async def connect(self, server_name: str, *,
                      server_script: str | None = None,
                      command: str | None = None,
                      args: list[str] | None = None,
                      env: dict | None = None):
        """启动一个 MCP Server 子进程并建立连接。

        支持两种模式：
        - server_script 模式：传入 Python 脚本路径，自动用 sys.executable 启动（内置 server）
        - command 模式：传入可执行文件路径 + args + env（外部 MCP Server，支持任意语言）

        Args:
            server_name: Server 标识名（如 "knowledge", "browser"）
            server_script: Python 脚本路径（内置 server 用），与 command 二选一
            command: 可执行文件路径（外部 Server 用），与 server_script 二选一
            args: 命令行参数列表
            env: 环境变量字典
        """
        if server_script is not None and command is not None:
            raise ValueError(
                f"Server '{server_name}': server_script 和 command 互斥，只能提供其一"
            )
        if server_script is None and command is None:
            raise ValueError(
                f"Server '{server_name}': 必须提供 server_script 或 command"
            )

        if server_name in self.connections:
            logger.warning(f"MCP Server '{server_name}' 已连接，跳过重复连接")
            return

        CONNECT_TIMEOUT = 30  # 连接超时秒数

        if server_script is not None:
            # 内置 Python MCP Server 模式
            server_params = StdioServerParameters(
                command=sys.executable,
                args=[server_script],
                env=None,
            )
            source_label = server_script
        else:
            # 外部 MCP Server 模式（通用 command + args + env）
            server_params = StdioServerParameters(
                command=command,
                args=args or [],
                env=env,
            )
            source_label = command

        exit_stack = AsyncExitStack()
        self._exit_stacks[server_name] = exit_stack

        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                read_stream, write_stream = await exit_stack.enter_async_context(
                    stdio_client(server_params)
                )

                session = await exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )

                await session.initialize()
        except TimeoutError:
            logger.error(f"MCP Server '{server_name}' 连接超时 ({CONNECT_TIMEOUT}s)，清理资源")
            try:
                await exit_stack.aclose()
            except Exception:
                logger.warning(f"MCP Server '{server_name}' 超时清理资源时异常", exc_info=True)
            del self._exit_stacks[server_name]
            raise
        except Exception as original_err:
            logger.error(f"MCP Server '{server_name}' 连接失败，清理资源")
            try:
                await exit_stack.aclose()
            except Exception:
                logger.warning(f"MCP Server '{server_name}' 清理资源时异常（原始错误: {original_err}）", exc_info=True)
            del self._exit_stacks[server_name]
            raise

        self.connections[server_name] = session

        # 第一个连接的 Server 作为主 Server（向后兼容）
        if self._primary_server is None:
            self._primary_server = server_name

        logger.info(f"MCP Server '{server_name}' 已连接并初始化 ({source_label})")

        # 刷新该 Server 的工具列表
        await self.refresh_tools(server_name)

    async def connect_all(self):
        """启动时连接所有已配置的 Server。

        从 config/mcp_servers.json 加载外部 Server 并逐一连接。
        外部 Server 连接失败时记录警告并继续，不阻塞系统启动。
        若中途发生未预期异常（如 BaseException），会清理已建立的连接避免泄漏。
        """
        try:
            # Coding 工具已迁移为 src/tools/coding_tools.py 中的主进程直接实现，
            # 不再需要 MCP coding_server 子进程。coding_server.py 保留为档案参考。

            # 加载外部 MCP Server 配置
            mcp_config_path = self._resolve_config_path()
            if mcp_config_path is None:
                return

            try:
                with open(mcp_config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"无法解析 MCP Server 配置文件 {mcp_config_path}: {e}，跳过外部 Server")
                return

            servers = config_data.get("mcpServers", {})
            if not servers:
                return

            for name, cfg in servers.items():
                try:
                    await self.connect(
                        name,
                        command=cfg["command"],
                        args=cfg.get("args", []),
                        env=cfg.get("env"),
                    )
                except Exception as e:
                    logger.warning(
                        f"MCP Server '{name}' 连接失败: {type(e).__name__}: {e}，跳过"
                    )
        except BaseException:
            logger.error("connect_all 发生未预期异常，正在清理已建立的连接", exc_info=True)
            await self.close()
            raise

    def _resolve_config_path(self) -> Path | None:
        """解析 MCP Server 配置文件路径。

        查找优先级：
        1. 项目 config/ 目录下的 mcp_servers.json
        2. 未找到时返回 None（跳过外部 Server 加载）
        """
        project_root = Path(__file__).parent.parent.parent
        config_root = Path(
            get_determinflow_env("CONFIG_DIR", str(project_root / "config"))
        ).expanduser().resolve()
        project_config = config_root / "mcp_servers.json"
        if project_config.exists():
            return project_config

        return None

    async def refresh_tools(self, server_name: str | None = None):
        """刷新指定或全部 Server 的工具列表

        Args:
            server_name: 指定 Server 名，None 表示刷新全部
        """
        servers = [server_name] if server_name else list(self.connections.keys())

        for name in servers:
            session = self.connections.get(name)
            if not session:
                continue

            result = await session.list_tools()

            # 先移除该 Server 的旧工具
            self._tools = [t for t in self._tools if self._tool_routing.get(t["name"]) != name]
            for tool_name in list(self._tool_routing.keys()):
                if self._tool_routing[tool_name] == name:
                    del self._tool_routing[tool_name]

            # 添加新工具
            for tool in result.tools:
                # 检查工具名冲突
                if tool.name in self._tool_routing:
                    existing_server = self._tool_routing[tool.name]
                    logger.error(f"工具名冲突: '{tool.name}' 同时存在于 '{existing_server}' 和 '{name}'")
                    raise ValueError(f"工具名冲突: '{tool.name}' 同时存在于 '{existing_server}' 和 '{name}'")

                self._tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                    "server": name,
                })
                self._tool_routing[tool.name] = name

            logger.info(f"Server '{name}' 已加载 {len(result.tools)} 个工具")

        logger.info(f"工具总数: {len(self._tools)} (路由表: {len(self._tool_routing)} 条)")

    def get_tools(self, server_name: str | None = None) -> list[dict]:
        """获取工具列表（MCP原始格式）

        Args:
            server_name: 按 Server 过滤，None 返回全部
        """
        if server_name:
            return [t for t in self._tools if t.get("server") == server_name]
        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> str:
        """调用MCP工具并返回结果字符串

        自动根据路由表找到正确的 Server。
        """
        server_name = self._tool_routing.get(name)
        if not server_name:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)

        session = self.connections.get(server_name)
        if not session:
            return json.dumps({"error": f"Server '{server_name}' 未连接"}, ensure_ascii=False)

        try:
            result = await session.call_tool(name, arguments)
            if result.content:
                texts = []
                for item in result.content:
                    if hasattr(item, "text"):
                        texts.append(item.text)
                    else:
                        texts.append(str(item))
                return "\n".join(texts)
            return json.dumps({"message": "工具执行完成，无返回内容"}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"调用工具 {name} (server={server_name}) 失败: {e}", exc_info=True)
            return json.dumps({"error": "工具调用失败，请查看服务日志"}, ensure_ascii=False)

    async def close(self):
        """关闭所有连接"""
        for name in list(self._exit_stacks.keys()):
            exit_stack = self._exit_stacks.pop(name, None)
            self.connections.pop(name, None)
            if exit_stack:
                try:
                    await exit_stack.aclose()
                except (asyncio.CancelledError, Exception) as e:
                    # MCP stdio_client 使用 anyio cancel scope，
                    # 在 shutdown 阶段关闭时可能出现 CancelledError 或
                    # cancel scope 跨 task 退出错误，这里安全忽略。
                    # 不捕获 KeyboardInterrupt/SystemExit，保留进程退出信号传播。
                    logger.warning(f"关闭 Server '{name}' 连接时忽略异常: {type(e).__name__}")
        self.connections.clear()
        self._exit_stacks.clear()
        self._tool_routing.clear()
        self._tools.clear()
        self._primary_server = None

        # 给 MCP 库内部的 aiohttp session 足够时间完成异步清理，
        # 避免 shutdown 时 asyncio 报告 "Unclosed client session"
        await asyncio.sleep(0.1)

        logger.info("所有 MCP 连接已关闭")
