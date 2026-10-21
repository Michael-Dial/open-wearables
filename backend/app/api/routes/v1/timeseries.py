from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BeforeValidator

from app.database import DbSession, StreamingDbSession
from app.schemas.enums import ProviderName, Resolution, SeriesType
from app.schemas.model_crud.activities import TimeSeriesQueryParams
from app.schemas.responses.activity import TimeSeriesSample
from app.schemas.utils import PaginatedResponse
from app.services import ApiKeyDep, timeseries_service
from app.services.series_export_service import MEDIA_TYPES, ExportFormat, body_for
from app.utils.dates import DateTimeQueryParam, parse_query_datetime, parse_query_end_datetime
from app.utils.pagination import DEFAULT_PAGE_SIZE, PageLimitQueryParam

router = APIRouter()


def split_comma_types(value: object) -> object:
    """Accept ``types=a,b`` as well as the repeated ``types=a&types=b``.

    FastAPI only understands the repeated form; a comma-joined value reaches the enum as
    one string and the whole request is rejected with 422. Clients building the query
    with a join (the Sleep Validation Hub did) then lose every series silently, because
    the failure looks like a night with no samples.
    """
    if isinstance(value, list):
        return [part.strip() for item in value for part in str(item).split(",") if part.strip()]
    return value


@router.get("/users/{user_id}/timeseries")
def get_timeseries(
    user_id: UUID,
    start_time: DateTimeQueryParam,
    end_time: DateTimeQueryParam,
    db: DbSession,
    _api_key: ApiKeyDep,
    types: Annotated[list[SeriesType], Query(), BeforeValidator(split_comma_types)] = [],
    resolution: Resolution = Resolution.RAW,
    cursor: str | None = None,
    limit: PageLimitQueryParam = DEFAULT_PAGE_SIZE,
    provider: ProviderName | None = None,
    source: str | None = None,
    device_model: str | None = None,
    data_source_id: UUID | None = None,
    include_redundant_relays: Annotated[
        bool,
        Query(
            description=(
                "Keep the aggregator's copy of a maker that is also connected directly. Off by "
                "default: data relayed through Apple Health, Google Health or Health Connect is "
                "left out for the span in which the maker's own API delivered the same data, so a "
                "device connected both ways is not counted twice. Nothing is deleted - set this to "
                "read the relayed copies back. `metadata.relay_dedup` lists what was left out."
            ),
        ),
    ] = False,
    filter_by_priority: bool = False,
) -> PaginatedResponse[TimeSeriesSample]:
    """Returns granular time series data (biometrics or activity)."""
    params = TimeSeriesQueryParams(
        start_datetime=parse_query_datetime(start_time),
        end_datetime=parse_query_end_datetime(end_time),
        limit=limit,
        cursor=cursor,
        resolution=resolution,
        provider=provider,
        source=source,
        device_model=device_model,
        data_source_id=data_source_id,
        include_redundant_relays=include_redundant_relays,
    )
    return timeseries_service.get_timeseries(db, user_id, types, params, filter_by_priority=filter_by_priority)


@router.get(
    "/users/{user_id}/export/series",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "Every raw sample in the window, streamed. NDJSON: one `/timeseries` `data[]` item "
                "per line. CSV: a header row, then one row per sample with the `source` object "
                "flattened into columns."
            ),
            "content": {"application/x-ndjson": {}, "text/csv": {}},
        },
    },
)
def export_series(
    user_id: UUID,
    start_time: DateTimeQueryParam,
    end_time: DateTimeQueryParam,
    db: StreamingDbSession,
    _api_key: ApiKeyDep,
    types: Annotated[
        list[SeriesType],
        Query(description="Series to export; every series when omitted. Repeated, or one comma-joined value."),
        BeforeValidator(split_comma_types),
    ] = [],
    data_source_id: Annotated[
        list[UUID],
        Query(description="Only these data sources (repeat the parameter). Every source when omitted."),
    ] = [],
    format: Annotated[ExportFormat, Query(description="`ndjson` (default) or `csv`.")] = ExportFormat.NDJSON,
) -> StreamingResponse:
    """Stream every raw time-series row of one user over a window, in one response.

    The bulk counterpart of `/timeseries?resolution=raw`: the same rows, with the same
    fields, same window bounds (`start_time` inclusive, `end_time` exclusive) and the
    same `types` parsing, but no page size and no cursor. A study-wide recompute is one
    request per participant instead of thousands of 1000-row pages. Rows are read
    through a server-side cursor and written as they arrive, so a window of any size
    is bounded by the client's throughput, not by the server's memory.

    **Every source's rows are returned.** No priority filtering and no relay
    deduplication is applied, unlike the default `/timeseries` read: a method
    comparison needs both arms, the maker's own feed and the copy an aggregator
    relayed, and both units of one model worn together. Tell them apart by
    `source.data_source_id` (NDJSON) or the `data_source_id` column (CSV); the `route`
    and `route_kind` fields say which feed of a source wrote each row.

    Ordered by `timestamp`, then `data_source_id`. The row count is not announced up
    front - counting would scan the window once more before the first row is sent.
    """
    params = TimeSeriesQueryParams(
        start_datetime=parse_query_datetime(start_time),
        end_datetime=parse_query_end_datetime(end_time),
    )
    samples = timeseries_service.iter_export_samples(db, user_id, types, params, data_source_id)
    filename = f"series_{user_id}_{params.start_datetime:%Y%m%dT%H%M%S}_{params.end_datetime:%Y%m%dT%H%M%S}.{format}"
    return StreamingResponse(
        body_for(format, samples),
        media_type=MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
