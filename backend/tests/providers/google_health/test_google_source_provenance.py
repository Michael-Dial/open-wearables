"""What a Google Health data point's dataSource says beyond the device.

``recordingMethod`` (MANUAL vs measured vs DERIVED), ``platform`` (FITBIT, HEALTH_CONNECT,
HEALTH_KIT...) and ``application.packageName`` were not read: a hand-typed value entered
the study as a measurement, and a relayed point could not be told from a native one.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.providers.google_health.data_247 import GoogleHealth247Data
from app.services.providers.google_health.helpers import observation_values, source_provenance
from app.services.providers.google_health.metrics import METRICS

WINDOW = (datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 5, 3, tzinfo=timezone.utc))
SAMPLE_TIME = {"physicalTime": "2026-05-02T07:00:00Z", "utcOffset": "-14400s"}
HEART_RATE = next(m for m in METRICS if m.data_type == "heart-rate")

RELAYED = {
    "recordingMethod": "PASSIVELY_MEASURED",
    "platform": "HEALTH_CONNECT",
    "application": {"packageName": "com.ouraring.oura"},
    "device": {"manufacturer": "Oura", "formFactor": "RING"},
}


class TestSourceProvenance:
    def test_everything_the_data_source_states_is_kept_verbatim(self) -> None:
        assert source_provenance(RELAYED) == {
            "recording_method": "PASSIVELY_MEASURED",
            "platform": "HEALTH_CONNECT",
            "application": "com.ouraring.oura",
            "manufacturer": "Oura",
            "form_factor": "RING",
        }

    def test_absent_keys_are_left_out_not_nulled(self) -> None:
        assert source_provenance({"recordingMethod": "MANUAL"}) == {"recording_method": "MANUAL"}

    @pytest.mark.parametrize("data_source", [None, {}, {"device": {}}, "x"])
    def test_nothing_stated_is_none(self, data_source: object) -> None:
        assert source_provenance(data_source) is None

    def test_observation_values(self) -> None:
        values = observation_values(source_provenance(RELAYED), "Oura Ring 4")
        assert values is not None
        assert values["source_name"] == "HEALTH_CONNECT"
        assert values["source_bundle_id"] == "com.ouraring.oura"
        assert values["device_model"] == "Oura Ring 4"
        assert values["device_manufacturer"] == "Oura"
        assert values["device_type_reported"] == "RING"
        assert values["recording_method"] == "PASSIVELY_MEASURED"


def test_a_manual_entry_is_labelled_on_its_sample() -> None:
    data_247 = GoogleHealth247Data(oauth=MagicMock(), connection_repo=MagicMock(), api_base_url="https://x")
    point = {
        "heartRate": {"sampleTime": SAMPLE_TIME, "beatsPerMinute": 58},
        "dataSource": {"recordingMethod": "MANUAL", "platform": "FITBIT"},
    }
    with patch.object(data_247, "_fetch_points", return_value=iter([point])):
        [sample] = data_247._native_samples(MagicMock(), uuid4(), HEART_RATE, *WINDOW)

    assert sample.provider_metadata == {"recording_method": "MANUAL", "platform": "FITBIT"}
