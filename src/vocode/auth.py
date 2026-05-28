from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Final, MutableMapping, Optional, Protocol

from pydantic import BaseModel, Field

from connect import auth as connect_auth
from connect import auth_router as connect_auth_router
from connect.credentials import base as connect_credentials_base
from vocode.config import default_credentials_path

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


class CredentialProfileMetadata(BaseModel):
    active_profile: str = "default"
    profiles: list[str] = Field(default_factory=list)


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
    profile: str = "default"
    has_stored_credentials: bool = False
    has_env_token: bool = False
    credentials_path: Optional[str] = None

    @property
    def is_authorized(self) -> bool:
        return self.has_stored_credentials or self.has_env_token


class AuthenticationCancelledError(RuntimeError):
    pass


class TokenCredentialManager(Protocol):
    async def get_token(
        self,
        name: str,
        *,
        context: Optional[connect_auth.AuthContext] = None,
    ) -> Optional[str]: ...

    async def set_token(
        self,
        name: str,
        value: Optional[str],
        *,
        context: Optional[connect_auth.AuthContext] = None,
    ) -> None: ...


class ProjectCredentialManager:
    _DEFAULT_PROFILE: Final[str] = "default"
    _PROFILE_METADATA_PROVIDER: Final[str] = "__auth_profiles__"
    _PROFILE_ENTRY_PREFIX: Final[str] = "__auth_profile_entry__:"

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

    def _empty_credentials_document(
        self,
    ) -> connect_credentials_base.StoredCredentialsDocument:
        return connect_credentials_base.StoredCredentialsDocument()

    def _normalize_profile_name(self, name: str) -> str:
        return name.strip()

    def _encode_profile_entry_key(self, profile: str, provider: str) -> str:
        encoded_profile = (
            base64.urlsafe_b64encode(profile.encode("utf-8"))
            .decode("ascii")
            .rstrip("=")
        )
        encoded_provider = (
            base64.urlsafe_b64encode(provider.encode("utf-8"))
            .decode("ascii")
            .rstrip("=")
        )
        return f"{self._PROFILE_ENTRY_PREFIX}{encoded_profile}:{encoded_provider}"

    def _decode_profile_entry_key(
        self,
        provider_key: str,
    ) -> Optional[tuple[str, str]]:
        if not provider_key.startswith(self._PROFILE_ENTRY_PREFIX):
            return None
        encoded_value = provider_key[len(self._PROFILE_ENTRY_PREFIX) :]
        parts = encoded_value.split(":", 1)
        if len(parts) != 2:
            return None
        encoded_profile, encoded_provider = parts
        try:
            profile = base64.urlsafe_b64decode(
                f"{encoded_profile}{'=' * (-len(encoded_profile) % 4)}"
            ).decode("utf-8")
            provider = base64.urlsafe_b64decode(
                f"{encoded_provider}{'=' * (-len(encoded_provider) % 4)}"
            ).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        if not profile or not provider:
            return None
        return profile, provider

    def _load_profile_documents(
        self,
    ) -> tuple[
        str,
        dict[str, connect_credentials_base.StoredCredentialsDocument],
    ]:
        document = self._credential_store.load_document(self._credentials_path)
        active_profile = self._DEFAULT_PROFILE
        profile_names = {self._DEFAULT_PROFILE}
        profile_documents = {self._DEFAULT_PROFILE: self._empty_credentials_document()}
        metadata_payload = document.credentials.get(self._PROFILE_METADATA_PROVIDER)
        if isinstance(metadata_payload, dict):
            metadata = CredentialProfileMetadata.model_validate(metadata_payload)
            active_profile = metadata.active_profile
            for profile_name in metadata.profiles:
                normalized = self._normalize_profile_name(profile_name)
                if normalized:
                    profile_names.add(normalized)
        for provider_name, payload in document.credentials.items():
            if provider_name == self._PROFILE_METADATA_PROVIDER:
                continue
            profile_entry = self._decode_profile_entry_key(provider_name)
            if profile_entry is None:
                profile_documents[self._DEFAULT_PROFILE].credentials[
                    provider_name
                ] = payload
                continue
            profile_name, nested_provider_name = profile_entry
            profile_names.add(profile_name)
            profile_document = profile_documents.setdefault(
                profile_name,
                self._empty_credentials_document(),
            )
            profile_document.credentials[nested_provider_name] = payload
        for profile_name in profile_names:
            profile_documents.setdefault(
                profile_name,
                self._empty_credentials_document(),
            )
        if active_profile not in profile_documents:
            profile_documents[active_profile] = self._empty_credentials_document()
        return active_profile, profile_documents

    def _save_profile_documents(
        self,
        *,
        active_profile: str,
        profile_documents: dict[
            str,
            connect_credentials_base.StoredCredentialsDocument,
        ],
    ) -> None:
        profile_names = {
            profile_name
            for profile_name in profile_documents
            if self._normalize_profile_name(profile_name)
        }
        profile_names.add(self._DEFAULT_PROFILE)
        profile_names.add(active_profile)
        document = connect_credentials_base.StoredCredentialsDocument()
        document.credentials[self._PROFILE_METADATA_PROVIDER] = (
            CredentialProfileMetadata(
                active_profile=active_profile,
                profiles=self._sort_profiles(profile_names),
            ).model_dump(mode="json")
        )
        default_document = profile_documents.get(self._DEFAULT_PROFILE)
        if default_document is not None:
            for provider_name, payload in default_document.credentials.items():
                document.credentials[provider_name] = payload
        for profile_name in self._sort_profiles(profile_names):
            if profile_name == self._DEFAULT_PROFILE:
                continue
            profile_document = profile_documents.get(profile_name)
            if profile_document is None:
                continue
            for provider_name, payload in profile_document.credentials.items():
                document.credentials[
                    self._encode_profile_entry_key(profile_name, provider_name)
                ] = payload
        self._credential_store.save_document(self._credentials_path, document)

    def _sort_profiles(self, profile_names: set[str]) -> list[str]:
        other_profiles = sorted(
            profile_name
            for profile_name in profile_names
            if profile_name != self._DEFAULT_PROFILE
        )
        return [self._DEFAULT_PROFILE, *other_profiles]

    def _current_profile_document(
        self,
    ) -> tuple[str, connect_credentials_base.StoredCredentialsDocument]:
        active_profile, profile_documents = self._load_profile_documents()
        return active_profile, profile_documents.setdefault(
            active_profile,
            self._empty_credentials_document(),
        )

    def _is_store_only_token(self, name: str) -> bool:
        return name.startswith("MCP_TOKEN_")

    async def get_active_profile(self) -> str:
        active_profile, _ = self._load_profile_documents()
        return active_profile

    async def list_profiles(self) -> list[str]:
        _, profile_documents = self._load_profile_documents()
        return self._sort_profiles(set(profile_documents.keys()))

    async def add_profile(self, name: str) -> bool:
        profile_name = self._normalize_profile_name(name)
        if not profile_name:
            raise ValueError("Profile name cannot be empty.")
        active_profile, profile_documents = self._load_profile_documents()
        if profile_name in profile_documents:
            return False
        profile_documents[profile_name] = self._empty_credentials_document()
        self._save_profile_documents(
            active_profile=active_profile,
            profile_documents=profile_documents,
        )
        return True

    async def switch_profile(self, name: str) -> bool:
        profile_name = self._normalize_profile_name(name)
        if not profile_name:
            raise ValueError("Profile name cannot be empty.")
        active_profile, profile_documents = self._load_profile_documents()
        if profile_name not in profile_documents:
            raise ValueError(f"Profile '{profile_name}' does not exist.")
        if profile_name == active_profile:
            return False
        self._save_profile_documents(
            active_profile=profile_name,
            profile_documents=profile_documents,
        )
        return True

    async def delete_profile(self, name: str) -> tuple[bool, str]:
        profile_name = self._normalize_profile_name(name)
        if not profile_name:
            raise ValueError("Profile name cannot be empty.")
        if profile_name == self._DEFAULT_PROFILE:
            raise ValueError("Profile 'default' cannot be deleted.")
        active_profile, profile_documents = self._load_profile_documents()
        if profile_name not in profile_documents:
            return False, active_profile
        del profile_documents[profile_name]
        next_active_profile = active_profile
        if active_profile == profile_name:
            next_active_profile = self._DEFAULT_PROFILE
        self._save_profile_documents(
            active_profile=next_active_profile,
            profile_documents=profile_documents,
        )
        return True, next_active_profile

    async def get_token(
        self,
        name: str,
        *,
        context: Optional[connect_auth.AuthContext] = None,
    ) -> Optional[str]:
        if not self._is_store_only_token(name):
            value = self._env.get(name)
            if value:
                return value
        _, document = self._current_profile_document()
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
        active_profile, profile_documents = self._load_profile_documents()
        profile_document = profile_documents.setdefault(
            active_profile,
            self._empty_credentials_document(),
        )
        if value is None:
            if not self._is_store_only_token(name):
                self._env.pop(name, None)
            profile_document.credentials.pop(provider_name, None)
            self._save_profile_documents(
                active_profile=active_profile,
                profile_documents=profile_documents,
            )
            return
        if not self._is_store_only_token(name):
            self._env[name] = value
        profile_document.credentials[provider_name] = (
            connect_credentials_base.OAuth2Credentials(
                provider=provider_name,
                access_token=value,
            ).model_dump(mode="json")
        )
        self._save_profile_documents(
            active_profile=active_profile,
            profile_documents=profile_documents,
        )

    async def get_oauth2_credentials(
        self,
        provider: str,
        *,
        context: Optional[connect_auth.AuthContext] = None,
    ) -> Optional[connect_credentials_base.OAuth2Credentials]:
        _, document = self._current_profile_document()
        payload = document.credentials.get(provider)
        if payload is None:
            return None
        try:
            credential_adapter = self._credential_registry.get(provider)
            return credential_adapter.credentials_type.model_validate(payload)
        except ValueError:
            return None

    async def set_oauth2_credentials(
        self,
        provider: str,
        credentials: Optional[connect_credentials_base.OAuth2Credentials],
        *,
        context: Optional[connect_auth.AuthContext] = None,
    ) -> None:
        active_profile, profile_documents = self._load_profile_documents()
        profile_document = profile_documents.setdefault(
            active_profile,
            self._empty_credentials_document(),
        )
        if credentials is None:
            profile_document.credentials.pop(provider, None)
            self._save_profile_documents(
                active_profile=active_profile,
                profile_documents=profile_documents,
            )
            return
        profile_document.credentials[provider] = credentials.model_dump(mode="json")
        self._save_profile_documents(
            active_profile=active_profile,
            profile_documents=profile_documents,
        )

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
        active_profile = await self.get_active_profile()
        credentials = await self.get_oauth2_credentials(provider)
        return ProviderAuthorizationStatus(
            provider=provider,
            profile=active_profile,
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
    from vocode.config import default_config_dir as resolve_default_config_dir

    return resolve_default_config_dir()


def default_credentials_path() -> Path:
    from vocode.config import (
        default_credentials_path as resolve_default_credentials_path,
    )

    return resolve_default_credentials_path()
