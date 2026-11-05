from typing import Any
from uuid import UUID

from celery import Task, shared_task
from redis.exceptions import LockError

from app.constants.celery_tasks import CeleryTask
from app.database import SessionLocal
from app.schemas.providers.mobile_sdk import SyncRequest
from app.services.sdk.sleep_service import SLEEP_RETRY_SECONDS, handle_sleep_data
from app.utils.connection_context import active_connection
from app.utils.structured_logging import get_logger

logger = get_logger(__name__)


@shared_task(name=CeleryTask.SAVE_SDK_SLEEP, bind=True, max_retries=4)
def save_sdk_sleep(self: Task, request_json: str, user_id: str, connection_id: str | None = None) -> dict[str, Any]:
    """Save the sleep of an upload whose user's sleep lock was busy, once the lock frees up.

    Fork: under the account the upload arrived through (``connection_id``), as the upload itself ran.
    """
    request = SyncRequest.model_validate_json(request_json)
    try:
        with SessionLocal() as db, active_connection(UUID(connection_id) if connection_id else None):
            handle_sleep_data(db, request, user_id)
    except LockError as exc:
        if self.request.retries >= (self.max_retries or 0):
            logger.error(
                "Sleep dropped: the sleep lock stayed busy",
                alert=True,
                provider=request.provider,
                user_id=user_id,
                sleep_count=len(request.data.sleep),
            )
            return {"status": "dropped", "sleep_count": len(request.data.sleep)}
        raise self.retry(exc=exc, countdown=SLEEP_RETRY_SECONDS * (self.request.retries + 2)) from exc
    return {"status": "saved", "sleep_count": len(request.data.sleep)}
