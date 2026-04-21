from __future__ import annotations

import base64
import json
import time

import pytest

from connect.credentials import chatgpt as connect_chatgpt_credentials

from vocode.connect_auth import ProjectCredentialManager


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
