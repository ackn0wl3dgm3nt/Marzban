"""
Synchronous wrappers for XrayManager operations.

Use these in sync contexts (APScheduler jobs, Telegram handlers) instead of
the repeated if/else pattern with asyncio.run().

Example:
    from app.xray import sync_ops
    sync_ops.update_user(dbuser)
"""
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models import User as DBUser


def add_user(dbuser: "DBUser") -> None:
    """Add user to xray. Uses XrayManager if available, otherwise falls back to legacy."""
    from app import xray

    if xray.xray_manager.is_started:
        asyncio.run(xray.xray_manager.add_user(dbuser))
    else:
        xray.operations.add_user(dbuser)


def update_user(dbuser: "DBUser") -> None:
    """Update user in xray. Uses XrayManager if available, otherwise falls back to legacy."""
    from app import xray

    if xray.xray_manager.is_started:
        asyncio.run(xray.xray_manager.update_user(dbuser))
    else:
        xray.operations.update_user(dbuser)


def remove_user(dbuser: "DBUser") -> None:
    """Remove user from xray. Uses XrayManager if available, otherwise falls back to legacy."""
    from app import xray

    if xray.xray_manager.is_started:
        asyncio.run(xray.xray_manager.remove_user(dbuser))
    else:
        xray.operations.remove_user(dbuser)
