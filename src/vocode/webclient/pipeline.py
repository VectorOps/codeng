from __future__ import annotations

from typing import Dict, Optional

from vocode.webclient import content
from vocode.webclient import errors
from vocode.webclient import models


def _decode_raw_text(raw: models.WebClientRawContent) -> str:
    if raw.text is not None:
        return raw.text
    if raw.bytes_body is None:
        raise errors.WebClientContentError("response body is empty")

    encoding = raw.encoding or "utf-8"
    try:
        return raw.bytes_body.decode(encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise errors.WebClientContentError("failed to decode response body") from exc
    except LookupError as exc:
        raise errors.WebClientContentError("unsupported response encoding") from exc


def _filter_headers(
    headers: Dict[str, str],
    settings: Optional[models.WebClientSettings],
) -> Dict[str, str]:
    if settings is None or not settings.return_headers:
        return {}
    return dict(headers)


def _validate_allowed_content_types(
    content_type: Optional[str],
    settings: Optional[models.WebClientSettings],
) -> None:
    if settings is None or not settings.allowed_content_types:
        return
    normalized = content_type.split(";", 1)[0].strip().lower() if content_type else ""
    if normalized in settings.allowed_content_types:
        return
    raise errors.WebClientContentError("content type is not allowed")


def process_raw_content(
    raw: models.WebClientRawContent,
    settings: Optional[models.WebClientSettings] = None,
) -> models.WebClientResult:
    _validate_allowed_content_types(raw.content_type, settings)
    content_kind = content.ensure_supported_content(raw.content_type, raw.final_url)
    decoded_text = _decode_raw_text(raw)

    if content_kind == models.WebContentKind.html_as_markdown:
        final_text = content.html_to_markdown(decoded_text)
    else:
        final_text = content.normalize_text_output(decoded_text)

    if not final_text:
        raise errors.WebClientContentError("response body is empty")

    metadata = dict(raw.metadata)
    headers = _filter_headers(raw.headers, settings)
    if headers:
        metadata["headers"] = headers

    return models.WebClientResult(
        url=raw.url,
        final_url=raw.final_url,
        status_code=raw.status_code,
        content_type=raw.content_type,
        encoding=raw.encoding,
        title=raw.title,
        content_kind=content_kind,
        text=final_text,
        metadata=metadata,
    )
