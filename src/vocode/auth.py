from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import MutableMapping, Optional

from pydantic import BaseModel, Field

from connect import auth as connect_auth
from connect import auth_router as connect_auth_router
from connect.credentials import base as connect_credentials_base

try:
    import keyring
    from keyring import errors as keyring_errors
except ImportError:
    keyring = None
    keyring_errors = None

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


logger = logging.getLogger(__name__)


class EncryptedCredentialsEnvelope(BaseModel):
    version: int = 1
    nonce: str
    ciphertext: str


class EncryptedCredentialStore(connect_credentials_base.CredentialStore):
    _KEYRING_SERVICE = "vocode.credentials"

    def __init__(self) -> None:
        super().__init__()
        self._warned_messages: set[str] = set()

    def encrypted_path_for(self, path: str | Path) -> Path:
        file_path = Path(path)
        return file_path.with_name(f"{file_path.stem}.encrypted{file_path.suffix}")

    def load_document(
        self,
        path: str | Path,
    ) -> connect_credentials_base.StoredCredentialsDocument:
        file_path = Path(path)
        encrypted_path = self.encrypted_path_for(file_path)
        secure_key = self._load_secure_key(file_path)

        if encrypted_path.exists() and file_path.exists():
            self._warn(
                "Both encrypted and plaintext credential files exist; using encrypted credentials.",
            )

        if encrypted_path.exists() and secure_key is not None:
            return self._load_encrypted_document(encrypted_path, secure_key)

        if file_path.exists() and not encrypted_path.exists():
            if secure_key is None:
                secure_key = self._load_secure_key(file_path, create=True)
            if secure_key is None:
                return super().load_document(file_path)
            document = super().load_document(file_path)
            self._save_encrypted_document(encrypted_path, document, secure_key)
            file_path.unlink(missing_ok=True)
            return document

        if encrypted_path.exists() and secure_key is None:
            self._warn(
                "Secure credential storage is unavailable; falling back to plaintext credential config.",
            )

        if not file_path.exists():
            return connect_credentials_base.StoredCredentialsDocument()
        return super().load_document(file_path)

    def save_document(
        self,
        path: str | Path,
        document: connect_credentials_base.StoredCredentialsDocument,
    ) -> None:
        file_path = Path(path)
        encrypted_path = self.encrypted_path_for(file_path)
        secure_key = self._load_secure_key(file_path, create=True)
        if secure_key is None:
            self._warn(
                "Secure credential storage is unavailable; falling back to plaintext credential config.",
            )
            super().save_document(file_path, document)
            return
        self._save_encrypted_document(encrypted_path, document, secure_key)
        file_path.unlink(missing_ok=True)

    def _load_encrypted_document(
        self,
        encrypted_path: Path,
        secure_key: bytes,
    ) -> connect_credentials_base.StoredCredentialsDocument:
        envelope = EncryptedCredentialsEnvelope.model_validate_json(
            encrypted_path.read_text(encoding="utf-8")
        )
        nonce = base64.b64decode(envelope.nonce)
        ciphertext = base64.b64decode(envelope.ciphertext)
        try:
            plaintext = AESGCM(secure_key).decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise ValueError("Unable to decrypt stored credentials") from exc
        return connect_credentials_base.StoredCredentialsDocument.model_validate_json(
            plaintext.decode("utf-8")
        )

    def _save_encrypted_document(
        self,
        encrypted_path: Path,
        document: connect_credentials_base.StoredCredentialsDocument,
        secure_key: bytes,
    ) -> None:
        encrypted_path.parent.mkdir(parents=True, exist_ok=True)
        plaintext = document.model_dump_json(indent=2).encode("utf-8")
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(secure_key).encrypt(nonce, plaintext, None)
        envelope = EncryptedCredentialsEnvelope(
            nonce=base64.b64encode(nonce).decode("ascii"),
            ciphertext=base64.b64encode(ciphertext).decode("ascii"),
        )
        encrypted_path.write_text(
            envelope.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _load_secure_key(
        self,
        path: Path,
        *,
        create: bool = False,
    ) -> Optional[bytes]:
        if keyring is None or keyring_errors is None:
            return None
        try:
            stored_value = keyring.get_password(
                self._KEYRING_SERVICE,
                self._keyring_username(path),
            )
            if stored_value:
                return base64.b64decode(stored_value)
            if not create:
                return None
            secure_key = secrets.token_bytes(32)
            keyring.set_password(
                self._KEYRING_SERVICE,
                self._keyring_username(path),
                base64.b64encode(secure_key).decode("ascii"),
            )
            return secure_key
        except keyring_errors.KeyringError:
            return None

    def _keyring_username(self, path: Path) -> str:
        return str(path.expanduser().resolve())

    def _warn(self, message: str) -> None:
        if message in self._warned_messages:
            return
        self._warned_messages.add(message)
        logger.warning(message)


class ProviderAuthorizationStatus(BaseModel):
    provider: str
    has_stored_credentials: bool = False
    has_env_token: bool = False
    credentials_path: Optional[str] = None

    @property
    def is_authorized(self) -> bool:
        return self.has_stored_credentials or self.has_env_token


class AuthenticationCancelledError(RuntimeError):
    pass


class ProjectCredentialManager:
    def __init__(
        self,
        *,
        env: Optional[MutableMapping[str, str]] = None,
        credential_registry: Optional[
            connect_credentials_base.CredentialRegistry
        ] = None,
        credential_store: Optional[connect_credentials_base.CredentialStore] = None,
        credentials_path: Optional[Path] = None,
    ) -> None:
        self._env = env if env is not None else os.environ
        self._credential_registry = (
            credential_registry
            or connect_credentials_base.build_default_credential_registry()
        )
        self._credential_store = credential_store or EncryptedCredentialStore()
        self._credentials_path = credentials_path or default_credentials_path()

    @property
    def credentials_path(self) -> Path:
        return self._credentials_path

    def _token_provider_name(self, name: str) -> str:
        return f"token:{name}"

    async def get_token(
        self,
        name: str,
        *,
        context: Optional[connect_auth.AuthContext] = None,
    ) -> Optional[str]:
        value = self._env.get(name)
        if value:
            return value
        document = self._credential_store.load_document(self._credentials_path)
        payload = document.credentials.get(self._token_provider_name(name))
        if payload is None:
            return None
        token_value = payload.get("access_token")
        if not isinstance(token_value, str) or not token_value:
            return None
        return token_value

    async def set_token(
        self,
        name: str,
        value: Optional[str],
        *,
        context: Optional[connect_auth.AuthContext] = None,
    ) -> None:
        provider_name = self._token_provider_name(name)
        if value is None:
            self._env.pop(name, None)
            self._credential_store.delete(
                self._credentials_path, provider=provider_name
            )
            return
        self._env[name] = value
        self._credential_store.save(
            self._credentials_path,
            connect_credentials_base.OAuth2Credentials(
                provider=provider_name,
                access_token=value,
            ),
        )

    async def get_oauth2_credentials(
        self,
        provider: str,
        *,
        context: Optional[connect_auth.AuthContext] = None,
    ) -> Optional[connect_credentials_base.OAuth2Credentials]:
        try:
            return self._credential_store.load(
                self._credentials_path,
                provider=provider,
                registry=self._credential_registry,
            )
        except ValueError:
            return None

    async def set_oauth2_credentials(
        self,
        provider: str,
        credentials: Optional[connect_credentials_base.OAuth2Credentials],
        *,
        context: Optional[connect_auth.AuthContext] = None,
    ) -> None:
        if credentials is None:
            self._credential_store.delete(self._credentials_path, provider=provider)
            return
        self._credential_store.save(self._credentials_path, credentials)

    async def get_oauth_login_callbacks(
        self,
        provider: str,
        *,
        context: Optional[connect_auth.AuthContext] = None,
    ) -> Optional[connect_credentials_base.OAuthLoginCallbacks]:
        return None

    async def login(
        self,
        provider: str,
        callbacks: connect_credentials_base.OAuthLoginCallbacks,
    ) -> connect_credentials_base.OAuth2Credentials:
        adapter = self._credential_registry.get(provider)
        credentials = await adapter.login(callbacks)
        await self.set_oauth2_credentials(provider, credentials)
        return credentials

    async def logout(self, provider: str) -> None:
        await self.set_oauth2_credentials(provider, None)

    async def authorization_status(self, provider: str) -> ProviderAuthorizationStatus:
        credentials = await self.get_oauth2_credentials(provider)
        return ProviderAuthorizationStatus(
            provider=provider,
            has_stored_credentials=credentials is not None,
            has_env_token=self._has_env_token(provider),
            credentials_path=str(self._credentials_path),
        )

    async def has_active_authorization(self, provider: str) -> bool:
        status = await self.authorization_status(provider)
        return status.is_authorized

    async def resolve(
        self,
        provider: str,
        *,
        model: Optional[str] = None,
        api_family: Optional[str] = None,
    ) -> connect_auth.ResolvedAuth:
        router = connect_auth_router.DynamicAuthRouter(
            credential_manager=self,
            credential_registry=self._credential_registry,
        )
        return await router.resolve(
            connect_auth.AuthContext(
                provider=provider,
                model=model,
                api_family=api_family,
            )
        )

    def build_login_callbacks(
        self,
        server,
        provider: str,
    ) -> connect_credentials_base.OAuthLoginCallbacks:
        loop = asyncio.get_running_loop()

        def _send_text(text: str) -> None:
            loop.create_task(server.send_text_message(text))

        def _send_markdown(text: str) -> None:
            loop.create_task(
                server.send_text_message(
                    text,
                    text_format=server.manager_proto.TextMessageFormat.MARKDOWN,
                )
            )

        def _on_auth(info: connect_credentials_base.OAuthAuthInfo) -> None:
            parts = [f"Open this URL in your browser to authenticate with {provider}:"]
            parts.append(f"<{info.url}>")
            if info.instructions:
                parts.append(info.instructions)
            _send_markdown("\n\n".join(parts))

        def _on_progress(message: str) -> None:
            _send_text(message)

        async def _on_prompt(prompt: connect_credentials_base.OAuthPrompt) -> str:
            subtitle = prompt.message
            if prompt.placeholder:
                subtitle = f"{subtitle}\n{prompt.placeholder}"
            return await server.request_text_input(
                title=f"Authentication for {provider}",
                subtitle=f"{subtitle}\nType /auth cancel to abort.",
            )

        return connect_credentials_base.OAuthLoginCallbacks(
            on_auth=_on_auth,
            on_prompt=_on_prompt,
            on_progress=_on_progress,
        )

    def _has_env_token(self, provider: str) -> bool:
        env_var_by_provider = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "chatgpt": "CHATGPT_ACCESS_TOKEN",
        }
        env_var = env_var_by_provider.get(provider)
        if env_var is None:
            return False
        value = self._env.get(env_var)
        if provider == "gemini" and not value:
            value = self._env.get("GOOGLE_API_KEY")
        return bool(value)


class ServerAuthenticationSession:
    def __init__(self, server, provider: str) -> None:
        self.server = server
        self.provider = provider
        self._task: Optional[asyncio.Task] = None
        self._cancelled = False

    @property
    def is_active(self) -> bool:
        task = self._task
        if task is None:
            return False
        return not task.done()

    async def run(self) -> connect_credentials_base.OAuth2Credentials:
        if self.is_active:
            raise RuntimeError("Authentication is already in progress.")
        callbacks = self.server.manager.project.credentials.build_login_callbacks(
            self.server,
            self.provider,
        )
        self._cancelled = False
        self._task = asyncio.create_task(
            self.server.manager.project.credentials.login(self.provider, callbacks)
        )
        try:
            return await self._task
        except asyncio.CancelledError as exc:
            raise AuthenticationCancelledError(
                f"Authentication cancelled for {self.provider}."
            ) from exc
        finally:
            self._task = None

    async def cancel(self) -> bool:
        task = self._task
        if task is None or task.done():
            return False
        self._cancelled = True
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return True


def default_config_dir() -> Path:
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = home / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return base / "vocode"


def default_credentials_path() -> Path:
    return default_config_dir() / "credentials.json"
