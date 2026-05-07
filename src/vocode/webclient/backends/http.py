from __future__ import annotations

from typing import Dict, Optional
from urllib import parse

import aiohttp

from vocode.webclient import base
from vocode.webclient import errors
from vocode.webclient import models
from vocode.webclient.backends import base as backend_base

_DEFAULT_MAX_CONTENT_BYTES = 2_000_000


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
        if request.method != "GET":
            raise errors.WebClientValidationError("only GET requests are supported")

        parsed_url = parse.urlparse(request.url)
        _validate_scheme(parsed_url, settings)
        _validate_host_access(request.url, parsed_url, settings)

        timeout = _build_timeout(settings)
        headers = _build_headers(request, settings)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    request.url,
                    headers=headers,
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
