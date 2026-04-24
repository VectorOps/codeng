from __future__ import annotations

from typing import Any, Dict, Optional

from vocode.webclient import errors
from vocode.webclient import models
from vocode.webclient import pipeline
from vocode.webclient.backends import base as backend_base


def merge_settings_layers(
    *settings_layers: Optional[models.WebClientSettings],
    policy: Optional[models.HarnessWebClientPolicy] = None,
) -> models.WebClientSettings:
    merged_data: Dict[str, Any] = {}
    for settings in settings_layers:
        if settings is None:
            continue
        merged_data.update(settings.model_dump(mode="python", exclude_none=True))

    merged_settings = models.WebClientSettings(**merged_data)
    return build_effective_settings(merged_settings, policy=policy)


def _merge_string_lists(
    base_values: list[str], override_values: list[str]
) -> list[str]:
    merged: list[str] = []
    for item in base_values + override_values:
        value = item.strip()
        if not value:
            continue
        if value not in merged:
            merged.append(value)
    return merged


def build_effective_settings(
    settings: Optional[models.WebClientSettings] = None,
    *,
    policy: Optional[models.HarnessWebClientPolicy] = None,
) -> models.WebClientSettings:
    effective = (
        settings.model_copy(deep=True)
        if settings is not None
        else models.WebClientSettings()
    )
    default_policy = policy or HarnessManagedWebClientPolicy.default_policy()
    effective.url_blocklist = _merge_string_lists(
        default_policy.default_url_blocklist,
        effective.url_blocklist,
    )
    return effective


def build_request(
    *,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout_s: Optional[float] = None,
) -> models.WebClientRequest:
    return models.WebClientRequest(
        url=url,
        headers=headers or {},
        timeout_s=timeout_s,
    )


class HarnessManagedWebClientPolicy:
    @classmethod
    def default_policy(cls) -> models.HarnessWebClientPolicy:
        return models.HarnessWebClientPolicy(default_url_blocklist=[])


class WebClientService:
    def __init__(
        self,
        *,
        settings: Optional[models.WebClientSettings] = None,
        policy: Optional[models.HarnessWebClientPolicy] = None,
    ) -> None:
        self._settings = merge_settings_layers(settings, policy=policy)
        self._policy = policy or HarnessManagedWebClientPolicy.default_policy()

    @property
    def settings(self) -> models.WebClientSettings:
        return self._settings

    @property
    def policy(self) -> models.HarnessWebClientPolicy:
        return self._policy

    def resolve_backend(self):
        backend_cls = backend_base.get_backend(self._settings.backend)
        if backend_cls is None:
            raise errors.WebClientValidationError("unknown webclient backend")
        return backend_cls()

    async def fetch(
        self,
        request: models.WebClientRequest,
    ) -> models.WebClientResult:
        backend = self.resolve_backend()
        raw = await backend.fetch(request, self._settings)
        return pipeline.process_raw_content(raw, self._settings)

    async def fetch_url(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout_s: Optional[float] = None,
    ) -> models.WebClientResult:
        request = build_request(
            url=url,
            headers=headers,
            timeout_s=timeout_s,
        )
        effective_settings = self._settings
        if timeout_s is not None:
            effective_settings = self._settings.model_copy(deep=True)
            effective_settings.timeout_s = timeout_s
            if (
                effective_settings.connect_timeout_s is not None
                and effective_settings.connect_timeout_s > timeout_s
            ):
                effective_settings.connect_timeout_s = timeout_s
            if (
                effective_settings.read_timeout_s is not None
                and effective_settings.read_timeout_s > timeout_s
            ):
                effective_settings.read_timeout_s = timeout_s
        backend = self.resolve_backend()
        raw = await backend.fetch(request, effective_settings)
        return pipeline.process_raw_content(raw, effective_settings)


async def fetch_url(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout_s: Optional[float] = None,
    settings: Optional[models.WebClientSettings] = None,
    policy: Optional[models.HarnessWebClientPolicy] = None,
) -> models.WebClientResult:
    service = WebClientService(settings=settings, policy=policy)
    return await service.fetch_url(
        url,
        headers=headers,
        timeout_s=timeout_s,
    )
