import contextlib
from datetime import datetime, timezone
from typing import cast

from celery import shared_task

from app.constants.celery_tasks import CeleryTask
from app.database import SessionLocal
from app.integrations.redis_client import get_redis_client
from app.services.sdk.sleep_service import (
    active_member,
    active_users_key,
    finish_sleep,
    is_ready_to_finish,
    load_sleep_state,
    parse_active_member,
)
from app.utils.structured_logging import get_logger

logger = get_logger(__name__)


@shared_task(name=CeleryTask.FINALIZE_STALE_SLEEPS)
def finalize_stale_sleeps() -> None:
    now = datetime.now(timezone.utc)
    redis_client = get_redis_client()

    with SessionLocal() as db:
        # Members identify a (user, reporting stream) pair; a member written by an
        # older release is the bare user id and parses back to an empty scope, which
        # still addresses the key that release wrote.
        for member in cast(set[str], redis_client.smembers(active_users_key())):
            user_id, scope = parse_active_member(member)
            try:
                # Skip users whose upload is currently in progress. The lock stays
                # per-user so a batch spanning several streams is still serialised
                # against this sweep as a whole.
                lock = redis_client.lock(f"sleep:lock:{user_id}", timeout=30, blocking_timeout=0)
                if not lock.acquire(blocking=False):
                    continue

                try:
                    state = load_sleep_state(user_id, scope)
                    if not state:
                        # The state expired or was finalised elsewhere; drop the
                        # member so the set cannot grow without bound.
                        redis_client.srem(active_users_key(), active_member(user_id, scope))
                        continue

                    if is_ready_to_finish(state, now):
                        finish_sleep(db, user_id, state)
                finally:
                    with contextlib.suppress(Exception):
                        lock.release()

            except Exception as e:
                logger.error("Error finalizing stale sleep", exc_info=e, user_id=user_id)
