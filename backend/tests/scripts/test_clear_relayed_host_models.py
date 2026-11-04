"""Tests for the one-off clearing of relaying host models on third-party HealthKit sources."""

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy.orm import Session

from app.models import DataSource
from app.repositories.device_repository import DeviceRepository
from app.schemas.enums import DeviceType, ProviderName
from tests.factories import DataSourceFactory, UserConnectionFactory, UserFactory

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "data_migrations" / "clear_relayed_host_models.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("clear_relayed_host_models", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clear_relayed_host_models = _load_module().clear_relayed_host_models


def _source(user: object, **fields: object) -> DataSource:
    return DataSourceFactory(user=user, provider=ProviderName.APPLE, **fields)


class TestClearRelayedHostModels:
    def test_clears_host_model_and_re_resolves_type(self, db: Session) -> None:
        user = UserFactory()
        oura = _source(
            user, device_model="iPhone16,1", device_type="phone", source="Oura", source_app_id="com.ouraring.oura"
        )

        counts = clear_relayed_host_models(db, dry_run=False)
        db.flush()
        db.refresh(oura)

        assert counts["cleared"] == 1
        assert oura.device_model is None
        assert oura.device_type == DeviceType.RING

    def test_leaves_apple_own_producer_and_legacy_rows(self, db: Session) -> None:
        user = UserFactory()
        watch = _source(user, device_model="Watch6,2", source="Apple Watch", source_app_id="com.apple.health.X")
        ring = _source(user, device_model="Oura Ring Gen3", source="Oura", source_app_id="com.ouraring.oura")
        legacy = _source(user, device_model="iPhone16,1", source="Zepp Life")

        counts = clear_relayed_host_models(db, dry_run=False)
        db.flush()

        assert counts["cleared"] == 0
        assert (watch.device_model, ring.device_model, legacy.device_model) == (
            "Watch6,2",
            "Oura Ring Gen3",
            "iPhone16,1",
        )

    def test_skips_writer_that_already_has_a_source_without_model(self, db: Session) -> None:
        user = UserFactory()
        relayed = _source(user, device_model="iPhone16,1", source="Oura", source_app_id="com.ouraring.oura")
        _source(user, device_model=None, source="Oura", source_app_id="com.ouraring.oura")

        counts = clear_relayed_host_models(db, dry_run=False)
        db.flush()

        assert counts == {"skipped": 1}
        assert relayed.device_model == "iPhone16,1"

    def test_dry_run_changes_nothing(self, db: Session) -> None:
        user = UserFactory()
        oura = _source(user, device_model="iPhone16,1", source="Oura", source_app_id="com.ouraring.oura")

        counts = clear_relayed_host_models(db, dry_run=True)

        assert counts["cleared"] == 1
        assert oura.device_model == "iPhone16,1"

    def test_leaves_other_providers_alone(self, db: Session) -> None:
        huawei = DataSourceFactory(
            provider=ProviderName.HEALTH_CONNECT,
            device_model="Watch GT 3",
            source="Huawei Health",
            source_app_id="com.huawei.health",
        )

        counts = clear_relayed_host_models(db, dry_run=False)
        db.flush()

        assert counts["cleared"] == 0
        assert huawei.device_model == "Watch GT 3"


class TestClearRelayedHostModelsFork:
    """The fork's identity: origin recorded with the model, labels, and one writer per account."""

    def test_clears_the_origin_with_the_model(self, db: Session) -> None:
        user = UserFactory()
        oura = _source(
            user,
            device_model="iPhone16,1",
            device_model_origin="provider",
            source="Oura",
            source_app_id="com.ouraring.oura",
        )

        counts = clear_relayed_host_models(db, dry_run=False)
        db.flush()  # ck_data_source_device_model_origin rejects an origin without a model
        db.refresh(oura)

        assert counts["cleared"] == 1
        assert (oura.device_model, oura.device_model_origin) == (None, None)

    def test_leaves_a_label_alone(self, db: Session) -> None:
        user = UserFactory()
        labelled = _source(
            user,
            device_model="iPhone16,1",
            device_model_origin="label",
            source="Oura",
            source_app_id="com.ouraring.oura",
        )

        counts = clear_relayed_host_models(db, dry_run=False)
        db.flush()

        assert counts["cleared"] == 0
        assert labelled.device_model == "iPhone16,1"

    def test_a_model_less_row_on_another_account_does_not_block(self, db: Session) -> None:
        user = UserFactory()
        first = UserConnectionFactory(user=user, provider=ProviderName.APPLE.value)
        second = UserConnectionFactory(user=user, provider=ProviderName.APPLE.value)
        relayed = _source(
            user,
            user_connection_id=first.id,
            device_model="iPhone16,1",
            source="Oura",
            source_app_id="com.ouraring.oura",
        )
        _source(user, user_connection_id=second.id, device_model=None, source="Oura", source_app_id="com.ouraring.oura")

        counts = clear_relayed_host_models(db, dry_run=False)
        db.flush()

        assert counts["cleared"] == 1
        assert relayed.device_model is None

    def test_ignores_the_device_registry_link(self, db: Session) -> None:
        """The fork's device_id is the registry link; a relayed row attributed to a device is still cleared."""
        user = UserFactory()
        device = DeviceRepository().create(db, user.id, brand="oura")
        relayed = _source(
            user, device_id=device.id, device_model="iPhone16,1", source="Oura", source_app_id="com.ouraring.oura"
        )

        counts = clear_relayed_host_models(db, dry_run=False)
        db.flush()

        assert counts["cleared"] == 1
        assert relayed.device_model is None
        assert relayed.device_id == device.id
