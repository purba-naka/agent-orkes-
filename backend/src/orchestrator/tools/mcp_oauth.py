"""OAuth 2.1 client for remote MCP servers (MCP authorization spec).

Flow: discover protected-resource + authorization-server metadata, register a
public client dynamically, send the user through authorization code + PKCE
(S256), then store encrypted tokens and refresh them on demand.
"""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets
from typing import Any
from urllib.parse import urlencode, urlsplit
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import settings
from orchestrator.db.models import McpConnection
from orchestrator.security.encryption import vault
from orchestrator.tools.network import NetworkPolicy

CALLBACK_PATH = "/api/v1/mcp-connections/callback"
STATE_TTL = timedelta(minutes=10)
REFRESH_SKEW = timedelta(seconds=60)
_RESOURCE_METADATA = re.compile(r'resource_metadata="([^"]+)"')


class McpOAuthError(RuntimeError):
    pass



def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _well_known(url: str, name: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/.well-known/{name}{parts.path.rstrip('/')}"


def _aad(connection: McpConnection) -> bytes:
    return f"mcp_connection:{connection.id}".encode()


class McpOAuthService:
    def __init__(
        self,
        network_policy: NetworkPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.network_policy = network_policy or NetworkPolicy(
            set(settings.tool_local_allowlist)
        )
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        # ponytail: redirects disabled on every OAuth hop; every URL (including
        # ones taken from server metadata) goes through the SSRF policy.
        return httpx.AsyncClient(
            timeout=15, follow_redirects=False, trust_env=False, transport=self.transport
        )

    async def _request(
        self, client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        await self.network_policy.validate_url(url)
        return await client.request(method, url, **kwargs)

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> dict[str, Any]:
        response = await self._request(client, "GET", url)
        if response.status_code != 200:
            raise McpOAuthError(f"Metadata request failed ({response.status_code}): {url}")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise McpOAuthError(f"Metadata is not JSON: {url}") from exc

    async def discover(self, server_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
        async with self._client() as client:
            probe = await self._request(
                client,
                "POST",
                server_url,
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
            )
            if probe.status_code != 401:
                raise McpOAuthError("MCP server does not require OAuth")
            match = _RESOURCE_METADATA.search(probe.headers.get("www-authenticate", ""))
            resource = await self._get_json(
                client,
                match.group(1) if match else _well_known(server_url, "oauth-protected-resource"),
            )
            servers = resource.get("authorization_servers") or []
            if not servers:
                raise McpOAuthError("Protected resource lists no authorization server")
            auth = await self._get_json(
                client, _well_known(str(servers[0]), "oauth-authorization-server")
            )
        for field in ("authorization_endpoint", "token_endpoint"):
            if urlsplit(str(auth.get(field, ""))).scheme not in {"https", "http"}:
                raise McpOAuthError(f"Authorization server metadata lacks {field}")
        if "S256" not in auth.get("code_challenge_methods_supported", []):
            raise McpOAuthError("Authorization server does not support PKCE S256")
        return resource, auth

    async def create(
        self,
        session: AsyncSession,
        *,
        name: str,
        server_url: str,
        origin: str,
        scope: str | None = None,
    ) -> tuple[McpConnection, str]:
        """`origin` is the browser-facing origin; callback and return page hang off it."""
        redirect_uri = origin.rstrip("/") + CALLBACK_PATH
        resource, auth = await self.discover(server_url)
        registration_endpoint = auth.get("registration_endpoint")
        if not registration_endpoint:
            raise McpOAuthError("Authorization server does not support client registration")
        async with self._client() as client:
            response = await self._request(
                client,
                "POST",
                str(registration_endpoint),
                json={
                    "client_name": "Agent Orchestrator",
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
            )
        if response.status_code not in {200, 201}:
            raise McpOAuthError(f"Client registration failed ({response.status_code})")
        client_id = response.json().get("client_id")
        if not client_id:
            raise McpOAuthError("Client registration returned no client_id")

        connection = McpConnection(
            id=uuid.uuid4(),
            name=name,
            transport="streamable_http",
            auth="oauth",
            server_url=server_url,
            status="pending",
            authorization_endpoint=str(auth["authorization_endpoint"]),
            token_endpoint=str(auth["token_endpoint"]),
            client_id=str(client_id),
            redirect_uri=redirect_uri,
            return_url=origin.rstrip("/") + "/",
            scope=scope or " ".join(resource.get("scopes_supported", [])) or None,
        )
        url = self._begin(connection)
        session.add(connection)
        await session.commit()
        return connection, url

    async def reauthorize(self, session: AsyncSession, connection: McpConnection) -> str:
        url = self._begin(connection)
        await session.commit()
        return url

    def _begin(self, connection: McpConnection) -> str:
        verifier, challenge = _pkce_pair()
        connection.oauth_state = secrets.token_urlsafe(32)
        connection.code_verifier = verifier
        connection.updated_at = _now()
        params = {
            "response_type": "code",
            "client_id": connection.client_id,
            "redirect_uri": connection.redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": connection.oauth_state,
            "resource": connection.server_url,
        }
        if connection.scope:
            params["scope"] = connection.scope
        return f"{connection.authorization_endpoint}?{urlencode(params)}"

    async def complete(self, session: AsyncSession, *, state: str, code: str) -> McpConnection:
        connection = await session.scalar(
            select(McpConnection)
            .where(McpConnection.oauth_state == state)
            .with_for_update()
        )
        if not connection or not connection.code_verifier:
            raise McpOAuthError("Unknown OAuth state")
        verifier = connection.code_verifier
        connection.oauth_state = connection.code_verifier = None
        if connection.updated_at < _now() - STATE_TTL:
            await session.commit()
            raise McpOAuthError("OAuth state expired")
        tokens = await self._token_request(
            connection,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": connection.redirect_uri,
                "code_verifier": verifier,
            },
        )
        self._store(connection, tokens, previous_refresh=None)
        connection.status = "connected"
        await session.commit()
        return connection

    async def access_token(self, session: AsyncSession, connection_id: uuid.UUID) -> str:
        connection = await session.get(McpConnection, connection_id)
        if not connection or connection.status != "connected":
            raise McpOAuthError("MCP connection is not authorized")
        if self._fresh(connection):
            return self._load(connection)["access_token"]

        # Serialize refreshes: another worker may have rotated the token already.
        connection = await session.get(
            McpConnection, connection_id, with_for_update=True, populate_existing=True
        )
        assert connection is not None
        tokens = self._load(connection)
        if self._fresh(connection):
            await session.commit()
            return tokens["access_token"]
        refresh_token = tokens.get("refresh_token")
        try:
            if not refresh_token:
                raise McpOAuthError("Access token expired and no refresh token is available")
            new_tokens = await self._token_request(
                connection, {"grant_type": "refresh_token", "refresh_token": refresh_token}
            )
        except McpOAuthError:
            connection.status = "needs_reauth"
            await session.commit()
            raise
        except BaseException:
            await session.rollback()
            raise
        self._store(connection, new_tokens, previous_refresh=refresh_token)
        await session.commit()
        return new_tokens["access_token"]

    @staticmethod
    def _fresh(connection: McpConnection) -> bool:
        return connection.expires_at is None or connection.expires_at > _now() + REFRESH_SKEW

    async def _token_request(
        self, connection: McpConnection, data: dict[str, str]
    ) -> dict[str, Any]:
        form = {**data, "client_id": connection.client_id, "resource": connection.server_url}
        async with self._client() as client:
            response = await self._request(
                client,
                "POST",
                connection.token_endpoint,
                data=form,
                headers={"Accept": "application/json"},
            )
        try:
            payload = response.json()
        except json.JSONDecodeError:
            payload = {}
        if response.status_code != 200 or not payload.get("access_token"):
            # Only the OAuth error code is surfaced; descriptions may echo input.
            raise McpOAuthError(
                f"Token endpoint rejected the request: {payload.get('error', response.status_code)}"
            )
        return payload

    @staticmethod
    def _store(
        connection: McpConnection, tokens: dict[str, Any], *, previous_refresh: str | None
    ) -> None:
        secret = json.dumps(
            {
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token") or previous_refresh,
            }
        )
        connection.token_ciphertext, connection.token_nonce, _ = vault.encrypt(
            secret, aad=_aad(connection)
        )
        expires_in = tokens.get("expires_in")
        connection.expires_at = (
            _now() + timedelta(seconds=int(expires_in)) if expires_in else None
        )

    @staticmethod
    def _load(connection: McpConnection) -> dict[str, Any]:
        if connection.token_ciphertext is None or connection.token_nonce is None:
            raise McpOAuthError("MCP connection has no stored token")
        return json.loads(
            vault.decrypt(connection.token_ciphertext, connection.token_nonce, aad=_aad(connection))
        )
