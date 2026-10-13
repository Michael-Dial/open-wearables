"""Which app created a user, taken from the credential of the request (fork).

``ApiKeyDep`` authenticates a request but returns a bare id that can be either a developer
or an API key, so it cannot say which. ``RequestCreatorDep`` authenticates the same way and
says what made the request, for the paths that create users. Nothing the client sends is
consulted, so an app cannot claim to be another one: it is named by the API key it holds.
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException

from app.database import DbSession
from app.models import Developer
from app.schemas.model_crud.user_management import UserCreatedVia
from app.services.api_key_service import api_key_service
from app.utils.auth import get_current_developer_optional

# User.created_by_label is VARCHAR(100); an API key's name is unbounded.
CREATED_BY_LABEL_MAX_LENGTH = 100


@dataclass(frozen=True)
class RequestCreator:
    """The credential a new user is attributed to.

    ``label`` is the API key's name at the time, kept on the user so the attribution
    survives the key being deleted.
    """

    via: UserCreatedVia
    developer_id: UUID | None = None
    api_key_id: UUID | None = None
    label: str | None = None


# The seed-data generator runs outside a request (Celery task, `make seed`).
SEED_CREATOR = RequestCreator(via=UserCreatedVia.SEED)


async def _resolve_request_creator(
    db: DbSession,
    developer: Developer | None = Depends(get_current_developer_optional),
    x_open_wearables_api_key: str | None = Header(None, alias="X-Open-Wearables-API-Key"),
) -> RequestCreator:
    """Same rule and errors as ``ApiKeyDep``: developer JWT first, then the API key header."""
    if developer:
        return RequestCreator(via=UserCreatedVia.PORTAL, developer_id=developer.id)
    if x_open_wearables_api_key:
        api_key = api_key_service.validate_api_key(db, x_open_wearables_api_key)
        return RequestCreator(
            via=UserCreatedVia.API_KEY,
            api_key_id=api_key.id,
            label=api_key.name[:CREATED_BY_LABEL_MAX_LENGTH],
        )
    raise HTTPException(status_code=401, detail="Authentication required: provide JWT token or API key")


RequestCreatorDep = Annotated[RequestCreator, Depends(_resolve_request_creator)]
