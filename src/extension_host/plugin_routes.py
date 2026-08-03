"""Management and static-page routes for installable Plugin Packages."""

from __future__ import annotations

import ipaddress
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.plugin_system import PluginStoreError, SourceTrustError
from src.environment import get_determinflow_env


router = APIRouter(prefix="/api/plugins", tags=["plugins"])


class InstallPluginRequest(BaseModel):
    plugin_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1)
    ref: str = "HEAD"
    subdirectory: str = ""
    resource_prefix: str | None = Field(default=None, max_length=128)
    acknowledge_risk: bool = False


class EnabledRequest(BaseModel):
    enabled: bool


class UpdatePluginRequest(BaseModel):
    ref: str | None = None


class ConfigRequest(BaseModel):
    settings: dict[str, Any]


class PluginSourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=4096)
    ref: str = Field(default="HEAD", min_length=1, max_length=512)


def _management(request: Request):
    return request.app.state.extension_manager.plugin_management


def _is_loopback_request(request: Request) -> bool:
    if request.client is None:
        return False
    host = request.client.host.split("%", 1)[0]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def require_plugin_write_access(request: Request) -> None:
    """Allow local administration, or require the configured remote token."""
    configured_token = get_determinflow_env("PLUGIN_ADMIN_TOKEN", "") or ""
    if not configured_token:
        if _is_loopback_request(request):
            return
        raise HTTPException(
            status_code=403,
            detail=(
                "远程 Plugin 管理默认关闭；请配置 "
                "DETERMINFLOW_PLUGIN_ADMIN_TOKEN 并使用 Bearer token"
            ),
        )
    scheme, supplied_token = get_authorization_scheme_param(
        request.headers.get("Authorization")
    )
    if (
        scheme.lower() != "bearer"
        or not supplied_token
        or not secrets.compare_digest(supplied_token, configured_token)
    ):
        raise HTTPException(
            status_code=401,
            detail="Plugin 管理令牌无效",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _mutation_response(manager, plugin: dict[str, Any], message: str):
    return {
        "plugin": plugin,
        "restart_required": manager.restart_required(),
        "message": message,
    }


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, SourceTrustError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (PluginStoreError, ValueError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


@router.get("")
async def list_plugins(request: Request):
    try:
        return _management(request).list_response()
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/catalog")
async def list_plugin_catalog(request: Request, refresh: bool = False):
    try:
        return await run_in_threadpool(
            _management(request).catalog_response,
            refresh=refresh,
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/sources")
async def list_plugin_sources(request: Request):
    try:
        return _management(request).sources_response()
    except Exception as exc:
        _raise_http_error(exc)


@router.post(
    "/sources",
    dependencies=[Depends(require_plugin_write_access)],
)
async def create_plugin_source(
    payload: PluginSourceRequest,
    request: Request,
):
    manager = _management(request)
    try:
        return await run_in_threadpool(
            manager.create_source,
            name=payload.name,
            url=payload.url,
            ref=payload.ref,
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.put(
    "/sources/{source_id}",
    dependencies=[Depends(require_plugin_write_access)],
)
async def update_plugin_source(
    source_id: str,
    payload: PluginSourceRequest,
    request: Request,
):
    manager = _management(request)
    try:
        return await run_in_threadpool(
            manager.update_source,
            source_id,
            name=payload.name,
            url=payload.url,
            ref=payload.ref,
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.delete(
    "/sources/{source_id}",
    dependencies=[Depends(require_plugin_write_access)],
)
async def delete_plugin_source(source_id: str, request: Request):
    manager = _management(request)
    try:
        return await run_in_threadpool(manager.delete_source, source_id)
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/install", dependencies=[Depends(require_plugin_write_access)])
async def install_plugin(payload: InstallPluginRequest, request: Request):
    manager = _management(request)
    try:
        plugin = await run_in_threadpool(
            manager.install,
            payload.plugin_id,
            payload.source,
            ref=payload.ref,
            subdirectory=payload.subdirectory,
            resource_prefix=payload.resource_prefix,
            acknowledge_risk=payload.acknowledge_risk,
        )
        return _mutation_response(manager, plugin, "Plugin 已安装，重启后生效")
    except Exception as exc:
        _raise_http_error(exc)


@router.put(
    "/{plugin_id}/enabled",
    dependencies=[Depends(require_plugin_write_access)],
)
async def set_plugin_enabled(
    plugin_id: str,
    payload: EnabledRequest,
    request: Request,
):
    manager = _management(request)
    try:
        plugin = manager.set_enabled(plugin_id, payload.enabled)
        return _mutation_response(manager, plugin, "目标启用状态已保存")
    except Exception as exc:
        _raise_http_error(exc)


@router.post(
    "/{plugin_id}/update",
    dependencies=[Depends(require_plugin_write_access)],
)
async def update_plugin(
    plugin_id: str,
    request: Request,
    payload: UpdatePluginRequest | None = None,
):
    manager = _management(request)
    try:
        plugin = await run_in_threadpool(
            manager.update,
            plugin_id,
            ref=payload.ref if payload is not None else None,
        )
        return _mutation_response(manager, plugin, "Plugin 更新已准备")
    except Exception as exc:
        _raise_http_error(exc)


@router.post(
    "/{plugin_id}/rollback",
    dependencies=[Depends(require_plugin_write_access)],
)
async def rollback_plugin(plugin_id: str, request: Request):
    manager = _management(request)
    try:
        plugin = manager.rollback(plugin_id)
        return _mutation_response(manager, plugin, "Plugin 回退已准备")
    except Exception as exc:
        _raise_http_error(exc)


@router.put(
    "/{plugin_id}/config",
    dependencies=[Depends(require_plugin_write_access)],
)
async def save_plugin_config(
    plugin_id: str,
    payload: ConfigRequest,
    request: Request,
):
    manager = _management(request)
    try:
        plugin = manager.save_config(plugin_id, payload.settings)
        return _mutation_response(manager, plugin, "Plugin 配置已保存")
    except Exception as exc:
        _raise_http_error(exc)


@router.delete(
    "/{plugin_id}/config",
    dependencies=[Depends(require_plugin_write_access)],
)
async def reset_plugin_config(plugin_id: str, request: Request):
    manager = _management(request)
    try:
        plugin = manager.reset_config(plugin_id)
        return _mutation_response(manager, plugin, "Plugin 配置已重置")
    except Exception as exc:
        _raise_http_error(exc)


@router.delete(
    "/{plugin_id}",
    dependencies=[Depends(require_plugin_write_access)],
)
async def uninstall_plugin(plugin_id: str, request: Request):
    manager = _management(request)
    try:
        plugin = manager.uninstall(plugin_id)
        return _mutation_response(manager, plugin, "Plugin 将在重启后卸载")
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/{plugin_id}/ui")
@router.get("/{plugin_id}/ui/{asset_path:path}")
async def plugin_static_page(
    plugin_id: str,
    request: Request,
    asset_path: str = "",
):
    try:
        path = _management(request).static_file(plugin_id, asset_path)
        return FileResponse(path)
    except Exception as exc:
        _raise_http_error(exc)
