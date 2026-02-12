from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .base import Base, SessionLocal, engine, AsyncSessionLocal  # noqa


class GetDB:  # Context Manager
    def __init__(self):
        self.db = SessionLocal()

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc_value, traceback):
        if isinstance(exc_value, SQLAlchemyError):
            self.db.rollback()  # rollback on exception

        self.db.close()


def get_db():  # Dependency
    with GetDB() as db:
        yield db


async def get_async_db():
    """Async DB dependency for route handlers. Falls back to sync if SQLite."""
    if AsyncSessionLocal is None:
        # SQLite fallback: yield sync session
        with GetDB() as db:
            yield db
        return
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except SQLAlchemyError:
            await session.rollback()
            raise


from .crud import (create_admin, create_notification_reminder,  # noqa
                   create_user, delete_notification_reminder, get_admin,
                   get_admins, get_jwt_secret_key, get_notification_reminder,
                   get_or_create_inbound, get_system_usage,
                   get_tls_certificate, get_user, get_user_by_id, get_users,
                   get_users_count, remove_admin, remove_user, revoke_user_sub,
                   set_owner, update_admin, update_user, update_user_status, reset_user_by_next,
                   update_user_sub, start_user_expire, get_admin_by_id,
                   get_admin_by_telegram_id,
                   # Async versions for route handlers
                   async_get_user, async_create_user, async_update_user,
                   async_remove_user, async_get_users,
                   async_get_or_create_inbound,
                   async_get_notification_reminder,
                   async_delete_notification_reminder,
                   async_get_admin,
                   async_reset_user_data_usage,
                   async_revoke_user_sub,
                   async_reset_user_by_next,
                   async_set_owner,
                   async_get_user_usages,
                   async_get_all_users_usages,
                   )

from .models import JWT, System, User  # noqa

__all__ = [
    "get_or_create_inbound",
    "get_user",
    "get_user_by_id",
    "get_users",
    "get_users_count",
    "create_user",
    "remove_user",
    "update_user",
    "update_user_status",
    "start_user_expire",
    "update_user_sub",
    "reset_user_by_next",
    "revoke_user_sub",
    "set_owner",
    "get_system_usage",
    "get_jwt_secret_key",
    "get_tls_certificate",
    "get_admin",
    "create_admin",
    "update_admin",
    "remove_admin",
    "get_admins",
    "get_admin_by_id",
    "get_admin_by_telegram_id",

    "create_notification_reminder",
    "get_notification_reminder",
    "delete_notification_reminder",

    "GetDB",
    "get_db",
    "get_async_db",

    # Async CRUD
    "async_get_user",
    "async_create_user",
    "async_update_user",
    "async_remove_user",
    "async_get_users",
    "async_get_or_create_inbound",
    "async_get_notification_reminder",
    "async_delete_notification_reminder",
    "async_get_admin",
    "async_reset_user_data_usage",
    "async_revoke_user_sub",
    "async_reset_user_by_next",
    "async_set_owner",
    "async_get_user_usages",
    "async_get_all_users_usages",

    "User",
    "System",
    "JWT",

    "Base",
    "Session",
]
