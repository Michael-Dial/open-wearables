from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Mapped

from app.database import BaseDbModel
from app.mappings import FKDeveloper, Indexed, PrimaryKey, str_64


class ProviderCredential(BaseDbModel):
    """One provider app setting entered in the dashboard, overriding the environment.

    A row per field (``whoop_client_id``, ``whoop_client_secret``, ...). A field with no
    row falls back to the backend's environment, so an instance configured only through
    ``.env`` behaves exactly as before. Secret values are stored Fernet-encrypted with a
    key derived from SECRET_KEY (see ``app/services/provider_credentials.py``).
    """

    __tablename__ = "provider_credential"

    key: Mapped[PrimaryKey[str_64]]
    provider: Mapped[Indexed[str_64]]
    value: Mapped[str]
    updated_at: Mapped[datetime]
    updated_by_id: Mapped[FKDeveloper | None]
