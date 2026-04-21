from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from typing import Optional, Protocol
from urllib import parse

import aiohttp
from pydantic import BaseModel, Field

from vocode import settings as vocode_settings


class MCPAuthError(Exception):
    pass


class MCPTokenManager(Protocol):
    async def get_token(
        self, name: str, *, context: object = None
    ) -> Optional[str]: ...

    async def set_token(
        self,
        name: str,
        value: Optional[str],
        *,
        context: object = None,
    ) -> None: ...


class MCPAuthChallenge(BaseModel):
    scheme: str
    params: dict[str, str] = Field(default_factory=dict)


class MCPProtectedResourceMetadata(BaseModel):
    resource: str
    authorization_servers: list[str] = Field(default_factory=list)
    scopes_supported: list[str] = Field(default_factory=list)


class MCPAuthorizationServerMetadata(BaseModel):
    issuer: Optional[str] = Field(default=None)
    authorization_endpoint: Optional[str] = Field(default=None)
    token_endpoint: str
    registration_endpoint: Optional[str] = Field(default=None)
    code_challenge_methods_supported: list[str] = Field(default_factory=list)


class MCPAuthToken(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_at: Optional[float] = Field(default=None)
    refresh_token: Optional[str] = Field(default=None)
    resource: str
    scope: Optional[str] = Field(default=None)

    def is_expired(self, *, skew_seconds: float = 30.0) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= time.time() + skew_seconds


class MCPAuthManager:
    def __init__(
        self,
        settings: Optional[vocode_settings.MCPSettings],
        *,
        credentials: Optional[MCPTokenManager] = None,
    ) -> None:
        self._settings = settings
        self._credentials = credentials

    async def resolve_headers(
        self,
        source_name: str,
        source_settings: vocode_settings.MCPExternalSourceSettings,
    ) -> dict[str, str]:
        auth_settings = source_settings.auth
        if auth_settings is None or not auth_settings.enabled:
            return {}
        resource_uri = self.canonicalize_resource_uri(source_settings.url)
        token = await self._load_cached_token(source_name, resource_uri)
        if token is None or token.is_expired():
            token = await self._acquire_token(
                source_name,
                source_settings,
                resource_uri,
            )
            await self._store_token(source_name, token)
        return {"Authorization": f"{token.token_type} {token.access_token}"}

    def canonicalize_resource_uri(self, value: str) -> str:
        parsed = parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"}:
            raise MCPAuthError("mcp auth resource URI must use http or https")
        hostname = parsed.hostname
        if hostname is None:
            raise MCPAuthError("mcp auth resource URI must include a hostname")
        hostname = hostname.lower()
        netloc = hostname
        if parsed.port is not None:
            is_default_port = parsed.scheme == "http" and parsed.port == 80
            is_default_port = is_default_port or (
                parsed.scheme == "https" and parsed.port == 443
            )
            if not is_default_port:
                netloc = f"{hostname}:{parsed.port}"
        path = parsed.path or "/"
        return parse.urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))

    def parse_www_authenticate(
        self,
        value: Optional[str],
    ) -> Optional[MCPAuthChallenge]:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        parts = stripped.split(" ", 1)
        scheme = parts[0].strip()
        params_text = ""
        if len(parts) > 1:
            params_text = parts[1].strip()
        params: dict[str, str] = {}
        if params_text:
            for raw_item in params_text.split(","):
                item = raw_item.strip()
                if "=" not in item:
                    continue
                key, raw_value = item.split("=", 1)
                parsed_value = raw_value.strip()
                if (
                    len(parsed_value) >= 2
                    and parsed_value[0] == '"'
                    and parsed_value[-1] == '"'
                ):
                    parsed_value = parsed_value[1:-1]
                params[key.strip()] = parsed_value
        return MCPAuthChallenge(scheme=scheme, params=params)

    async def discover_protected_resource_metadata(
        self,
        resource_uri: str,
        *,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> MCPProtectedResourceMetadata:
        owns_session = session is None
        http_session = session or aiohttp.ClientSession()
        try:
            for url in self._build_protected_resource_metadata_urls(resource_uri):
                async with http_session.get(url) as response:
                    if response.status == 404:
                        continue
                    if response.status >= 400:
                        text = await response.text()
                        raise MCPAuthError(
                            f"protected resource metadata discovery failed for {resource_uri}: {response.status} {text}"
                        )
                    payload = await response.json()
                    servers = payload.get("authorization_servers")
                    if not isinstance(servers, list):
                        servers = payload.get("authorizationServers") or []
                    scopes_supported = payload.get("scopes_supported")
                    if not isinstance(scopes_supported, list):
                        scopes_supported = payload.get("scopesSupported") or []
                    return MCPProtectedResourceMetadata(
                        resource=str(payload.get("resource") or resource_uri),
                        authorization_servers=[str(item) for item in servers if item],
                        scopes_supported=[
                            str(item) for item in scopes_supported if item
                        ],
                    )
        except aiohttp.ClientError as exc:
            raise MCPAuthError(
                f"protected resource metadata discovery failed for {resource_uri}: {exc}"
            ) from exc
        finally:
            if owns_session:
                await http_session.close()
        raise MCPAuthError(f"protected resource metadata not found for {resource_uri}")

    async def discover_authorization_server_metadata(
        self,
        issuer: str,
        *,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> MCPAuthorizationServerMetadata:
        owns_session = session is None
        http_session = session or aiohttp.ClientSession()
        try:
            for url in self._build_authorization_server_metadata_urls(issuer):
                async with http_session.get(url) as response:
                    if response.status == 404:
                        continue
                    if response.status >= 400:
                        text = await response.text()
                        raise MCPAuthError(
                            f"authorization server metadata discovery failed for {issuer}: {response.status} {text}"
                        )
                    payload = await response.json()
                    token_endpoint = payload.get("token_endpoint")
                    if not isinstance(token_endpoint, str) or not token_endpoint:
                        raise MCPAuthError(
                            f"authorization server metadata for {issuer} is missing token_endpoint"
                        )
                    code_methods = payload.get("code_challenge_methods_supported")
                    if not isinstance(code_methods, list):
                        code_methods = []
                    return MCPAuthorizationServerMetadata(
                        issuer=(
                            str(payload.get("issuer"))
                            if payload.get("issuer") is not None
                            else None
                        ),
                        authorization_endpoint=(
                            str(payload.get("authorization_endpoint"))
                            if payload.get("authorization_endpoint") is not None
                            else None
                        ),
                        token_endpoint=token_endpoint,
                        registration_endpoint=(
                            str(payload.get("registration_endpoint"))
                            if payload.get("registration_endpoint") is not None
                            else None
                        ),
                        code_challenge_methods_supported=[
                            str(item) for item in code_methods if item
                        ],
                    )
        except aiohttp.ClientError as exc:
            raise MCPAuthError(
                f"authorization server metadata discovery failed for {issuer}: {exc}"
            ) from exc
        finally:
            if owns_session:
                await http_session.close()
        raise MCPAuthError(f"authorization server metadata not found for {issuer}")

    def build_pkce_pair(self) -> tuple[str, str]:
        verifier = secrets.token_urlsafe(48)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = self._base64url_encode(digest)
        return verifier, challenge

    def build_authorization_request_url(
        self,
        authorization_endpoint: str,
        *,
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        resource_uri: str,
        scopes: Optional[list[str]] = None,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "resource": self.canonicalize_resource_uri(resource_uri),
        }
        if scopes:
            params["scope"] = " ".join(scopes)
        parsed = parse.urlsplit(authorization_endpoint)
        existing = parse.parse_qsl(parsed.query, keep_blank_values=True)
        merged_query = parse.urlencode([*existing, *params.items()])
        return parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                merged_query,
                parsed.fragment,
            )
        )

    async def _acquire_token(
        self,
        source_name: str,
        source_settings: vocode_settings.MCPExternalSourceSettings,
        resource_uri: str,
    ) -> MCPAuthToken:
        auth_settings = source_settings.auth
        if auth_settings is None or not auth_settings.enabled:
            raise MCPAuthError(f"mcp auth is not enabled for source {source_name}")
        if auth_settings.mode in {
            vocode_settings.MCPAuthMode.client_metadata,
            vocode_settings.MCPAuthMode.dynamic,
        }:
            raise MCPAuthError(
                f"mcp auth mode {auth_settings.mode.value} is not implemented"
            )
        if not auth_settings.client_id:
            raise MCPAuthError(f"mcp auth for source {source_name} requires client_id")
        client_secret = await self._read_client_secret(auth_settings)
        if client_secret is None:
            raise MCPAuthError(
                f"mcp auth for source {source_name} requires client_secret_env"
            )
        async with aiohttp.ClientSession() as session:
            resource_metadata = await self.discover_protected_resource_metadata(
                resource_uri,
                session=session,
            )
            issuer = self._select_authorization_server(resource_metadata, resource_uri)
            auth_server = await self.discover_authorization_server_metadata(
                issuer,
                session=session,
            )
            scopes = list(auth_settings.scopes)
            if not scopes and resource_metadata.scopes_supported:
                scopes = list(resource_metadata.scopes_supported)
            canonical_resource = self.canonicalize_resource_uri(
                resource_metadata.resource
            )
            form_data = {
                "grant_type": "client_credentials",
                "client_id": auth_settings.client_id,
                "client_secret": client_secret,
                "resource": canonical_resource,
            }
            if scopes:
                form_data["scope"] = " ".join(scopes)
            async with session.post(
                auth_server.token_endpoint,
                data=form_data,
                headers={"Accept": "application/json"},
            ) as response:
                payload = await response.json()
                if response.status >= 400:
                    description = payload.get("error_description") or payload.get(
                        "error"
                    )
                    raise MCPAuthError(
                        f"token request failed for source {source_name}: {description or response.status}"
                    )
                access_token = payload.get("access_token")
                if not isinstance(access_token, str) or not access_token:
                    raise MCPAuthError(
                        f"token response for source {source_name} is missing access_token"
                    )
                expires_at: Optional[float] = None
                expires_in = payload.get("expires_in")
                if isinstance(expires_in, int | float):
                    expires_at = time.time() + float(expires_in)
                token_type = payload.get("token_type")
                if not isinstance(token_type, str) or not token_type:
                    token_type = "Bearer"
                refresh_token = payload.get("refresh_token")
                scope_value = payload.get("scope")
                return MCPAuthToken(
                    access_token=access_token,
                    token_type=token_type,
                    expires_at=expires_at,
                    refresh_token=(
                        str(refresh_token) if refresh_token is not None else None
                    ),
                    resource=canonical_resource,
                    scope=str(scope_value) if scope_value is not None else None,
                )

    def _select_authorization_server(
        self,
        resource_metadata: MCPProtectedResourceMetadata,
        resource_uri: str,
    ) -> str:
        if resource_metadata.authorization_servers:
            return resource_metadata.authorization_servers[0]
        parsed = parse.urlsplit(resource_uri)
        return parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    async def _read_client_secret(
        self,
        auth_settings: vocode_settings.MCPAuthSettings,
    ) -> Optional[str]:
        if auth_settings.client_secret_env is None:
            return None
        if self._credentials is not None:
            return await self._credentials.get_token(auth_settings.client_secret_env)
        value = os.environ.get(auth_settings.client_secret_env)
        if not value:
            return None
        return value

    async def _load_cached_token(
        self,
        source_name: str,
        resource_uri: str,
    ) -> Optional[MCPAuthToken]:
        if self._credentials is None:
            return None
        raw_value = await self._credentials.get_token(
            self._token_store_key(source_name, resource_uri)
        )
        if raw_value is None or raw_value == "":
            return None
        try:
            data = json.loads(raw_value)
        except json.JSONDecodeError:
            return MCPAuthToken(access_token=raw_value, resource=resource_uri)
        return MCPAuthToken.model_validate(data)

    async def _store_token(
        self,
        source_name: str,
        token: MCPAuthToken,
    ) -> None:
        if self._credentials is None:
            return
        await self._credentials.set_token(
            self._token_store_key(source_name, token.resource),
            token.model_dump_json(),
        )

    async def has_stored_token(self, source_name: str, resource_uri: str) -> bool:
        if self._credentials is None:
            return False
        canonical_resource_uri = self.canonicalize_resource_uri(resource_uri)
        value = await self._credentials.get_token(
            self._token_store_key(source_name, canonical_resource_uri)
        )
        return value is not None and value != ""

    async def clear_token(self, source_name: str, resource_uri: str) -> None:
        if self._credentials is None:
            return
        canonical_resource_uri = self.canonicalize_resource_uri(resource_uri)
        await self._credentials.set_token(
            self._token_store_key(source_name, canonical_resource_uri),
            None,
        )

    def _token_store_key(self, source_name: str, resource_uri: str) -> str:
        digest = hashlib.sha256(resource_uri.encode("utf-8")).hexdigest()
        normalized = source_name.replace("-", "_").replace(".", "_")
        return f"MCP_TOKEN_{normalized.upper()}_{digest[:16].upper()}"

    def _build_protected_resource_metadata_urls(self, resource_uri: str) -> list[str]:
        parsed = parse.urlsplit(resource_uri)
        base = parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        path = parsed.path.strip("/")
        urls: list[str] = []
        if path:
            urls.append(f"{base}/.well-known/oauth-protected-resource/{path}")
        urls.append(f"{base}/.well-known/oauth-protected-resource")
        return urls

    def _build_authorization_server_metadata_urls(self, issuer: str) -> list[str]:
        normalized = issuer.rstrip("/")
        return [
            f"{normalized}/.well-known/oauth-authorization-server",
            f"{normalized}/.well-known/openid-configuration",
        ]

    def _base64url_encode(self, value: bytes) -> str:
        encoded = base64.urlsafe_b64encode(value).decode("ascii")
        return encoded.rstrip("=").replace("+", "-").replace("/", "_")
