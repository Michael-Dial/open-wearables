"""Render `/timeseries` rows as a streamed NDJSON or CSV body.

The bulk export (`GET /users/{user_id}/export/series`) hands every raw sample of a
window to a consumer in one response, so a study-wide recompute is one request per
participant rather than thousands of 1000-row pages. This module only shapes the
bytes; which rows and in what order is ``TimeSeriesService.iter_export_samples``.

Both formats carry exactly the fields a raw `/timeseries` row carries, taken from the
same ``TimeSeriesSample`` schema so the two cannot drift: an NDJSON line *is* one
``data[]`` item of `/timeseries`, and a CSV row is that item flattened, with the
nested ``source`` object's attribution fields as their own columns.
"""

import csv
import io
from collections.abc import Iterable, Iterator
from enum import StrEnum

from app.schemas.responses.activity import TimeSeriesSample
from app.schemas.utils import SourceMetadata


class ExportFormat(StrEnum):
    NDJSON = "ndjson"
    CSV = "csv"


MEDIA_TYPES: dict[ExportFormat, str] = {
    ExportFormat.NDJSON: "application/x-ndjson",
    ExportFormat.CSV: "text/csv; charset=utf-8",
}

# Rows are written in chunks rather than one yield per line: an ASGI server writes each
# yielded chunk as its own frame, and a night of 1 Hz data is tens of thousands of lines.
_ROWS_PER_CHUNK = 500

# The row's own fields, in schema order, minus the nested attribution object - that is
# flattened into the columns below. Read off the schema so a field added to a
# `/timeseries` row is a column here without a second list to keep in step.
SAMPLE_COLUMNS: tuple[str, ...] = tuple(name for name in TimeSeriesSample.model_fields if name != "source")

# The attribution columns, the join keys first. These are a subset of ``SourceMetadata``
# (fields and computed fields alike); ``_source_value`` reads them by name, so a name
# that is not on the schema fails loudly at the first row rather than exporting blanks.
SOURCE_COLUMNS: tuple[str, ...] = (
    "data_source_id",
    "provider",
    "source",
    "device",
    "device_name",
    "device_hardware_name",
    "device_type",
    "device_id",
    "device_label",
    "device_display_name",
    "user_connection_id",
    "original_source_name",
    "ingestion_route",
    "provider_device_id",
)

CSV_COLUMNS: tuple[str, ...] = SAMPLE_COLUMNS + SOURCE_COLUMNS


def _csv_cell(value: object) -> str:
    """One CSV cell: empty for null, JSON's words for booleans, else the plain string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _source_value(source: SourceMetadata | None, name: str) -> object:
    if source is None:
        return None
    return getattr(source, name)


def flatten(sample: TimeSeriesSample) -> list[str]:
    """A sample as one CSV row, in ``CSV_COLUMNS`` order."""
    row = sample.model_dump(mode="json", exclude={"source"})
    cells = [_csv_cell(row[name]) for name in SAMPLE_COLUMNS]
    cells.extend(_csv_cell(_source_value(sample.source, name)) for name in SOURCE_COLUMNS)
    return cells


def _chunked(lines: Iterable[str]) -> Iterator[str]:
    buffer: list[str] = []
    for line in lines:
        buffer.append(line)
        if len(buffer) >= _ROWS_PER_CHUNK:
            yield "".join(buffer)
            buffer.clear()
    if buffer:
        yield "".join(buffer)


def ndjson_body(samples: Iterable[TimeSeriesSample]) -> Iterator[str]:
    """One JSON object per line, each the exact shape of a `/timeseries` ``data[]`` item."""
    return _chunked(sample.model_dump_json() + "\n" for sample in samples)


def csv_body(samples: Iterable[TimeSeriesSample]) -> Iterator[str]:
    """A header row, then one row per sample. The header is written even when no row follows."""

    def lines() -> Iterator[str]:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        yield out.getvalue()
        for sample in samples:
            out.seek(0)
            out.truncate()
            writer.writerow(flatten(sample))
            yield out.getvalue()

    return _chunked(lines())


def body_for(export_format: ExportFormat, samples: Iterable[TimeSeriesSample]) -> Iterator[str]:
    if export_format is ExportFormat.CSV:
        return csv_body(samples)
    return ndjson_body(samples)
