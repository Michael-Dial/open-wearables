from typing import Annotated

from fastapi import HTTPException, Query, status

from app.schemas.model_crud.activities.source_filters import PLATFORM_FILTER_DESCRIPTION

PlatformQuery = Annotated[str | None, Query(description=PLATFORM_FILTER_DESCRIPTION, examples=["HEALTH_CONNECT"])]


def refuse_platform_unless(platform: str | None, allowed: bool, reason: str) -> None:
    """400 when ``platform`` is set on a read that cannot apply it, rather than ignoring it.

    A silently ignored filter returns every platform's rows under a request for one.
    """
    if platform and not allowed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"platform filters single rows; {reason}")
