from __future__ import annotations

from typing import Dict, Optional
from urllib import parse

import aiohttp

from vocode.webclient import base
from vocode.webclient import errors
from vocode.webclient import models
from vocode.webclient.backends import base as backend_base

_DEFAULT_MAX_CONTENT_BYTES = 2_000_000


def _strip_content_type_params(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _build_timeout(settings: models.WebClientSettings) -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(
        total=settings.timeout_s,
        connect=settings.connect_timeout_s,
        sock_read=settings.read_timeout_s,
    )


def _normalize_host(host: Optional[str]) -> str:
    if host is None:
        return ""
    return host.strip().lower().rstrip(".")


def _matches_block_entry(url: str, host: str, entry: str) -> bool:
    normalized_entry = entry.strip().lower()
    if not normalized_entry:
        return False
    if "://" in normalized_entry:
        return url.lower().startswith(normalized_entry)
    normalized_host = host.lower()
    if normalized_host == normalized_entry:
        return True
    return normalized_host.endswith("." + normalized_entry)


def _validate_scheme(
    parsed_url: parse.ParseResult,
    settings: models.WebClientSettings,
) -> None:
    scheme = parsed_url.scheme.strip().lower()
    if scheme not in settings.allowed_schemes:
        raise errors.WebClientValidationError("unsupported URL scheme")


def _validate_host_access(
    request_url: str,
    parsed_url: parse.ParseResult,
    settings: models.WebClientSettings,
) -> None:
    host = _normalize_host(parsed_url.hostname)
    if not host:
        raise errors.WebClientValidationError("url must include a host")
    for entry in settings.url_blocklist:
        if _matches_block_entry(request_url, host, entry):
            raise errors.WebClientAccessError("blocked destination")


def _build_headers(
    request: models.WebClientRequest,
    settings: models.WebClientSettings,
) -> Dict[str, str]:
    headers = dict(request.headers)
    if "User-Agent" not in headers and "user-agent" not in headers:
        headers["User-Agent"] = settings.user_agent
    return headers


def _find_header_value(headers: Dict[str, str], name: str) -> Optional[str]:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _validate_method(
    request: models.WebClientRequest, settings: models.WebClientSettings
) -> None:
    if request.method.value not in settings.allowed_methods:
        raise errors.WebClientValidationError("HTTP method is not allowed")


def _validate_request_body(
    request: models.WebClientRequest,
    settings: models.WebClientSettings,
) -> None:
    if request.body is None:
        return
    content_type = request.body.content_type.strip()
    if settings.require_explicit_content_type_for_body and not content_type:
        raise errors.WebClientValidationError(
            "request body content_type must not be empty"
        )
    header_content_type = _find_header_value(request.headers, "Content-Type")
    if header_content_type is not None:
        if header_content_type.strip().lower() != content_type.lower():
            raise errors.WebClientValidationError(
                "Content-Type header must match request body content_type"
            )
    if (
        settings.allowed_request_content_types
        and _strip_content_type_params(content_type)
        not in settings.allowed_request_content_types
    ):
        raise errors.WebClientValidationError(
            "request body content type is not allowed"
        )
    if isinstance(request.body, models.WebClientRequestBodyBinary):
        if not settings.allow_binary_request_bodies:
            raise errors.WebClientValidationError(
                "binary request bodies are not allowed"
            )
        body_bytes = request.body.decoded_bytes()
    else:
        body_bytes = request.body.text.encode(request.body.encoding)
    if len(body_bytes) > settings.max_request_body_bytes:
        raise errors.WebClientValidationError(
            "request body exceeds max_request_body_bytes"
        )


def _serialize_request_body(
    request: models.WebClientRequest,
) -> Optional[bytes]:
    if request.body is None:
        return None
    if isinstance(request.body, models.WebClientRequestBodyBinary):
        return request.body.decoded_bytes()
    return request.body.text.encode(request.body.encoding)


async def _read_body_with_limit(
    response: aiohttp.ClientResponse,
    max_content_bytes: int,
) -> bytes:
    limit = max_content_bytes or _DEFAULT_MAX_CONTENT_BYTES
    chunks = []
    total_size = 0
    async for chunk in response.content.iter_chunked(65536):
        total_size += len(chunk)
        if total_size > limit:
            raise errors.WebClientContentError(
                "response body exceeds max_content_bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


@backend_base.WebClientBackendFactory.register("http")
class HTTPWebClientBackend(base.BaseWebClientBackend):
    async def fetch(
        self,
        request: models.WebClientRequest,
        settings: models.WebClientSettings,
    ) -> models.WebClientRawContent:
        parsed_url = parse.urlparse(request.url)
        _validate_method(request, settings)
        _validate_scheme(parsed_url, settings)
        _validate_host_access(request.url, parsed_url, settings)
        _validate_request_body(request, settings)

        timeout = _build_timeout(settings)
        headers = _build_headers(request, settings)
        body = _serialize_request_body(request)
        if request.body is not None:
            headers["Content-Type"] = request.body.content_type

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    request.method.value,
                    request.url,
                    headers=headers,
                    data=body,
                    allow_redirects=settings.follow_redirects,
                    max_redirects=settings.max_redirects,
                ) as response:
                    final_url = str(response.url)
                    final_parsed_url = parse.urlparse(final_url)
                    _validate_scheme(final_parsed_url, settings)
                    _validate_host_access(final_url, final_parsed_url, settings)

                    body = await _read_body_with_limit(
                        response,
                        settings.max_content_bytes,
                    )

                    content_type = response.headers.get("Content-Type")
                    charset = response.charset
                    response_headers = dict(response.headers)
                    metadata: Dict[str, object] = {
                        "reason": response.reason,
                    }
                    if response.history:
                        metadata["redirect_count"] = len(response.history)

                    return models.WebClientRawContent(
                        url=request.url,
                        final_url=final_url,
                        status_code=response.status,
                        content_type=content_type,
                        encoding=charset,
                        headers=response_headers,
                        bytes_body=body,
                        metadata=metadata,
                    )
        except errors.WebClientError:
            raise
        except aiohttp.InvalidURL as exc:
            raise errors.WebClientValidationError("invalid URL") from exc
        except aiohttp.TooManyRedirects as exc:
            raise errors.WebClientFetchError("too many redirects") from exc
        except aiohttp.ClientConnectorError as exc:
            raise errors.WebClientFetchError("failed to connect") from exc
        except aiohttp.ClientResponseError as exc:
            raise errors.WebClientFetchError("request failed") from exc
        except aiohttp.ClientError as exc:
            raise errors.WebClientFetchError("HTTP request failed") from exc
        except TimeoutError as exc:
            raise errors.WebClientFetchError("request timed out") from exc
