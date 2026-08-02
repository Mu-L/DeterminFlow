"""Read-only Extension Host status API."""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/extensions", tags=["extensions"])


@router.get("")
async def list_extensions(request: Request):
    manager = request.app.state.extension_manager
    extensions = manager.get_statuses()
    return {
        "extensions": extensions,
        "enabled": [item["id"] for item in extensions if item["enabled"]],
    }
