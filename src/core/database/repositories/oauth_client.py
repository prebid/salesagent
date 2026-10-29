"""Repository for first-party MCP OAuth clients."""

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from src.core.database.models import OAuthAuthorizationCode, OAuthClient
from src.core.oauth_service import DEFAULT_MCP_OAUTH_SCOPE, OAuthClientCredentials, validate_oauth_redirect_uri


class OAuthClientRepository:
    """Data access for OAuth clients tied to advertiser principals."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_active(
        self,
        *,
        tenant_id: str,
        client_id: str | None = None,
        principal_id: str | None = None,
    ) -> OAuthClient | None:
        stmt = select(OAuthClient).filter_by(tenant_id=tenant_id, is_active=True)
        if client_id is not None:
            stmt = stmt.filter_by(client_id=client_id)
        if principal_id is not None:
            stmt = stmt.filter_by(principal_id=principal_id)
        return self._session.scalars(stmt).first()

    def get_active_client(
        self,
        *,
        tenant_id: str,
        client_id: str,
        principal_id: str | None = None,
    ) -> OAuthClient | None:
        return self.get_active(tenant_id=tenant_id, client_id=client_id, principal_id=principal_id)

    def get_active_client_by_client_id(self, client_id: str) -> OAuthClient | None:
        return self.find_active_by_client_id_across_tenants(client_id)

    def find_active_by_client_id_across_tenants(self, client_id: str) -> OAuthClient | None:
        stmt = select(OAuthClient).filter_by(client_id=client_id, is_active=True)
        return self._session.scalars(stmt).first()

    def get_active_client_by_principal(self, *, tenant_id: str, principal_id: str) -> OAuthClient | None:
        return self.get_active(tenant_id=tenant_id, principal_id=principal_id)

    def create_for_principal(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        credentials: OAuthClientCredentials,
        scopes: list[str] | None = None,
        redirect_uris: list[str] | None = None,
        created_at: datetime | None = None,
    ) -> OAuthClient:
        timestamp = created_at or datetime.now(UTC)
        validated_redirect_uris = _validated_redirect_uris(redirect_uris or [])
        oauth_client = OAuthClient(
            tenant_id=tenant_id,
            principal_id=principal_id,
            client_id=credentials.client_id,
            client_secret_hash=credentials.client_secret_hash,
            scopes=scopes or [DEFAULT_MCP_OAUTH_SCOPE],
            redirect_uris=validated_redirect_uris,
            is_active=True,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._session.add(oauth_client)
        return oauth_client

    def update_redirect_uris(self, oauth_client: OAuthClient, redirect_uris: list[str]) -> None:
        oauth_client.redirect_uris = _validated_redirect_uris(redirect_uris)
        oauth_client.updated_at = datetime.now(UTC)

    def create_authorization_code(
        self,
        *,
        code_hash: str,
        tenant_id: str,
        client_id: str,
        principal_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scopes: list[str],
        resource: str,
        expires_at: datetime,
        created_at: datetime | None = None,
    ) -> OAuthAuthorizationCode:
        timestamp = created_at or datetime.now(UTC)
        authorization_code = OAuthAuthorizationCode()
        for field_name, value in {
            "code_hash": code_hash,
            "tenant_id": tenant_id,
            "client_id": client_id,
            "principal_id": principal_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "scopes": scopes,
            "resource": resource,
            "expires_at": expires_at,
            "created_at": timestamp,
        }.items():
            setattr(authorization_code, field_name, value)
        self._session.add(authorization_code)
        return authorization_code

    def get_authorization_code(self, code_hash: str) -> OAuthAuthorizationCode | None:
        stmt = select(OAuthAuthorizationCode).filter_by(code_hash=code_hash)
        return self._session.scalars(stmt).first()

    def consume_authorization_code(self, code_hash: str, *, now: datetime) -> bool:
        stmt = (
            update(OAuthAuthorizationCode)
            .where(
                OAuthAuthorizationCode.code_hash == code_hash,
                OAuthAuthorizationCode.used_at.is_(None),
                OAuthAuthorizationCode.expires_at > now,
            )
            .values(used_at=now)
        )
        result = cast(CursorResult, self._session.execute(stmt))
        return result.rowcount == 1


def _validated_redirect_uris(redirect_uris: list[str]) -> list[str]:
    for redirect_uri in redirect_uris:
        validation_error = validate_oauth_redirect_uri(redirect_uri)
        if validation_error:
            raise ValueError(validation_error)
    return redirect_uris
