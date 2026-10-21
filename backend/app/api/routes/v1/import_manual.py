from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.database import DbSession
from app.schemas.manual_import import (
    ManualSeriesImport,
    ManualSeriesImportResult,
    ManualSleepImportResult,
    ManualSleepStagesImport,
)
from app.services import ApiKeyDep
from app.services.manual_import_service import ManualConnectionNotFoundError, manual_import_service

router = APIRouter()


@router.post("/users/{user_id}/import/manual/sleep-stages")
def import_manual_sleep_stages(
    user_id: UUID,
    payload: ManualSleepStagesImport,
    db: DbSession,
    _api_key: ApiKeyDep,
) -> ManualSleepImportResult:
    """Import one scored night from a reference instrument that has no provider.

    For PSG exports, Kubios outputs and EEG headband files: the stages land as one
    sleep record with `sleep_stage_intervals`, exactly as a night relayed through
    Apple Health is stored, on the user's `manual` connection (created with
    `account_type` `reference` if there is none) and under a data source named by
    `device_label`. Read it back with `/users/{user_id}/events/sleep?include=stages`.

    The instrument's stage labels are kept verbatim (`vendor_stage`); a label that is
    not one of OW's words is filed with `stage` = `unknown` and no totals, because
    folding N1/N2/N3 to light/deep is the consumer's rule. Re-importing the same
    night (same `external_id`, or the same label and start when none is given)
    replaces the record. Two instruments scoring one night are two records.

    400 when the body is inconsistent; 404 when `connection_id` is not a manual
    connection of this user.
    """
    try:
        return manual_import_service.import_sleep_stages(db, user_id, payload)
    except ManualConnectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/users/{user_id}/import/manual/series")
def import_manual_series(
    user_id: UUID,
    payload: ManualSeriesImport,
    db: DbSession,
    _api_key: ApiKeyDep,
) -> ManualSeriesImportResult:
    """Import time-series samples from a reference instrument that has no provider.

    Rows go through the same upsert every provider uses, keyed on (data source,
    series type, second), so sending a file twice changes nothing and a corrected
    value replaces the old one. They read back from `/users/{user_id}/timeseries`
    and the bulk export with `route` `manual.samples`, or `manual.windows` when
    `interval_seconds` was given, which is also stored on each row.

    404 when `connection_id` is not a manual connection of this user.
    """
    try:
        return manual_import_service.import_series(db, user_id, payload)
    except ManualConnectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
