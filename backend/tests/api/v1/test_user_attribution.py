"""Which app created a user (fork).

Attribution comes from the credential of the creating request, never from the payload:
- an API key: the user is attributed to that key, labelled with its current name, and with the
  name it had at creation once the key is gone
- a developer's JWT: the portal
- the seed-data generator: seed
- users created before attribution existed: unknown
"""

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from app.models import ApiKey, Developer, User
from app.schemas.model_crud.user_management import UserCreatedVia
from app.schemas.utils.seed_data import SeedDataRequest, SeedProfileConfig
from app.services.api_key_service import user_repository as api_key_user_repository
from app.services.seed_data import seed_data_service
from tests.factories import ApiKeyFactory, DeveloperFactory, UserFactory
from tests.utils import api_key_headers, developer_auth_headers

UNKNOWN = {"via": None, "label": None, "api_key_id": None}


@pytest.fixture
def developer(db: Session) -> Developer:
    return DeveloperFactory(email="attribution@example.com", password="test123")


@pytest.fixture
def sleep_hub_key(developer: Developer) -> ApiKey:
    return ApiKeyFactory(developer=developer, name="Sleep Hub")


@pytest.fixture
def ams_key(developer: Developer) -> ApiKey:
    return ApiKeyFactory(developer=developer, name="AMS")


def _create(client: TestClient, prefix: str, headers: dict[str, str], **payload: str) -> dict:
    response = client.post(f"{prefix}/users", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _created_by(client: TestClient, prefix: str, headers: dict[str, str], user_id: str) -> dict:
    response = client.get(f"{prefix}/users/{user_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["created_by"]


class TestCreateUserAttribution:
    def test_api_key_records_the_key_and_its_name(
        self, client: TestClient, db: Session, api_v1_prefix: str, sleep_hub_key: ApiKey
    ) -> None:
        created = _create(client, api_v1_prefix, api_key_headers(sleep_hub_key.plain_key))

        expected = {"via": "api_key", "label": "Sleep Hub", "api_key_id": str(sleep_hub_key.id)}
        assert created["created_by"] == expected
        user = db.get(User, UUID(created["id"]))
        assert user is not None
        assert user.created_via == UserCreatedVia.API_KEY
        assert user.created_by_api_key_id == sleep_hub_key.id
        assert user.created_by_label == "Sleep Hub"
        assert user.created_by_developer_id is None

    def test_each_app_is_told_apart_by_its_own_key(
        self, client: TestClient, api_v1_prefix: str, sleep_hub_key: ApiKey, ams_key: ApiKey
    ) -> None:
        from_sleep_hub = _create(client, api_v1_prefix, api_key_headers(sleep_hub_key.plain_key))
        from_ams = _create(client, api_v1_prefix, api_key_headers(ams_key.plain_key))

        assert from_sleep_hub["created_by"]["label"] == "Sleep Hub"
        assert from_ams["created_by"]["label"] == "AMS"

    def test_developer_session_records_the_portal(
        self, client: TestClient, db: Session, api_v1_prefix: str, developer: Developer
    ) -> None:
        created = _create(client, api_v1_prefix, developer_auth_headers(developer.id))

        assert created["created_by"] == {"via": "portal", "label": "Portal", "api_key_id": None}
        user = db.get(User, UUID(created["id"]))
        assert user is not None
        assert user.created_via == UserCreatedVia.PORTAL
        assert user.created_by_developer_id == developer.id
        assert user.created_by_api_key_id is None
        assert user.created_by_label is None

    def test_developer_session_wins_over_an_api_key_like_api_key_dep(
        self, client: TestClient, api_v1_prefix: str, developer: Developer, ams_key: ApiKey
    ) -> None:
        headers = developer_auth_headers(developer.id) | api_key_headers(ams_key.plain_key)

        assert _create(client, api_v1_prefix, headers)["created_by"]["via"] == "portal"

    def test_payload_cannot_claim_another_creator(
        self, client: TestClient, api_v1_prefix: str, ams_key: ApiKey, sleep_hub_key: ApiKey
    ) -> None:
        created = _create(
            client,
            api_v1_prefix,
            api_key_headers(ams_key.plain_key),
            created_via="portal",
            created_by_label="Sleep Hub",
            created_by_api_key_id=str(sleep_hub_key.id),
        )

        assert created["created_by"] == {"via": "api_key", "label": "AMS", "api_key_id": str(ams_key.id)}

    def test_label_snapshot_is_cut_to_the_column_length(
        self, client: TestClient, db: Session, api_v1_prefix: str, developer: Developer
    ) -> None:
        long_name = "x" * 150
        key = ApiKeyFactory(developer=developer, name=long_name)

        created = _create(client, api_v1_prefix, api_key_headers(key.plain_key))

        user = db.get(User, UUID(created["id"]))
        assert user is not None
        assert user.created_by_label == long_name[:100]
        # While the key exists the label is its full current name.
        assert created["created_by"]["label"] == long_name

    def test_without_credentials_is_rejected_like_api_key_dep(self, client: TestClient, api_v1_prefix: str) -> None:
        response = client.post(f"{api_v1_prefix}/users", json={})

        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required: provide JWT token or API key"

    def test_invalid_api_key_is_rejected(self, client: TestClient, api_v1_prefix: str) -> None:
        response = client.post(f"{api_v1_prefix}/users", json={}, headers=api_key_headers("sk-nope"))

        assert response.status_code == 401


class TestLabelOverTheKeyLifetime:
    def test_label_follows_a_key_rename(
        self, client: TestClient, api_v1_prefix: str, developer: Developer, ams_key: ApiKey
    ) -> None:
        user_id = _create(client, api_v1_prefix, api_key_headers(ams_key.plain_key))["id"]
        dev_headers = developer_auth_headers(developer.id)

        renamed = client.patch(
            f"{api_v1_prefix}/developer/api-keys/{ams_key.id}", json={"name": "AMS (prod)"}, headers=dev_headers
        )
        assert renamed.status_code == 200

        created_by = _created_by(client, api_v1_prefix, dev_headers, user_id)
        assert created_by == {"via": "api_key", "label": "AMS (prod)", "api_key_id": str(ams_key.id)}

    def test_label_falls_back_to_the_snapshot_after_the_key_is_deleted(
        self, client: TestClient, db: Session, api_v1_prefix: str, developer: Developer, ams_key: ApiKey
    ) -> None:
        user_id = _create(client, api_v1_prefix, api_key_headers(ams_key.plain_key))["id"]
        dev_headers = developer_auth_headers(developer.id)

        deleted = client.delete(f"{api_v1_prefix}/developer/api-keys/{ams_key.id}", headers=dev_headers)
        assert deleted.status_code == 200

        assert _created_by(client, api_v1_prefix, dev_headers, user_id) == {
            "via": "api_key",
            "label": "AMS",
            "api_key_id": None,
        }
        # The user itself is untouched by the key's deletion.
        assert db.get(User, UUID(user_id)) is not None

    def test_rotation_moves_the_users_to_the_new_key(
        self, client: TestClient, api_v1_prefix: str, developer: Developer, ams_key: ApiKey
    ) -> None:
        user_id = _create(client, api_v1_prefix, api_key_headers(ams_key.plain_key))["id"]
        dev_headers = developer_auth_headers(developer.id)

        rotated = client.post(f"{api_v1_prefix}/developer/api-keys/{ams_key.id}/rotate", headers=dev_headers)
        assert rotated.status_code == 201
        new_key_id = rotated.json()["id"]

        assert _created_by(client, api_v1_prefix, dev_headers, user_id) == {
            "via": "api_key",
            "label": "AMS",
            "api_key_id": new_key_id,
        }
        listed = client.get(f"{api_v1_prefix}/users", params={"created_by": new_key_id}, headers=dev_headers)
        assert {item["id"] for item in listed.json()["items"]} == {user_id}

    def test_rotation_still_hands_out_the_new_key_if_moving_users_fails(
        self, client: TestClient, api_v1_prefix: str, developer: Developer, ams_key: ApiKey
    ) -> None:
        user_id = _create(client, api_v1_prefix, api_key_headers(ams_key.plain_key))["id"]
        dev_headers = developer_auth_headers(developer.id)

        with patch.object(
            api_key_user_repository,
            "reassign_created_by_api_key",
            side_effect=RuntimeError("boom"),
        ):
            rotated = client.post(f"{api_v1_prefix}/developer/api-keys/{ams_key.id}/rotate", headers=dev_headers)

        assert rotated.status_code == 201
        assert rotated.json()["key"].startswith("sk-")
        # The user falls back to the name its key had, as after a delete.
        assert _created_by(client, api_v1_prefix, dev_headers, user_id) == {
            "via": "api_key",
            "label": "AMS",
            "api_key_id": None,
        }

    def test_rotation_leaves_other_keys_users_alone(
        self, client: TestClient, api_v1_prefix: str, developer: Developer, ams_key: ApiKey, sleep_hub_key: ApiKey
    ) -> None:
        sleep_hub_user = _create(client, api_v1_prefix, api_key_headers(sleep_hub_key.plain_key))["id"]
        dev_headers = developer_auth_headers(developer.id)

        client.post(f"{api_v1_prefix}/developer/api-keys/{ams_key.id}/rotate", headers=dev_headers)

        created_by = _created_by(client, api_v1_prefix, dev_headers, sleep_hub_user)
        assert created_by["api_key_id"] == str(sleep_hub_key.id)


class TestReadingAttribution:
    def test_pre_existing_user_reads_as_unknown(
        self, client: TestClient, api_v1_prefix: str, developer: Developer
    ) -> None:
        legacy = UserFactory()
        headers = developer_auth_headers(developer.id)

        assert _created_by(client, api_v1_prefix, headers, str(legacy.id)) == UNKNOWN
        listed = client.get(f"{api_v1_prefix}/users", headers=headers).json()["items"]
        assert [item["created_by"] for item in listed if item["id"] == str(legacy.id)] == [UNKNOWN]

    def test_raw_attribution_columns_stay_out_of_the_payload(
        self, client: TestClient, api_v1_prefix: str, ams_key: ApiKey
    ) -> None:
        headers = api_key_headers(ams_key.plain_key)
        created = _create(client, api_v1_prefix, headers)
        detail = client.get(f"{api_v1_prefix}/users/{created['id']}", headers=headers).json()
        listed = client.get(f"{api_v1_prefix}/users", headers=headers).json()["items"][0]

        hidden = {"created_via", "created_by_api_key_id", "created_by_api_key_name", "created_by_label"}
        for body in (created, detail, listed):
            assert "created_by" in body
            assert not hidden & body.keys()

    def test_update_and_delete_responses_carry_attribution(
        self, client: TestClient, api_v1_prefix: str, ams_key: ApiKey
    ) -> None:
        headers = api_key_headers(ams_key.plain_key)
        user_id = _create(client, api_v1_prefix, headers)["id"]
        expected = {"via": "api_key", "label": "AMS", "api_key_id": str(ams_key.id)}

        updated = client.patch(f"{api_v1_prefix}/users/{user_id}", json={"first_name": "Ada"}, headers=headers)
        assert updated.status_code == 200
        assert updated.json()["created_by"] == expected

        deleted = client.delete(f"{api_v1_prefix}/users/{user_id}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["created_by"] == expected


class TestCreatedByFilter:
    @pytest.fixture
    def users(
        self, client: TestClient, api_v1_prefix: str, developer: Developer, ams_key: ApiKey, sleep_hub_key: ApiKey
    ) -> dict[str, str]:
        gone_key = ApiKeyFactory(developer=developer, name="Retired app")
        users = {
            "ams": _create(client, api_v1_prefix, api_key_headers(ams_key.plain_key))["id"],
            "sleep_hub": _create(client, api_v1_prefix, api_key_headers(sleep_hub_key.plain_key))["id"],
            "retired": _create(client, api_v1_prefix, api_key_headers(gone_key.plain_key))["id"],
            "portal": _create(client, api_v1_prefix, developer_auth_headers(developer.id))["id"],
            "legacy": str(UserFactory().id),
        }
        client.delete(f"{api_v1_prefix}/developer/api-keys/{gone_key.id}", headers=developer_auth_headers(developer.id))
        return users

    def _ids(self, response: Response) -> set[str]:
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    def _list(self, client: TestClient, prefix: str, developer: Developer, created_by: str) -> Response:
        return client.get(
            f"{prefix}/users", params={"created_by": created_by}, headers=developer_auth_headers(developer.id)
        )

    def test_by_api_key_id(
        self, client: TestClient, api_v1_prefix: str, developer: Developer, ams_key: ApiKey, users: dict
    ) -> None:
        assert self._ids(self._list(client, api_v1_prefix, developer, str(ams_key.id))) == {users["ams"]}

    def test_any_api_key_includes_deleted_keys(
        self, client: TestClient, api_v1_prefix: str, developer: Developer, users: dict
    ) -> None:
        response = self._list(client, api_v1_prefix, developer, "api_key")
        assert self._ids(response) == {users["ams"], users["sleep_hub"], users["retired"]}
        assert response.json()["total"] == 3

    def test_portal(self, client: TestClient, api_v1_prefix: str, developer: Developer, users: dict) -> None:
        assert self._ids(self._list(client, api_v1_prefix, developer, "portal")) == {users["portal"]}

    def test_unknown(self, client: TestClient, api_v1_prefix: str, developer: Developer, users: dict) -> None:
        assert self._ids(self._list(client, api_v1_prefix, developer, "unknown")) == {users["legacy"]}

    def test_seed_matches_nothing_here(
        self, client: TestClient, api_v1_prefix: str, developer: Developer, users: dict
    ) -> None:
        assert self._ids(self._list(client, api_v1_prefix, developer, "seed")) == set()

    def test_key_that_never_existed_matches_nothing(
        self, client: TestClient, api_v1_prefix: str, developer: Developer, users: dict
    ) -> None:
        assert self._ids(self._list(client, api_v1_prefix, developer, str(uuid4()))) == set()

    def test_unrecognised_value_is_rejected(
        self, client: TestClient, api_v1_prefix: str, developer: Developer, users: dict
    ) -> None:
        assert self._list(client, api_v1_prefix, developer, "sleep-hub").status_code == 400


class TestSeedAttribution:
    def test_seed_generator_records_seed(self, db: Session) -> None:
        request = SeedDataRequest(
            num_users=2,
            profile=SeedProfileConfig(generate_workouts=False, generate_sleep=False, generate_time_series=False),
        )

        seed_data_service.generate(db, request)

        users = db.query(User).all()
        assert len(users) == 2
        for user in users:
            assert user.created_via == UserCreatedVia.SEED
            assert user.created_by_api_key_id is None
            assert user.created_by_developer_id is None
            assert user.created_by_label is None

    def test_seeded_user_reads_as_seed_data(
        self, client: TestClient, db: Session, api_v1_prefix: str, developer: Developer
    ) -> None:
        request = SeedDataRequest(
            num_users=1,
            profile=SeedProfileConfig(generate_workouts=False, generate_sleep=False, generate_time_series=False),
        )
        seed_data_service.generate(db, request)
        seeded = db.query(User).one()

        created_by = _created_by(client, api_v1_prefix, developer_auth_headers(developer.id), str(seeded.id))
        assert created_by == {"via": "seed", "label": "Seed data", "api_key_id": None}
