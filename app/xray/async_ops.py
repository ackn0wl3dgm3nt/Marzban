"""
Async wrappers for XrayManager operations.

Use these in async contexts (FastAPI routes with BackgroundTasks) instead of
the repeated if/else pattern.

Example:
    from app.xray import async_ops
    bg.add_task(async_ops.update_user, dbuser)
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models import User as DBUser


async def add_user(dbuser: "DBUser") -> None:
    """Add user to xray. Uses XrayManager if available, otherwise falls back to legacy."""
    from app import xray

    if xray.xray_manager.is_started:
        await xray.xray_manager.add_user(dbuser)
    else:
        xray.operations.add_user(dbuser)


async def update_user(dbuser: "DBUser") -> None:
    """Update user in xray. Uses XrayManager if available, otherwise falls back to legacy."""
    from app import xray

    if xray.xray_manager.is_started:
        await xray.xray_manager.update_user(dbuser)
    else:
        xray.operations.update_user(dbuser)


async def remove_user(dbuser: "DBUser") -> None:
    """Remove user from xray. Uses XrayManager if available, otherwise falls back to legacy."""
    from app import xray

    if xray.xray_manager.is_started:
        await xray.xray_manager.remove_user(dbuser)
    else:
        xray.operations.remove_user(dbuser)
