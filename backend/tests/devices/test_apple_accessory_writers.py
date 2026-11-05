"""Apple's Bluetooth accessory services write for many devices and identify none.

HealthKit names every source that com.apple.heartratecoordinatord (AirPods Pro heart
rate) or com.apple.BTLEServer (a Bluetooth LE heart-rate sensor) writes "Bluetooth
Device". Keyed on that name, an AirPods Pro and a chest strap were one writer: they
shared an identity claim, raised a link proposal against each other, and a record that
named no accessory joined the one that later named itself. These tests pin the
separation, in the over-split direction.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import DataSource, Device, DeviceLinkProposal, User
from app.repositories.data_source_repository import DataSourceRepository
from app.repositories.device_repository import DeviceRepository
from app.schemas.enums import DeviceIdentityKind, DeviceType, ProviderName
from app.services.devices.detection import DeviceDetectionService
from app.services.devices.identity import (
    IdentityClaim,
    claims_from_data_source,
    claims_from_sdk_source,
    data_source_writer_id,
    grouping_claim,
    relaying_host_model,
)
from app.services.sdk.device_resolution import extract_device_info

AIRPODS_WRITER = "com.apple.heartratecoordinatord"
BLE_WRITER = "com.apple.BTLEServer"
ACCESSORY_NAME = "Bluetooth Device"


@pytest.fixture
def user(db: Session) -> User:
    user = User(id=uuid4(), external_user_id=f"sub-{uuid4().hex[:8]}")
    db.add(user)
    db.flush()
    return user


def _ensure(
    db: Session,
    user: User,
    device_model: str | None,
    source: str | None = ACCESSORY_NAME,
    source_app_id: str | None = None,
) -> DataSource:
    return DataSourceRepository().ensure_data_source(
        db,
        user_id=user.id,
        provider=ProviderName.APPLE,
        device_model=device_model,
        source=source,
        source_app_id=source_app_id,
    )


def _kinds(claims: list[IdentityClaim]) -> set[DeviceIdentityKind]:
    return {c.kind for c in claims}


class TestWriterIdentity:
    def test_the_bundle_id_names_the_writer_in_preference_to_the_display_name(self) -> None:
        assert data_source_writer_id(ProviderName.APPLE, "Oura", "com.ouraring.oura") == "com.ouraring.oura"
        assert data_source_writer_id(ProviderName.APPLE, "Oura", None) == "Oura"
        # The integration's own literal is never a writer, whichever field holds it.
        assert data_source_writer_id(ProviderName.APPLE, "apple_health_xml", None) is None

    def test_an_accessory_writer_is_never_claimed(self) -> None:
        """Two accessories would hold one claim, and a shared claim reads as one unit."""
        claims = claims_from_data_source(
            ProviderName.APPLE, "AirPods Pro", ACCESSORY_NAME, source_app_id=AIRPODS_WRITER
        )
        assert DeviceIdentityKind.HEALTHKIT_BUNDLE not in _kinds(claims)
        assert [c.value for c in claims if c.kind is DeviceIdentityKind.MODEL_STRING] == ["AirPods Pro"]

        by_name = claims_from_data_source(ProviderName.APPLE, "3578", ACCESSORY_NAME)
        assert DeviceIdentityKind.HEALTHKIT_BUNDLE not in _kinds(by_name)

    def test_an_sdk_source_from_an_accessory_writer_claims_no_bundle(self) -> None:
        source = SimpleNamespace(
            device_id=None,
            bundle_identifier=None,
            app_id=BLE_WRITER,
            product_type=None,
            device_model="3578",
            device_name="Polar H10 8E1F2C3A",
        )
        assert DeviceIdentityKind.HEALTHKIT_BUNDLE not in _kinds(claims_from_sdk_source(ProviderName.APPLE, source))

    def test_a_third_party_writer_is_still_claimed_by_its_bundle_id(self) -> None:
        claims = claims_from_data_source(ProviderName.APPLE, None, "Oura", source_app_id="com.ouraring.oura")
        bundle = [c.value for c in claims if c.kind is DeviceIdentityKind.HEALTHKIT_BUNDLE]
        assert bundle == ["com.ouraring.oura"]

    def test_a_host_model_on_an_accessory_writer_names_the_phone_not_the_unit(self) -> None:
        """Apple's own code, but writing for an accessory: the phone only carried it.

        Read as platform-own, "iPhone18,1" would group AirPods heart rate with the
        phone's own data - a merge with no symptom.
        """
        assert relaying_host_model(ProviderName.APPLE, "iPhone18,1", AIRPODS_WRITER) == "iPhone18,1"
        claim = grouping_claim(ProviderName.APPLE, AIRPODS_WRITER, "iPhone18,1")
        assert claim is not None
        assert claim.kind is DeviceIdentityKind.AGGREGATOR_WRITER_MODEL
        assert claim.value.startswith(AIRPODS_WRITER)

    def test_apple_s_own_device_writer_is_still_the_handset(self) -> None:
        assert relaying_host_model(ProviderName.APPLE, "iPhone18,1", "com.apple.health.ABCDEF") is None


class TestSourceNaming:
    def test_an_accessory_source_is_named_after_the_accessory(self) -> None:
        source = SimpleNamespace(
            app_id=AIRPODS_WRITER,
            bundle_identifier=None,
            name=ACCESSORY_NAME,
            device_name="Michael's AirPods Pro",
            device_model="AirPods Pro",
            device_hardware_version=None,
            product_type=None,
            operating_system_version=None,
        )
        _model, _sw, name = extract_device_info(source, ProviderName.APPLE.value)
        assert name == "Michael's AirPods Pro"

    def test_without_the_app_id_the_name_is_left_alone(self) -> None:
        """Without an app id the name is part of the row's key; renaming would move rows."""
        source = SimpleNamespace(
            app_id=None,
            bundle_identifier=None,
            name=ACCESSORY_NAME,
            device_name="Michael's AirPods Pro",
            device_model="AirPods Pro",
            device_hardware_version=None,
            product_type=None,
            operating_system_version=None,
        )
        assert extract_device_info(source, ProviderName.APPLE.value)[2] == ACCESSORY_NAME

    def test_a_third_party_writer_keeps_its_own_name(self) -> None:
        source = SimpleNamespace(
            app_id="com.ouraring.oura",
            bundle_identifier=None,
            name="Oura",
            device_name="Oura Ring",
            device_model=None,
            device_hardware_version=None,
            product_type=None,
            operating_system_version=None,
        )
        assert extract_device_info(source, ProviderName.APPLE.value)[2] == "Oura"


class TestRegistry:
    def test_two_accessories_named_alike_are_two_devices_and_no_proposal(self, db: Session, user: User) -> None:
        airpods = _ensure(db, user, "AirPods Pro", source_app_id=AIRPODS_WRITER)
        strap = _ensure(db, user, "3578", source_app_id=BLE_WRITER)

        assert airpods.device_id is not None
        assert strap.device_id is not None
        assert airpods.device_id != strap.device_id
        assert db.query(DeviceLinkProposal).filter(DeviceLinkProposal.user_id == user.id).count() == 0

    def test_airpods_are_typed_as_headphones(self, db: Session, user: User) -> None:
        airpods = _ensure(db, user, "AirPods Pro", source_app_id=AIRPODS_WRITER)
        assert airpods.device_type == DeviceType.HEADPHONES.value
        device = db.get(Device, airpods.device_id)
        assert device is not None
        assert device.device_type == DeviceType.HEADPHONES.value


class TestNoConvergence:
    def test_a_model_less_record_and_a_named_accessory_stay_two_sources(self, db: Session, user: User) -> None:
        unnamed = _ensure(db, user, None, source_app_id=AIRPODS_WRITER)
        named = _ensure(db, user, "AirPods Pro", source_app_id=AIRPODS_WRITER)
        assert unnamed.id != named.id
        assert unnamed.device_model is None

    def test_the_named_accessory_first_then_a_model_less_record(self, db: Session, user: User) -> None:
        named = _ensure(db, user, "AirPods Pro", source_app_id=AIRPODS_WRITER)
        unnamed = _ensure(db, user, None, source_app_id=AIRPODS_WRITER)
        assert unnamed.id != named.id
        assert named.device_model == "AirPods Pro"

    def test_a_third_party_writer_still_converges(self, db: Session, user: User) -> None:
        """Unchanged for an app that is one product: its records with and without a model are one source."""
        first = _ensure(db, user, None, source="Muse", source_app_id="com.interaxon.muse")
        second = _ensure(db, user, "Muse S", source="Muse", source_app_id="com.interaxon.muse")
        assert first.id == second.id

    def test_a_phone_keyed_legacy_row_does_not_absorb_a_named_accessory(self, db: Session, user: User) -> None:
        """An iOS SDK that sent no app id and the phone as the model left this row.

        It holds whatever accessory the service relayed; AirPods naming themselves later
        are not known to be it, so they get their own source, typed by their own model.
        """
        legacy = _ensure(db, user, "iPhone18,1")
        assert legacy.device_type == DeviceType.PHONE.value

        airpods = _ensure(db, user, "AirPods Pro", source_app_id=AIRPODS_WRITER)
        assert airpods.id != legacy.id
        assert airpods.device_type == DeviceType.HEADPHONES.value


class TestStuckHandsetType:
    def test_a_row_typed_by_its_carrier_takes_its_own_model_s_type(self, db: Session, user: User) -> None:
        """The production case: a row learned "AirPods Pro" but kept the phone's type."""
        row = _ensure(db, user, "AirPods Pro", source_app_id=AIRPODS_WRITER)
        row.device_type = DeviceType.PHONE.value
        db.flush()

        again = _ensure(db, user, "AirPods Pro", source_app_id=AIRPODS_WRITER)
        assert again.id == row.id
        assert again.device_type == DeviceType.HEADPHONES.value

    def test_a_phone_s_own_row_stays_a_phone(self) -> None:
        assert (
            DataSourceRepository.next_device_type(
                ProviderName.APPLE, DeviceType.PHONE.value, DeviceType.WATCH, "iPhone18,1"
            )
            == DeviceType.PHONE.value
        )

    def test_without_a_model_nothing_changes(self) -> None:
        assert (
            DataSourceRepository.next_device_type(ProviderName.APPLE, DeviceType.PHONE.value, DeviceType.WATCH, None)
            == DeviceType.PHONE.value
        )


class TestWriterNameLegacyKey:
    def test_a_relayed_device_keyed_by_the_display_name_is_found_by_the_bundle_id(
        self, db: Session, user: User
    ) -> None:
        """Devices grouped before the bundle id was preferred keep their new sources."""
        repo = DeviceRepository()
        legacy_claim = grouping_claim(ProviderName.APPLE, "Oura", "iPhone18,1")
        assert legacy_claim is not None
        device = repo.create(
            db,
            user_id=user.id,
            device_type=DeviceType.RING.value,
            brand="Oura",
            model_raw=None,
            model_display=None,
            host_model_raw="iPhone18,1",
            actor="test",
            reason="legacy",
            detected=True,
        )
        repo.add_claim(db, device, legacy_claim, actor="test")

        row = DataSource(
            id=uuid4(),
            user_id=user.id,
            provider=ProviderName.APPLE,
            device_model="iPhone18,1",
            source="Oura",
            source_app_id="com.ouraring.oura",
            device_type=DeviceType.RING.value,
        )
        db.add(row)
        db.flush()

        found = DeviceDetectionService(repo).resolve_for_data_source(db, row, actor="test")
        assert found is not None
        assert found.id == device.id
        values = {c.id_value for c in repo.claims_for_device(db, device.id)}
        assert "com.ouraring.oura||iPhone18,1" in values
