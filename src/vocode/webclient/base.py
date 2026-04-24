from __future__ import annotations

from abc import ABC, abstractmethod

from . import models


class BaseWebClientBackend(ABC):
    name: str

    @abstractmethod
    async def fetch(
        self,
        request: models.WebClientRequest,
        settings: models.WebClientSettings,
    ) -> models.WebClientRawContent: ...
