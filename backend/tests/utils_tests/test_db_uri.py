"""Tests for the Settings.db_uri property (URL-encoding of credentials)."""

from unittest.mock import patch

from pydantic import SecretStr
from sqlalchemy.engine import make_url

from app.config import settings


class TestDbUri:
    """Test suite for Settings.db_uri."""

    def test_plain_credentials_are_unchanged(self) -> None:
        with (
            patch.object(settings, "db_user", "open-wearables"),
            patch.object(settings, "db_password", SecretStr("open-wearables")),
            patch.object(settings, "db_host", "db"),
            patch.object(settings, "db_port", 5432),
            patch.object(settings, "db_name", "open-wearables"),
        ):
            assert settings.db_uri == "postgresql+psycopg://open-wearables:open-wearables@db:5432/open-wearables"

    def test_special_characters_in_credentials_round_trip(self) -> None:
        # Unencoded, '@' moved the host into the password, '%41' was silently decoded
        # to 'A', and the mix below made SQLAlchemy fail to parse the port.
        user = "app:user@x"
        password = "p@ss:w/rd?#%41 x"
        with (
            patch.object(settings, "db_user", user),
            patch.object(settings, "db_password", SecretStr(password)),
            patch.object(settings, "db_host", "db.example.com"),
            patch.object(settings, "db_port", 5432),
            patch.object(settings, "db_name", "open-wearables"),
        ):
            url = make_url(settings.db_uri)

        assert url.username == user
        assert url.password == password
        assert url.host == "db.example.com"
        assert url.port == 5432
        assert url.database == "open-wearables"
