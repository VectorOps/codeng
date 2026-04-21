from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from connect.credentials import chatgpt as connect_chatgpt_credentials
from connect.credentials import base as connect_credentials_base

from vocode import auth
from vocode.auth import EncryptedCredentialStore, ProjectCredentialManager


def _jwt(account_id: str = "acct_123") -> str:
    def _encode(payload: dict) -> str:
        return (
            base64.urlsafe_b64encode(
                json.dumps(payload, separators=(",", ":")).encode()
            )
            .decode()
            .rstrip("=")
        )

    return (
        f"{_encode({'alg': 'none', 'typ': 'JWT'})}."
        f"{_encode({'https://api.openai.com/auth': {'chatgpt_account_id': account_id}})}."
        "signature"
    )


@pytest.mark.asyncio
async def test_project_credential_manager_status_uses_store_and_env(tmp_path) -> None:
    manager = ProjectCredentialManager(
        env={"CHATGPT_ACCESS_TOKEN": _jwt("acct_env")},
        credentials_path=tmp_path / "credentials.json",
    )

    status = await manager.authorization_status("chatgpt")

    assert status.provider == "chatgpt"
    assert status.has_env_token is True
    assert status.has_stored_credentials is False
    assert status.is_authorized is True


@pytest.mark.asyncio
async def test_project_credential_manager_login_persists_credentials(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProjectCredentialManager(credentials_path=tmp_path / "credentials.json")

    async def _fake_login(self, callbacks):
        callbacks.on_auth(
            connect_chatgpt_credentials.OAuthAuthInfo(url="https://example.test/auth")
        )
        return connect_chatgpt_credentials.ChatGPTCredentials(
            access_token=_jwt("acct_login"),
            refresh_token="refresh_login",
            expires_at=time.time() + 600,
            account_id="acct_login",
        )

    monkeypatch.setattr(
        connect_chatgpt_credentials.ChatGPTCredentialProvider,
        "login",
        _fake_login,
    )

    prompted: list[str] = []

    async def _prompt(prompt):
        prompted.append(prompt.message)
        return ""

    callbacks = connect_chatgpt_credentials.OAuthLoginCallbacks(
        on_auth=lambda info: None,
        on_prompt=_prompt,
    )
    credentials = await manager.login("chatgpt", callbacks)
    stored = await manager.get_oauth2_credentials("chatgpt")

    assert credentials.account_id == "acct_login"
    assert stored is not None
    assert stored.provider == "chatgpt"
    assert stored.account_id == "acct_login"


@pytest.mark.asyncio
async def test_project_credential_manager_persists_raw_tokens_to_file(tmp_path) -> None:
    credentials_path = tmp_path / "credentials.json"
    manager = ProjectCredentialManager(credentials_path=credentials_path)

    await manager.set_token("MCP_TOKEN_TEST", "token-value")

    reloaded = ProjectCredentialManager(credentials_path=credentials_path)

    assert await reloaded.get_token("MCP_TOKEN_TEST") == "token-value"


class _MemoryKeyring:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._values[(service, username)] = password


def _plaintext_path(path: Path) -> Path:
    return path


def _encrypted_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.encrypted{path.suffix}")


def test_encrypted_credential_store_saves_to_encrypted_sidecar_when_keyring_available(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EncryptedCredentialStore()
    credentials_path = tmp_path / "credentials.json"
    keyring_backend = _MemoryKeyring()
    monkeypatch.setattr(auth, "keyring", keyring_backend)

    store.save(
        credentials_path,
        connect_chatgpt_credentials.ChatGPTCredentials(
            access_token=_jwt("acct_encrypted"),
            refresh_token="refresh_encrypted",
            expires_at=time.time() + 600,
            account_id="acct_encrypted",
        ),
    )

    assert not _plaintext_path(credentials_path).exists()
    assert _encrypted_path(credentials_path).exists()
    registry = connect_credentials_base.CredentialRegistry()
    registry.register(connect_chatgpt_credentials.ChatGPTCredentialProvider())
    loaded = store.load(
        credentials_path,
        provider="chatgpt",
        registry=registry,
    )

    assert loaded.access_token


def test_encrypted_credential_store_upgrades_plaintext_when_keyring_becomes_available(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EncryptedCredentialStore()
    credentials_path = tmp_path / "credentials.json"
    plaintext_store = connect_credentials_base.CredentialStore()
    registry = connect_credentials_base.CredentialRegistry()
    registry.register(connect_chatgpt_credentials.ChatGPTCredentialProvider())

    plaintext_store.save(
        credentials_path,
        connect_chatgpt_credentials.ChatGPTCredentials(
            access_token=_jwt("acct_upgrade"),
            refresh_token="refresh_upgrade",
            expires_at=time.time() + 600,
            account_id="acct_upgrade",
        ),
    )
    monkeypatch.setattr(auth, "keyring", _MemoryKeyring())

    loaded = store.load(credentials_path, provider="chatgpt", registry=registry)

    assert loaded.account_id == "acct_upgrade"
    assert not _plaintext_path(credentials_path).exists()
    assert _encrypted_path(credentials_path).exists()


def test_encrypted_credential_store_warns_and_uses_plaintext_when_keyring_unavailable(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EncryptedCredentialStore()
    credentials_path = tmp_path / "credentials.json"
    registry = connect_credentials_base.CredentialRegistry()
    registry.register(connect_chatgpt_credentials.ChatGPTCredentialProvider())
    monkeypatch.setattr(auth, "keyring", None)

    store.save(
        credentials_path,
        connect_chatgpt_credentials.ChatGPTCredentials(
            access_token=_jwt("acct_plaintext"),
            refresh_token="refresh_plaintext",
            expires_at=time.time() + 600,
            account_id="acct_plaintext",
        ),
    )
    loaded = store.load(credentials_path, provider="chatgpt", registry=registry)

    assert loaded.account_id == "acct_plaintext"
    assert credentials_path.exists()
    assert "falling back to plaintext credential config" in caplog.text


def test_encrypted_credential_store_warns_and_prefers_encrypted_when_both_files_exist(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "credentials.json"
    keyring_backend = _MemoryKeyring()
    monkeypatch.setattr(auth, "keyring", keyring_backend)
    encrypted_store = EncryptedCredentialStore()
    registry = connect_credentials_base.CredentialRegistry()
    registry.register(connect_chatgpt_credentials.ChatGPTCredentialProvider())

    encrypted_store.save(
        credentials_path,
        connect_chatgpt_credentials.ChatGPTCredentials(
            access_token=_jwt("acct_encrypted_preferred"),
            refresh_token="refresh_encrypted_preferred",
            expires_at=time.time() + 600,
            account_id="acct_encrypted_preferred",
        ),
    )
    connect_credentials_base.CredentialStore().save(
        credentials_path,
        connect_chatgpt_credentials.ChatGPTCredentials(
            access_token=_jwt("acct_plaintext_ignored"),
            refresh_token="refresh_plaintext_ignored",
            expires_at=time.time() + 600,
            account_id="acct_plaintext_ignored",
        ),
    )

    loaded = encrypted_store.load(
        credentials_path, provider="chatgpt", registry=registry
    )

    assert loaded.account_id == "acct_encrypted_preferred"
    assert "Both encrypted and plaintext credential files exist" in caplog.text
