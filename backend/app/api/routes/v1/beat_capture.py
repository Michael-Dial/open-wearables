"""Beat-to-beat capture: researcher controls and the mobile SDK's view of them.

Apple Watch beat-to-beat intervals (HKHeartbeatSeriesSample) stay on the phone until the
iOS SDK reads them, so these endpoints decide what the phone uploads rather than what
the server stores:

- ``/users/{user_id}/beat-capture`` sets a participant's mode (off / sleep / all_day).
- ``/users/{user_id}/beat-capture/windows`` asks the phone for one span, in any mode.
- ``/sdk/users/{user_id}/beat-capture`` is what the phone reads before each sync, and
  ``.../windows/{id}/fulfilled`` is how it reports a window done.

Uploaded beats land in the ``pulse_to_pulse_interval`` series, queryable through
``/users/{user_id}/timeseries``.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.database import DbSession
from app.schemas.auth import SDKAuthContext
from app.schemas.beat_capture import (
    BeatCaptureSettingRead,
    BeatCaptureSettingUpdate,
    BeatCaptureWindowCreate,
    BeatCaptureWindowRead,
    BeatCaptureWindowStatus,
    SDKBeatCaptureConfig,
    SDKBeatCaptureFulfilment,
)
from app.services import ApiKeyDep
from app.services.beat_capture_service import beat_capture_service
from app.utils.auth import SDKAuthDep

router = APIRouter()
sdk_router = APIRouter()


def _assert_sdk_caller_is_user(auth: SDKAuthContext, user_id: UUID) -> None:
    if auth.auth_type == "sdk_token" and auth.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token does not match user_id")


@router.get(
    "/users/{user_id}/beat-capture",
    summary="Get a participant's beat-to-beat capture setting",
)
def get_beat_capture_setting(user_id: UUID, db: DbSession, _api_key: ApiKeyDep) -> BeatCaptureSettingRead:
    """The mode the participant's phone follows. ``is_default`` is true when nothing is stored for them."""
    return beat_capture_service.get_setting(db, user_id)


@router.put(
    "/users/{user_id}/beat-capture",
    summary="Set a participant's beat-to-beat capture setting",
)
def update_beat_capture_setting(
    user_id: UUID,
    update: BeatCaptureSettingUpdate,
    db: DbSession,
    _api_key: ApiKeyDep,
) -> BeatCaptureSettingRead:
    """Takes effect on the phone's next sync.

    - ``off``: nothing automatic; requested windows are still uploaded.
    - ``sleep``: heartbeat series overlapping a HealthKit sleep session, padded by
      ``sleep_padding_minutes`` either side.
    - ``all_day``: every heartbeat series.

    ``lookback_days`` (1-30) bounds how far back the automatic modes reach. Fields left
    out keep their current value.
    """
    return beat_capture_service.update_setting(db, user_id, update)


@router.get(
    "/users/{user_id}/beat-capture/windows",
    summary="List requested beat-to-beat windows",
)
def list_beat_capture_windows(
    user_id: UUID,
    db: DbSession,
    _api_key: ApiKeyDep,
    status: BeatCaptureWindowStatus | None = None,
) -> list[BeatCaptureWindowRead]:
    return [BeatCaptureWindowRead.model_validate(w) for w in beat_capture_service.list_windows(db, user_id, status)]


@router.post(
    "/users/{user_id}/beat-capture/windows",
    status_code=status.HTTP_201_CREATED,
    summary="Request beat-to-beat intervals for a span of time",
)
def create_beat_capture_window(
    user_id: UUID,
    payload: BeatCaptureWindowCreate,
    db: DbSession,
    _api_key: ApiKeyDep,
) -> BeatCaptureWindowRead:
    """The phone uploads every heartbeat series overlapping the window on its next sync,
    whatever the participant's mode, then reports how many beats and series it sent.

    Opening the app makes the next sync immediate; otherwise it waits for iOS to run a
    background sync. A window may span at most ``BEAT_CAPTURE_MAX_WINDOW_DAYS`` (default 7).
    """
    return BeatCaptureWindowRead.model_validate(beat_capture_service.create_window(db, user_id, payload))


@router.delete(
    "/users/{user_id}/beat-capture/windows/{window_id}",
    summary="Cancel a pending beat-to-beat window",
)
def cancel_beat_capture_window(
    user_id: UUID,
    window_id: UUID,
    db: DbSession,
    _api_key: ApiKeyDep,
) -> BeatCaptureWindowRead:
    """409 when the window was already fulfilled: its beats are stored and cancelling would not remove them."""
    window = beat_capture_service.cancel_window(db, user_id, window_id)
    if window.status == BeatCaptureWindowStatus.FULFILLED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Window already fulfilled")
    return BeatCaptureWindowRead.model_validate(window)


@sdk_router.get(
    "/sdk/users/{user_id}/beat-capture",
    summary="Beat-to-beat capture instructions for the phone",
)
def get_sdk_beat_capture_config(user_id: UUID, db: DbSession, auth: SDKAuthDep) -> SDKBeatCaptureConfig:
    """Read by the iOS SDK before it collects heartbeat series: the participant's mode and pending windows."""
    _assert_sdk_caller_is_user(auth, user_id)
    return beat_capture_service.sdk_config(db, user_id)


@sdk_router.post(
    "/sdk/users/{user_id}/beat-capture/windows/{window_id}/fulfilled",
    summary="Report a requested beat-to-beat window as uploaded",
)
def report_sdk_beat_capture_window_fulfilled(
    user_id: UUID,
    window_id: UUID,
    report: SDKBeatCaptureFulfilment,
    db: DbSession,
    auth: SDKAuthDep,
) -> BeatCaptureWindowRead:
    _assert_sdk_caller_is_user(auth, user_id)
    return BeatCaptureWindowRead.model_validate(beat_capture_service.record_fulfilment(db, user_id, window_id, report))
