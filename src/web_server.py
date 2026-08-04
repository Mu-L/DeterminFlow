"""
FastAPI Web 服务入口 - 初始化共享实例，注册路由和 WebSocket，启动 uvicorn

重构后：不再在 app_state 上挂 compiled_main 和 lc_messages，
Graph 编译和消息累积已下沉到 AgentSession 内部。
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.config import (
    AGENTS_CONFIG_FILE,
    BASE_DIR,
    CONFIG_DIR,
    LOGS_DIR,
    PLUGINS_DIR,
    PRESET_PHRASES_FILE,
    RULES_CONFIG_FILE,
    RULES_DIR,
    SKILLS_CONFIG_FILE,
    SKILLS_DIR,
    WORKFLOWS_DIR,
    ensure_dirs,
)
from src.extension_api import CoreRuntime
from src.extension_host import ExtensionManager, LayeredJsonConfig
from src.extension_host.gates import ExtensionMiddlewareGate, extension_route_guard
from src.extension_host.plugin_routes import router as plugin_router
from src.extension_host.routes import router as extension_router
from src.mcp.client import MCPClient
from src.core.llm_client import create_startup_llm
from src.core.workspace_manager import WorkspaceManager
from src.core.approval_manager import ApprovalManager
from src.prompts.manager import PromptManager
from src.prompts import create_orchestrator
from src.skills.manager import SkillManager
from src.agent.session_manager import SessionManager
from src.agent.session import AgentSession
from src.tools import ToolRegistry, register_all_tool_factories
from src.web.api_routes import router as api_router
from src.roundtable.routes import router as roundtable_router
from src.web.workflow_routes import router as workflow_router, tasks_router
from src.web.workflow_node_control_routes import router as workflow_node_control_router
from src.web.ws_handlers import handle_chat_ws, handle_events_ws

logger = logging.getLogger(__name__)


def _validate_prompt_template_references(agent_config_mgr, prompt_mgr):
    """校验所有 Agent 引用的 prompt_template 在 prompts_config.json 中均存在。缺失时仅记录警告，不阻止启动。"""
    available_templates = set(prompt_mgr.get_config().get("agents", {}).keys())
    agents = agent_config_mgr.get_all_agents()

    issues: list[str] = []
    for agent_type, agent_def in agents.items():
        pt = agent_def.get("prompt_template", "")
        if not pt:
            issues.append(f"  - Agent '{agent_type}' 缺少 prompt_template 字段")
        elif pt not in available_templates:
            issues.append(f"  - Agent '{agent_type}' 引用的模板 '{pt}' 不存在于 prompts_config.json")

    if issues:
        details = "\n".join(issues)
        logger.warning(
            f"prompt_template 校验发现以下问题（服务仍会启动）:\n{details}\n"
            f"可用的提示词模板: {', '.join(sorted(available_templates))}"
        )
    else:
        logger.info(f"prompt_template 交叉校验通过: {len(agents)} 个 Agent，{len(available_templates)} 个模板")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期管理，带错误隔离：初始化失败时清理已创建资源。"""

    setup_logging()
    ensure_dirs()
    extension_manager = app.state.extension_manager

    logger.info("正在初始化 Web 服务...")

    # 跟踪已创建的资源，用于失败时清理（按创建顺序）
    mcp = None
    agent_config_mgr = None
    session_mgr = None
    roundtable_mgr = None
    cron_scheduler = None
    workflow_mgr = None

    async def _cleanup_on_failure():
        """初始化失败时按创建顺序的逆序清理已分配资源。"""
        if workflow_mgr:
            try:
                await workflow_mgr.shutdown_task_recovery()
            except Exception:
                logger.debug("workflow_mgr 恢复计时器清理失败", exc_info=True)
        try:
            await extension_manager.stop()
        except Exception:
            logger.debug("extension_manager 清理失败", exc_info=True)
        if cron_scheduler:
            try:
                await cron_scheduler.stop()
            except Exception:
                logger.debug("cron_scheduler 清理失败", exc_info=True)
        if roundtable_mgr:
            try:
                await roundtable_mgr.shutdown()
            except Exception:
                logger.debug("roundtable_mgr 清理失败", exc_info=True)
        if session_mgr:
            try:
                await session_mgr.shutdown()
            except Exception:
                logger.debug("session_mgr 清理失败", exc_info=True)
        if agent_config_mgr:
            try:
                agent_config_mgr.stop_file_watcher()
            except Exception:
                logger.debug("agent_config_mgr 清理失败", exc_info=True)
        if mcp:
            try:
                await mcp.close()
            except Exception:
                logger.debug("mcp 清理失败", exc_info=True)

    try:
        mcp = MCPClient()
        await mcp.connect_all()
        logger.info(f"MCP Server 已连接，加载了 {len(mcp.get_tools())} 个工具")

        # 初始化 SkillManager
        from src.skills.config_manager import SkillConfigManager
        skill_config_store = LayeredJsonConfig(
            SKILLS_CONFIG_FILE,
            extension_manager.resource_paths("skills"),
            dict_sections=("skills", "skill_configs"),
            list_sections=("groups",),
            owner_enabled=extension_manager.is_running,
        )
        skill_config_mgr = SkillConfigManager(SKILLS_CONFIG_FILE, config_store=skill_config_store)
        skill_mgr = SkillManager(
            SKILLS_DIR,
            skill_config_mgr,
            resource_roots=extension_manager.resource_paths("skill_bundles"),
            owner_enabled=extension_manager.is_running,
        )
        skill_mgr.initialize_if_empty()
        logger.info(f"SkillManager 已初始化，加载了 {skill_mgr.get_stats()['total']} 个 skills")

        # 初始化 RuleManager
        from src.rules.config_manager import RuleConfigManager
        from src.rules.manager import RuleManager
        rule_config_store = LayeredJsonConfig(
            RULES_CONFIG_FILE,
            extension_manager.resource_paths("rules"),
            dict_sections=("rules", "rule_configs"),
            list_sections=("groups",),
            owner_enabled=extension_manager.is_running,
        )
        rule_config_mgr = RuleConfigManager(RULES_CONFIG_FILE, config_store=rule_config_store)
        rule_mgr = RuleManager(
            RULES_DIR,
            rule_config_mgr,
            resource_roots=extension_manager.resource_paths("rule_bundles"),
            owner_enabled=extension_manager.is_running,
        )
        rule_mgr.initialize_if_empty()
        logger.info(f"RuleManager 已初始化，加载了 {rule_mgr.get_stats()['total']} 个 rules")

        # 初始化 AgentConfigManager
        from src.agent.config_manager import AgentConfigManager
        from src.agent.definition import set_agent_config_manager
        agent_config_store = LayeredJsonConfig(
            AGENTS_CONFIG_FILE,
            extension_manager.resource_paths("agents"),
            dict_sections=("agents",),
            owner_enabled=extension_manager.is_running,
        )
        agent_config_mgr = AgentConfigManager(AGENTS_CONFIG_FILE, config_store=agent_config_store)
        set_agent_config_manager(agent_config_mgr)
        logger.info(f"AgentConfigManager 已初始化，has_config={agent_config_mgr.has_config()}")

        # 启动 Agent 配置文件监听
        agent_config_mgr.start_file_watcher(debounce_seconds=1.0)

        from src.config import PROMPTS_CONFIG_FILE
        prompt_config_store = LayeredJsonConfig(
            PROMPTS_CONFIG_FILE,
            extension_manager.resource_paths("prompts"),
            dict_sections=("agents",),
            owner_enabled=extension_manager.is_running,
        )
        prompt_mgr = PromptManager(config_store=prompt_config_store)
        from src.session import PromptBuilder

        prompt_builder = PromptBuilder(
            prompt_manager=prompt_mgr,
            skill_manager=skill_mgr,
            rule_manager=rule_mgr,
        )
        preset_phrases_store = LayeredJsonConfig(
            PRESET_PHRASES_FILE,
            extension_manager.resource_paths("preset_phrases"),
            list_sections=("phrases",),
            owner_enabled=extension_manager.is_running,
        )
        preset_phrases_store.load()

        prompt_orchestrator = create_orchestrator(prompt_mgr, skill_mgr, rule_mgr)
        session_mgr = SessionManager()

        # 初始化 WorkspaceManager 和 ApprovalManager
        workspace_mgr = WorkspaceManager()
        approval_mgr = ApprovalManager()
        session_mgr.inject_dependencies(
            workspace_manager=workspace_mgr,
            approval_manager=approval_mgr,
            mcp_client=mcp,
            extension_manager=extension_manager,
            agent_config_manager=agent_config_mgr,
        )

        session_mgr.load_sessions()

        llm = create_startup_llm(streaming=True)

        # 初始化 WorkflowManager（必须在 ToolRegistry 之前，因为工厂闭包通过 session_mgr._workflow_manager 获取）
        from src.workflow.manager import WorkflowManager
        from src.workflow.runtime import WorkflowRuntimeFacade
        from src.workflow.script_library import configure_script_library
        extension_manager.provision_workflows(WORKFLOWS_DIR)
        configure_script_library(
            extension_manager.resource_paths("script_libraries"),
            owner_enabled=extension_manager.is_running,
            owner_environment=extension_manager.plugin_environment,
            owner_revision=extension_manager.resource_revision,
        )
        workflow_mgr = WorkflowManager(session_mgr, extension_manager=extension_manager)
        workflow_runtime = WorkflowRuntimeFacade(
            workflow_mgr,
            prompt_manager=prompt_mgr,
            prompt_builder=prompt_builder,
        )
        session_mgr.inject_dependencies(workflow_manager=workflow_mgr)
        logger.info("WorkflowManager 已初始化")

        # 初始化 ToolRegistry 并统一注册所有工具工厂
        registry = ToolRegistry("config/tool_groups_config.json")
        register_all_tool_factories(registry,
            mcp_client=mcp, session_manager=session_mgr,
            prompt_manager=prompt_mgr,
            skill_manager=skill_mgr, rule_manager=rule_mgr,
            approval_manager=approval_mgr, llm_client=llm)
        runtime = CoreRuntime(
            app=app,
            session_manager=session_mgr,
            workflow_runtime=workflow_runtime,
            tool_registry=registry,
            event_publisher=None,
            services={
                "mcp_client": mcp,
                "agent_config_manager": agent_config_mgr,
                "prompt_manager": prompt_mgr,
                "skill_manager": skill_mgr,
                "rule_manager": rule_mgr,
                "approval_manager": approval_mgr,
                "workspace_manager": workspace_mgr,
                "llm": llm,
            },
        )
        await extension_manager.start(runtime)
        agent_config_mgr.reload()
        skill_mgr.reload()
        rule_mgr.reload()
        prompt_mgr.reload()
        _validate_prompt_template_references(agent_config_mgr, prompt_mgr)
        logger.info(f"ToolRegistry 已初始化，注册了 {len(registry.get_tools())} 个工具元数据 + {len(registry.get_factory_names())} 个工厂")

        # 一次性创建全部功能工具实例（用于启动时的主会话 Graph）
        all_tools = registry.instantiate_all(
            session_manager=session_mgr, llm_client=llm,
            skill_manager=skill_mgr, rule_manager=rule_mgr,
            approval_manager=approval_mgr,
            workflow_manager=workflow_mgr)

        # 注入 PromptBuilder 和 ToolAssembler
        from src.session import ToolAssembler
        tool_assembler = ToolAssembler(
            session_manager=session_mgr,
            registry=registry,
            approval_manager=approval_mgr,
            skill_manager=skill_mgr,
            rule_manager=rule_mgr,
            llm_client=llm,
        )
        session_mgr.set_builders(prompt_builder, tool_assembler)
        logger.info("PromptBuilder 和 ToolAssembler 已注入到 SessionManager")

        recovery_summary = await workflow_mgr.recover_workflow_tasks()
        logger.info("Workflow 任务恢复完成: %s", recovery_summary)

        # 检查是否有可恢复的主会话（多 main 架构：恢复所有主会话）
        recoverable_mains = [
            s for s in session_mgr.sessions.values()
            if s.session_type == "main" and s.status in ("running", "error", "completed")
        ]

        from src.core.model_manager import get_model_manager
        from src.agent.definition import get_agent_definition

        model_manager = get_model_manager()
        configured_models = set(model_manager.get_all_models())

        if recoverable_mains:
            for i, existing_main in enumerate(recoverable_mains):
                logger.info(f"恢复主会话 [{i+1}/{len(recoverable_mains)}]: {existing_main.session_id}")
                agent_type = existing_main.agent_type or "main"
                agent_def = get_agent_definition(agent_type)
                extension_context = await session_mgr._build_extension_prompt_context(
                    agent_type,
                    agent_def,
                )
                system_prompt = prompt_builder.build(
                    agent_type,
                    session=existing_main,
                    tools=all_tools,
                    extension_context=extension_context,
                )
                existing_main.refresh_system_prompt(system_prompt)
                existing_main.status = "running"
                # 保留会话自己的选择；已删除的旧模型回退到当前首个模型。
                session_model = existing_main.model_id
                if session_model not in configured_models:
                    session_model = agent_def.model if agent_def else None
                if session_model not in configured_models:
                    session_model = model_manager.get_default_model()
                if not existing_main.model_params and agent_def:
                    existing_main.model_params = dict(agent_def.model_params or {})
                session_llm = create_startup_llm(
                    model_override=session_model,
                    streaming=True,
                    model_params=existing_main.model_params,
                )
                existing_main.model_id = session_model
                existing_main.setup_graph(llm=session_llm, tools=all_tools)
                existing_main.start_consumer()
                if i == 0:
                    session_mgr.register_main(existing_main)  # 首个设为 Chat WS 默认绑定
                else:
                    session_mgr.sessions[existing_main.session_id] = existing_main  # 注册但不改 main_session_id
                logger.info(f"主会话 {existing_main.session_id} 已恢复，Graph 已重新编译，消费循环已启动")
        else:
            # 创建新的 Main Session
            agent_def = get_agent_definition("main")
            main_session = AgentSession(
                session_type="main",
                agent_type="main",
                model_params=agent_def.model_params if agent_def else None,
            )
            extension_context = await session_mgr._build_extension_prompt_context(
                "main",
                agent_def,
            )
            system_prompt = prompt_builder.build(
                "main",
                session=main_session,
                tools=all_tools,
                extension_context=extension_context,
            )
            main_session.refresh_system_prompt(system_prompt)
            main_llm = create_startup_llm(
                model_override=agent_def.model if agent_def else None,
                streaming=True,
                model_params=main_session.model_params,
            )
            main_session.setup_graph(llm=main_llm, tools=all_tools)
            # 设置会话的模型标识（用于子会话继承）
            main_session.model_id = model_manager.get_default_model()
            main_session.start_consumer()
            session_mgr.register_main(main_session)
            logger.info("Main Session 已创建，Graph 已编译，消费循环已启动")

        from src.roundtable.runner import RoundtableManager
        roundtable_mgr = RoundtableManager()
        roundtable_mgr.load_sessions()

        # 初始化 CronScheduler
        from src.cron.jobs import CronJobManager
        from src.cron.scheduler import CronScheduler
        cron_job_mgr = CronJobManager()
        cron_scheduler = CronScheduler(session_mgr, cron_job_mgr)
        session_mgr.inject_dependencies(
            cron_scheduler=cron_scheduler,
            cron_job_manager=cron_job_mgr,
        )
        await cron_scheduler.start()
        logger.info("CronScheduler 已初始化并启动")

        # 挂载到 app.state（移除了 compiled_main 和 lc_messages）
        app.state.mcp_client = mcp
        app.state.prompt_manager = prompt_mgr
        app.state.preset_phrases_store = preset_phrases_store
        app.state.prompt_orchestrator = prompt_orchestrator
        app.state.agent_config_manager = agent_config_mgr
        app.state.skill_manager = skill_mgr
        app.state.rule_manager = rule_mgr
        app.state.session_manager = session_mgr
        app.state.llm = llm
        app.state.all_tools = all_tools
        app.state.roundtable_manager = roundtable_mgr
        app.state.workspace_manager = workspace_mgr
        app.state.approval_manager = approval_mgr
        app.state.tool_registry = registry
        app.state.workflow_manager = workflow_mgr
        app.state.cron_scheduler = cron_scheduler
        app.state.cron_job_manager = cron_job_mgr

        # 将 skill_manager 和 rule_manager 注入到 session_manager（用于 create_main_session）
        session_mgr._skill_manager = skill_mgr
        session_mgr._rule_manager = rule_mgr

        main_session = session_mgr.get_main_session()
        logger.info(f"Web 服务初始化完成，主会话: {main_session.session_id if main_session else '无'}")

        # 启动全局 Session 定时刷盘管理器（内存缓存 + 5s 定时落盘）
        from src.agent.session import _persistence_manager
        _persistence_manager.start()
        logger.info("SessionPersistenceManager 已启动（5s 定时刷盘）")

    except Exception:
        logger.error("Web 服务初始化失败，正在清理已创建的资源...", exc_info=True)
        await _cleanup_on_failure()
        raise

    yield

    logger.info("正在关闭 Web 服务...")
    try:
        if workflow_mgr:
            await workflow_mgr.shutdown_task_recovery()
    except Exception:
        logger.warning("关闭 Workflow 重试计时器时忽略异常", exc_info=True)
    try:
        await extension_manager.stop()
    except Exception:
        logger.warning("关闭 ExtensionManager 时忽略异常", exc_info=True)
    for name, coro_fn in [
        ("AgentConfigManager.stop_file_watcher", lambda: agent_config_mgr.stop_file_watcher()),
        ("CronScheduler.stop", lambda: cron_scheduler.stop()),
        ("SessionManager.shutdown", lambda: session_mgr.shutdown()),
        ("RoundtableManager.shutdown", lambda: roundtable_mgr.shutdown()),
        ("MCPClient.close", lambda: mcp.close()),
    ]:
        try:
            result = coro_fn()
            if hasattr(result, "__await__"):
                await result
        except Exception:
            logger.warning(f"关闭 {name} 时忽略异常", exc_info=True)
    logger.info("Web 服务已关闭")


def setup_logging():
    from datetime import datetime
    from logging.handlers import RotatingFileHandler
    ensure_dirs()
    log_file = LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}-web.log"

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)

    # 使用 RotatingFileHandler 防止日志文件无限增长（10MB × 5 份）
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            file_handler,
            logging.StreamHandler(),
        ],
    )

    # 抑制 httpx / urllib3 / asyncio / watchdog / openai 的 DEBUG 噪音
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "watchfiles",
                   "watchdog", "openai", "openai._base_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ============ FastAPI 应用 ============

# CORS 配置：从环境变量 CORS_ORIGINS 读取（逗号分隔），默认允许本地开发地址
_cors_origins_str = os.environ.get("CORS_ORIGINS", "")
if _cors_origins_str:
    _cors_origins = [o.strip() for o in _cors_origins_str.split(",") if o.strip()]
else:
    # 默认允许 localhost 和 127.0.0.1 常见端口，以及局域网访问
    _cors_origins = [
        "http://localhost:3000", "http://localhost:8020",
        "http://127.0.0.1:3000", "http://127.0.0.1:8020",
        "http://localhost:5173",  # Vite 默认端口
    ]
    # 如果 CORS_ORIGINS=* 则允许所有来源（仅用于开发环境）
    # 注意：allow_origins=["*"] 与 allow_credentials=True 组合违反 CORS 规范，
    # 浏览器会拒绝携带凭证的跨域请求。因此使用通配符时禁用 credentials。
_use_wildcard = _cors_origins_str == "*" or os.environ.get("CORS_ALLOW_ALL", "").lower() in ("true", "1", "yes")
if _use_wildcard:
    _cors_origins = ["*"]
    _cors_credentials = False  # CORS 规范不允许 * 与 credentials 同时使用
else:
    _cors_credentials = True

def create_app(extension_manager: ExtensionManager | None = None) -> FastAPI:
    """Build an application with an explicit set of installed extensions."""
    manager = extension_manager or ExtensionManager(
        BASE_DIR,
        config_file=CONFIG_DIR / "extensions.json",
        workflows_dir=WORKFLOWS_DIR,
        plugins_dir=PLUGINS_DIR,
        plugin_logs_dir=LOGS_DIR / "plugins",
    )
    application = FastAPI(
        title="DeterminFlow",
        description="可恢复、可审计的 AI 工作流运行框架",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.extension_manager = manager

    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=_cors_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for owner, middleware, options in manager.middleware:
        application.add_middleware(
            ExtensionMiddlewareGate,
            manager=manager,
            owner=owner,
            middleware=middleware,
            middleware_options=options,
        )

    application.include_router(api_router)
    application.include_router(roundtable_router)
    application.include_router(workflow_router)
    application.include_router(tasks_router)
    application.include_router(workflow_node_control_router)
    application.include_router(extension_router)
    application.include_router(plugin_router)
    for owner, router in manager.routers:
        application.include_router(
            router,
            dependencies=[Depends(extension_route_guard(manager, owner))],
        )

    @application.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket):
        await handle_chat_ws(ws, application.state)

    @application.websocket("/ws/events")
    async def ws_events(ws: WebSocket):
        await handle_events_ws(ws, application.state)

    web_dist = BASE_DIR / "web" / "dist"
    if web_dist.exists():
        application.mount("/", StaticFiles(directory=str(web_dist), html=True), name="static")
    return application


app = create_app()
