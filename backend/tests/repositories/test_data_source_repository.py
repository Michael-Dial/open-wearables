"""
Tests for DataSourceRepository.

Regression coverage for the ``data_source.source`` column width: Apple
HealthKit tags on-device data with a source bundle identifier of the form
``com.apple.health.<UUID>`` (53 chars). This overflowed the previous
``VARCHAR(50)`` and aborted the entire SDK import batch with
``StringDataRightTruncation``, so no Apple sleep/records were ever saved.
The column is now ``VARCHAR(100)``.
"""

import pytest
from sqlalchemy.orm import Session

from app.models import DataSource
from app.repositories.data_source_repository import DataSourceIdentity, DataSourceRepository
from app.schemas.enums import DeviceType, ProviderName
from tests.factories import UserConnectionFactory, UserFactory

# Realistic Apple HealthKit source bundle id: "com.apple.health." + a UUID.
APPLE_HEALTH_SOURCE = "com.apple.health.ED447642-08FD-4E45-AF20-633C02C83170"


class TestDataSourceRepository:
    """Test suite for DataSourceRepository."""

    def test_apple_health_source_bundle_id_persists(self, db: Session) -> None:
        """A 53-char Apple source bundle id imports without truncation.

        Exercises ``ensure_data_source`` (the SDK import path that failed) and
        asserts the full identifier round-trips — this would raise
        ``StringDataRightTruncation`` against the old ``VARCHAR(50)`` column.
        """
        # Arrange: a source longer than the old 50-char limit.
        assert len(APPLE_HEALTH_SOURCE) > 50
        user = UserFactory()
        repo = DataSourceRepository(DataSource)

        # Act
        created = repo.ensure_data_source(
            db,
            user_id=user.id,
            provider=ProviderName.APPLE,
            device_model="Watch7,5",
            source=APPLE_HEALTH_SOURCE,
        )
        db.commit()
        db.expire_all()

        # Assert: stored intact, retrievable by its full identity.
        assert created.source == APPLE_HEALTH_SOURCE
        stored = repo.get_by_identity(
            db,
            user_id=user.id,
            provider=ProviderName.APPLE,
            device_model="Watch7,5",
            source=APPLE_HEALTH_SOURCE,
        )
        assert stored is not None
        assert stored.source == APPLE_HEALTH_SOURCE
        assert len(stored.source) == len(APPLE_HEALTH_SOURCE)


class TestInferDeviceTypeFromSourceLabel:
    """HealthKit stamps the syncing iPhone's productType on watch-recorded samples."""

    def _repo(self) -> DataSourceRepository:
        return DataSourceRepository()

    @pytest.mark.parametrize(
        ("device_model", "source", "expected"),
        [
            # The label names the recorder; the model names the handset that relayed it.
            ("iPhone18,1", "Michael's Apple Watch", DeviceType.WATCH),
            ("iPhone18,1", "Michael's Apple Watch Ultra 3", DeviceType.WATCH),
            ("iPhone18,1", "Oura", DeviceType.RING),
            ("iPhone18,1", "WHOOP", DeviceType.BAND),
            # A sleep headband has no API of its own, so the relay is the only way
            # its data ever arrives - if the label loses to the handset here, the
            # type is wrong every time rather than only sometimes.
            ("iPhone18,1", "Muse", DeviceType.EEG),
            ("iPhone18,1", "Dreem", DeviceType.EEG),
            ("iPhone18,1", "Michael's Headband", DeviceType.HEADBAND),
            # A phone label under a phone model stays a phone.
            ("iPhone18,1", "Michael's iPhone", DeviceType.PHONE),
            ("iPhone18,1", "Fitness", DeviceType.PHONE),
            ("iPhone18,1", "Health", DeviceType.PHONE),
            # A real wearable model is never downgraded by its label.
            ("Watch7,12", "Bluetooth Device", DeviceType.WATCH),
            ("Watch7,12", "Michael's Apple Watch Ultra 3", DeviceType.WATCH),
            # No model at all: the label carries it.
            (None, "Michael's Apple Watch", DeviceType.WATCH),
        ],
    )
    def test_label_overrides_a_relayed_phone_model(
        self, device_model: str | None, source: str, expected: DeviceType
    ) -> None:
        assert self._repo()._infer_device_type(device_model, "Apple", source) == expected

    def test_falls_back_to_the_brand_when_nothing_else_identifies_the_device(self) -> None:
        assert self._repo()._infer_device_type(None, "Oura", None) == DeviceType.RING
        assert self._repo()._infer_device_type(None, None, None) == DeviceType.UNKNOWN


class TestCanonicalBrandWins:
    """``ensure_data_source`` resolves a canonical brand even when handed a raw source.

    The fork's attribution contract (docs/dev-guides/consuming-all-data.md) is that
    ``original_source_name`` holds a canonical brand — "Oura", "Apple" — regardless of
    which path the data arrived on, while ``provider`` holds the path. Upstream's
    event_record_repository passes ``creator.source`` straight in as
    ``original_source_name``; that argument must not pre-empt ``resolve_brand()``, or
    package ids land in the column and take device-type inference and
    ``resolve_ingestion_route`` down with them.

    These go through ``ensure_data_source`` on purpose. The unit tests around
    ``resolve_brand`` / ``resolve_ingestion_route`` pin those helpers directly, so they
    stay green even when nothing calls them — which is exactly how the wiring can be
    cut by an upstream merge without a single test failing.
    """

    @staticmethod
    def _repo() -> DataSourceRepository:
        return DataSourceRepository(DataSource)

    @pytest.mark.parametrize(
        ("provider", "source", "device_model", "expected_brand"),
        [
            # Oura relayed through Apple Health: the package id names the brand.
            (ProviderName.APPLE, "com.oura.oura", None, "Oura"),
            # Whoop relayed through Google Health Connect.
            (ProviderName.HEALTH_CONNECT, "com.whoop.android", None, "Whoop"),
            # Apple's own data inside Apple Health stays Apple.
            (ProviderName.APPLE, APPLE_HEALTH_SOURCE, "Watch7,12", "Apple"),
            # A direct provider read: the provider itself is the brand.
            (ProviderName.OURA, "oura", None, "Oura"),
            (ProviderName.WITHINGS, "withings", None, "Withings"),
        ],
    )
    def test_raw_source_argument_does_not_pre_empt_brand_resolution(
        self,
        db: Session,
        provider: ProviderName,
        source: str,
        device_model: str | None,
        expected_brand: str,
    ) -> None:
        user = UserFactory()

        created = self._repo().ensure_data_source(
            db,
            user_id=user.id,
            provider=provider,
            device_model=device_model,
            source=source,
            # What event_record_repository hands us: the raw source, not a brand.
            original_source_name=source,
        )

        assert created.original_source_name == expected_brand
        assert created.original_source_name != source or source == expected_brand

    def test_caller_value_survives_when_no_brand_can_be_resolved(self, db: Session) -> None:
        """The argument is still the fallback — it just no longer wins by default."""
        user = UserFactory()

        created = self._repo().ensure_data_source(
            db,
            user_id=user.id,
            provider=ProviderName.UNKNOWN,
            source="some.unrecognised.writer",
            original_source_name="some.unrecognised.writer",
        )

        assert created.original_source_name == "some.unrecognised.writer"

    def test_relayed_brand_is_not_labelled_first_party(self, db: Session) -> None:
        """The resolved brand has to reach resolve_ingestion_route to classify correctly."""
        from app.schemas.enums.provider import IngestionRoute
        from app.utils.device_registry import resolve_ingestion_route

        user = UserFactory()

        relayed = self._repo().ensure_data_source(
            db,
            user_id=user.id,
            provider=ProviderName.APPLE,
            source="com.oura.oura",
            original_source_name="com.oura.oura",
        )
        native = self._repo().ensure_data_source(
            db,
            user_id=user.id,
            provider=ProviderName.APPLE,
            device_model="Watch7,12",
            source=APPLE_HEALTH_SOURCE,
            original_source_name=APPLE_HEALTH_SOURCE,
        )

        assert resolve_ingestion_route(relayed.provider, relayed.original_source_name) == (IngestionRoute.AGGREGATOR)
        assert resolve_ingestion_route(native.provider, native.original_source_name) == IngestionRoute.DIRECT


class TestUnrecognisedWritersKeepTheirName:
    """A HealthKit writer no brand table knows must not be filed as "Apple".

    ``resolve_brand`` falls back to the platform when nothing matches, and that
    fallback used to win over the caller's value unconditionally. On the Apple route
    it therefore overwrote the HealthKit source name - the only field naming what
    recorded the data - with "Apple", for every third-party app the tables had never
    heard of. The relay then also read as first-party Apple data, because
    ``resolve_ingestion_route`` compares this column against the platform brand.
    """

    @staticmethod
    def _repo() -> DataSourceRepository:
        return DataSourceRepository(DataSource)

    @pytest.mark.parametrize("writer", ["Muse", "AutoSleep", "Eight Sleep", "Hume", "Bevel"])
    def test_an_unknown_apple_writer_keeps_its_own_name(self, db: Session, writer: str) -> None:
        user = UserFactory()

        created = self._repo().ensure_data_source(
            db,
            user_id=user.id,
            provider=ProviderName.APPLE,
            device_model="iPhone15,3",
            source=writer,
            original_source_name=writer,
        )

        assert created.original_source_name == writer

    def test_an_unknown_writer_reads_as_relayed_not_first_party(self, db: Session) -> None:
        from app.schemas.enums.provider import IngestionRoute
        from app.utils.device_registry import resolve_ingestion_route

        user = UserFactory()

        created = self._repo().ensure_data_source(
            db,
            user_id=user.id,
            provider=ProviderName.APPLE,
            device_model="iPhone15,3",
            source="Muse",
            original_source_name="Muse",
        )

        assert resolve_ingestion_route(created.provider, created.original_source_name) == IngestionRoute.AGGREGATOR

    def test_apples_own_data_still_resolves_to_apple(self, db: Session) -> None:
        """The fallback is still right for the case it was written for."""
        user = UserFactory()

        created = self._repo().ensure_data_source(
            db,
            user_id=user.id,
            provider=ProviderName.APPLE,
            device_model="Watch7,12",
            source=APPLE_HEALTH_SOURCE,
            original_source_name=APPLE_HEALTH_SOURCE,
        )

        assert created.original_source_name == "Apple"

    def test_the_bulk_path_agrees_with_the_single_path(self, db: Session) -> None:
        """Time series arrive through batch_ensure_data_sources, events through the other.

        Two rules would mean one Muse source reading "Muse" and the other "Apple"
        depending only on which path happened to create the row.
        """
        user = UserFactory()

        ids = self._repo().batch_ensure_data_sources(
            db,
            provider=ProviderName.APPLE,
            user_connection_id=None,
            identities={(user.id, "iPhone15,3", "Muse")},
        )

        created = db.get(DataSource, next(iter(ids.values())))
        assert created.original_source_name == "Muse"

    def test_sdk_device_type_upgrades_from_other_but_never_flips(self, db: Session) -> None:
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        identity = {
            "user_id": user.id,
            "provider": ProviderName.SAMSUNG,
            "device_model": "unlisted-model",
            "source": "tab",
        }
        created = repo.ensure_data_source(db, **identity)
        assert created.device_type == DeviceType.OTHER

        repo.ensure_data_source(db, **identity, reported_type=DeviceType.WATCH)
        assert repo.get_by_identity(db, **identity).device_type == DeviceType.WATCH

        batch_identity = DataSourceIdentity(user.id, "unlisted-model", "tab")
        repo.batch_ensure_data_sources(
            db, ProviderName.SAMSUNG, None, {batch_identity}, {batch_identity: DeviceType.BAND}
        )
        assert repo.get_by_identity(db, **identity).device_type == DeviceType.WATCH

    def test_cloud_device_type_is_corrected_on_sync(self, db: Session) -> None:
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        identity = {
            "user_id": user.id,
            "provider": ProviderName.GARMIN,
            "device_model": "Garmin Index BPM",
            "source": "garmin",
        }
        stored = repo.ensure_data_source(db, **identity)
        object.__setattr__(stored, "device_type", DeviceType.SCALE.value)
        db.flush()

        repo.ensure_data_source(db, **identity)
        assert repo.get_by_identity(db, **identity).device_type == DeviceType.BP_MONITOR

    def test_batch_upgrades_unset_device_type(self, db: Session) -> None:
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        identity = DataSourceIdentity(user.id, None, "com.fitbit.FitbitMobile")
        repo.batch_ensure_data_sources(db, ProviderName.HEALTH_CONNECT, None, {identity})
        ds = repo.get_by_identity(db, user.id, ProviderName.HEALTH_CONNECT, None, "com.fitbit.FitbitMobile")
        # Fork: an unclassifiable row stores "unknown", never NULL - consumers key off
        # device_type to tell wearables from relays, and NULL sends them back to guessing.
        assert ds.device_type == DeviceType.UNKNOWN

        repo.batch_ensure_data_sources(db, ProviderName.HEALTH_CONNECT, None, {identity}, {identity: DeviceType.BAND})
        db.expire_all()
        ds = repo.get_by_identity(db, user.id, ProviderName.HEALTH_CONNECT, None, "com.fitbit.FitbitMobile")
        assert ds.device_type == DeviceType.BAND

    def test_batch_resolves_stored_empty_model(self, db: Session) -> None:
        # Upstream's version also checks that the bulk path stores software_version; the
        # fork's bulk path never took one, so only the empty-model half is kept.
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        stored = repo.ensure_data_source(
            db, user_id=user.id, provider=ProviderName.STRAVA, device_model="", source="strava"
        )
        identity = DataSourceIdentity(user.id, None, "strava")

        result = repo.batch_ensure_data_sources(db, ProviderName.STRAVA, None, {identity})

        assert result[identity] == stored.id


class TestStableIdentity:
    """Upstream #1781's identity tiers (provider device id, app id + model, model + source).

    Ported from upstream with ``device_id`` read as ``provider_device_id`` and its batch
    API as the fork's. The per-account cases are the fork's own, below.
    """

    def test_device_id_links_records_and_fills_model(self, db: Session) -> None:
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        sleep = repo.ensure_data_source(
            db, user_id=user.id, provider=ProviderName.POLAR, source="polar", provider_device_id="15995F33"
        )
        workout = repo.ensure_data_source(
            db,
            user_id=user.id,
            provider=ProviderName.POLAR,
            device_model="Polar Vantage M3",
            source="polar",
            provider_device_id="15995F33",
        )

        assert workout.id == sleep.id
        assert workout.device_model == "Polar Vantage M3"
        assert workout.device_model_origin == "provider"

    def test_stable_ids_are_stamped_onto_legacy_row(self, db: Session) -> None:
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        legacy = repo.ensure_data_source(
            db, user_id=user.id, provider=ProviderName.APPLE, device_model="Watch6,2", source="Apple Watch"
        )

        keyed = repo.ensure_data_source(
            db,
            user_id=user.id,
            provider=ProviderName.APPLE,
            device_model="Watch6,2",
            source="Apple Watch",
            source_app_id="com.apple.health.X",
            device_manufacturer="Apple Inc.",
        )
        renamed = repo.ensure_data_source(
            db,
            user_id=user.id,
            provider=ProviderName.APPLE,
            device_model="Watch6,2",
            source="Kuba's Watch",
            source_app_id="com.apple.health.X",
        )

        assert keyed.id == legacy.id == renamed.id
        assert keyed.source_app_id == "com.apple.health.X"
        assert keyed.device_manufacturer == "Apple Inc."

    def test_record_without_ids_joins_the_only_keyed_row_of_its_model(self, db: Session) -> None:
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        ids = {"user_id": user.id, "provider": ProviderName.SAMSUNG, "device_model": "SM-M127F"}
        keyed = repo.ensure_data_source(
            db,
            **ids,
            source="Kamil's M12",
            provider_device_id="loGXfm78JC",
            source_app_id="com.sec.android.app.shealth",
        )

        steps = repo.ensure_data_source(db, **ids, source="m12")
        repo.ensure_data_source(
            db, **ids, source="Galaxy M12", provider_device_id="other", source_app_id="com.sec.android.app.shealth"
        )
        ambiguous = repo.ensure_data_source(db, **ids, source="m12")

        assert steps.id == keyed.id
        assert ambiguous.id != keyed.id

    def test_record_without_ids_prefers_keyed_row_over_legacy_row(self, db: Session) -> None:
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        ids = {"user_id": user.id, "provider": ProviderName.SAMSUNG, "device_model": "SM-M127F"}
        legacy = repo.ensure_data_source(db, **ids, source="m12")
        keyed = repo.ensure_data_source(
            db,
            **ids,
            source="Kamil's M12",
            provider_device_id="loGXfm78JC",
            source_app_id="com.sec.android.app.shealth",
        )

        steps = repo.ensure_data_source(db, **ids, source="m12")

        assert keyed.id != legacy.id
        assert steps.id == keyed.id

    def test_records_without_ids_join_keyed_row_created_in_the_same_batch(self, db: Session) -> None:
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        steps = DataSourceIdentity(user.id, "SM-M127F", "m12")
        keyed = DataSourceIdentity(user.id, "SM-M127F", "Kamil's M12", "loGXfm78JC", "com.sec.android.app.shealth")

        result = repo.batch_ensure_data_sources(db, ProviderName.SAMSUNG, None, [steps, keyed])

        assert result[steps] == result[keyed]

    def test_writer_row_learns_model_and_matches_unknown_model(self, db: Session) -> None:
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        ids = {"user_id": user.id, "provider": ProviderName.HEALTH_CONNECT, "source_app_id": "com.fitbit.FitbitMobile"}

        first = repo.ensure_data_source(db, **ids, source="com.fitbit.FitbitMobile")
        known = repo.ensure_data_source(db, **ids, device_model="Charge 6", source="com.fitbit.FitbitMobile")
        unknown_again = repo.ensure_data_source(db, **ids, source="com.fitbit.FitbitMobile")

        assert first.id == known.id == unknown_again.id
        assert known.device_model == "Charge 6"

    def test_writer_converges_within_one_batch(self, db: Session) -> None:
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        app = "com.fitbit.FitbitMobile"
        unknown = DataSourceIdentity(user.id, None, app, None, app)
        known = DataSourceIdentity(user.id, "Charge 6", app, None, app)

        result = repo.batch_ensure_data_sources(db, ProviderName.HEALTH_CONNECT, None, [unknown, known])

        assert result[unknown] == result[known]
        assert db.get(DataSource, result[known]).device_model == "Charge 6"

    def test_writer_with_several_models_keeps_separate_sources(self, db: Session) -> None:
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        ids = {"user_id": user.id, "provider": ProviderName.HEALTH_CONNECT, "source_app_id": "com.fitbit.FitbitMobile"}

        charge = repo.ensure_data_source(db, **ids, device_model="Charge 6")
        watch = repo.ensure_data_source(db, **ids, device_model="Pixel Watch 2")
        unknown = repo.ensure_data_source(db, **ids)

        assert len({charge.id, watch.id, unknown.id}) == 3

    def test_a_relaying_hosts_model_is_kept_not_replaced(self, db: Session) -> None:
        """Fork: upstream replaces an Apple host code with the producing device's model and
        merges the two. Here a stored model is the provider's capture, so the relayed
        stream keeps its own source; the device registry is where the two are tied to
        one unit."""
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        ids = {
            "user_id": user.id,
            "provider": ProviderName.APPLE,
            "source": "Oura",
            "source_app_id": "com.ouraring.oura",
        }

        relayed = repo.ensure_data_source(db, **ids, device_model="iPhone16,1")
        produced = repo.ensure_data_source(db, **ids, device_model="Oura Ring Gen3")

        assert relayed.id != produced.id
        assert relayed.device_model == "iPhone16,1"

    def test_apple_own_source_keeps_product_type(self, db: Session) -> None:
        user = UserFactory()
        repo = DataSourceRepository(DataSource)
        ids = {"user_id": user.id, "provider": ProviderName.APPLE, "source_app_id": APPLE_HEALTH_SOURCE}

        watch = repo.ensure_data_source(db, **ids, device_model="Watch6,2")
        other = repo.ensure_data_source(db, **ids, device_model="Watch7,5")

        assert watch.id != other.id
        assert watch.device_model == "Watch6,2"


class TestStableIdentityIsPerAccount:
    """Fork: every identity tier is scoped to the account, as the model + source tier already was.

    Upstream's tiers match on (user, provider) alone. A participant wearing two units of one
    model on two accounts of one provider would land on one source, and the samples of the
    two could not be told apart afterwards.
    """

    def test_one_device_id_on_two_accounts_is_two_sources(self, db: Session) -> None:
        user = UserFactory()
        left = UserConnectionFactory(user=user, provider="polar", provider_user_id="p-left")
        right = UserConnectionFactory(user=user, provider="polar", provider_user_id="p-right")
        repo = DataSourceRepository(DataSource)
        ids = {"user_id": user.id, "provider": ProviderName.POLAR, "source": "polar", "provider_device_id": "15995F33"}

        on_left = repo.ensure_data_source(db, **ids, user_connection_id=left.id)
        on_right = repo.ensure_data_source(db, **ids, user_connection_id=right.id)

        assert on_left.id != on_right.id
        assert on_left.user_connection_id == left.id
        assert on_right.user_connection_id == right.id

    def test_one_writer_on_two_accounts_is_two_sources_in_bulk(self, db: Session) -> None:
        user = UserFactory()
        left = UserConnectionFactory(user=user, provider="health_connect", provider_user_id="hc-left")
        right = UserConnectionFactory(user=user, provider="health_connect", provider_user_id="hc-right")
        repo = DataSourceRepository(DataSource)
        identity = DataSourceIdentity(user.id, "Charge 6", "Fitbit", None, "com.fitbit.FitbitMobile")

        on_left = repo.batch_ensure_data_sources(db, ProviderName.HEALTH_CONNECT, left.id, [identity])[identity]
        on_right = repo.batch_ensure_data_sources(db, ProviderName.HEALTH_CONNECT, right.id, [identity])[identity]

        assert on_left != on_right

    def test_a_legacy_row_on_another_account_is_not_stamped(self, db: Session) -> None:
        user = UserFactory()
        left = UserConnectionFactory(user=user, provider="samsung", provider_user_id="s-left")
        right = UserConnectionFactory(user=user, provider="samsung", provider_user_id="s-right")
        repo = DataSourceRepository(DataSource)
        ids = {"user_id": user.id, "provider": ProviderName.SAMSUNG, "device_model": "SM-R960", "source": "watch"}
        legacy_left = repo.ensure_data_source(db, **ids, user_connection_id=left.id)

        keyed_right = repo.ensure_data_source(db, **ids, user_connection_id=right.id, provider_device_id="R9ZW30ABC12")

        db.refresh(legacy_left)
        assert keyed_right.id != legacy_left.id
        assert legacy_left.provider_device_id is None
