"""An Apple VO2max reaches /timeseries saying which HealthKit test produced it.

Apple Watch writes a submaximal prediction; a lab result typed into Health, or a
third-party app, can write a maximal test or a non-exercise prediction into the
same series. Without the test type the three are one row shape. This pins that the
phone SDK path keeps HealthKit's key and that the read states it.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User
from app.services.sdk.import_service import ImportService

WATCH = {"name": "MD Apple Watch Ultra 4", "bundleIdentifier": "com.apple.health.test", "productType": "Watch8,1"}


@pytest.fixture(autouse=True)
def mock_sleep_redis() -> Any:
    with (
        patch("app.services.sdk.sleep_service.get_redis_client") as mock_get_redis,
        patch("app.integrations.celery.tasks.finalize_stale_sleep_task.finalize_stale_sleeps"),
    ):
        mock_get_redis.return_value = MagicMock()
        yield


def _vo2(value: float, at: str, metadata: dict[str, Any] | None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": f"vo2-{at}",
        "type": "HKQuantityTypeIdentifierVO2Max",
        "value": value,
        "unit": "mL/min·kg",
        "startDate": at,
        "endDate": at,
        "source": WATCH,
    }
    if metadata is not None:
        record["metadata"] = metadata
    return record


def test_the_sdk_path_states_healthkits_test_type(
    client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
) -> None:
    payload = {
        "provider": "apple",
        "sdkVersion": "0.14.0",
        "syncTimestamp": "2026-10-30T12:00:00Z",
        "data": {
            "records": [
                _vo2(44.1, "2026-10-29T18:00:00Z", {"HKVO2MaxTestType": "2"}),
                _vo2(47.0, "2026-10-30T09:00:00Z", None),
            ]
        },
    }
    ImportService().load_data(db, payload, str(user.id))

    response = client.get(
        f"/api/v1/users/{user.id}/timeseries?types=vo2_max"
        "&start_time=2026-10-29T00:00:00Z&end_time=2026-10-31T00:00:00Z&resolution=raw&limit=100",
        headers=api_key_header,
    )

    assert response.status_code == 200, response.text
    assert [(r["value"], r["estimate_method"]) for r in response.json()["data"]] == [
        (44.1, "HKVO2MaxTestType:2"),
        (47.0, None),  # nothing said, nothing assumed
    ]
