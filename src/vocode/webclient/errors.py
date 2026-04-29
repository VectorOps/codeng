from __future__ import annotations

from typing import Any, Dict, Optional


class WebClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.payload = payload


class WebClientValidationError(WebClientError):
    pass


class WebClientAccessError(WebClientError):
    pass


class WebClientFetchError(WebClientError):
    pass


class WebClientContentError(WebClientError):
    pass
