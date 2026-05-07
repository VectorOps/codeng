from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List
from typing import Literal, Optional
from urllib import parse

from pydantic import BaseModel, Field
from pydantic import field_validator, model_validator

from vocode import vars as vars_mod


class WebContentKind(str, Enum):
    text = "text"
    markdown = "markdown"
    html_as_markdown = "html_as_markdown"
    unsupported = "unsupported"


class WebClientRequest(BaseModel):
    url: str
    method: Literal["GET"] = "GET"
    headers: Dict[str, str] = Field(default_factory=dict)
    timeout_s: Optional[float] = Field(default=None, gt=0)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        normalized = value.strip()
        parsed = parse.urlparse(normalized)
        if not normalized:
            raise ValueError("url must not be empty")
        if not parsed.scheme:
            raise ValueError("url must include a scheme")
        if not parsed.netloc:
            raise ValueError("url must include a host")
        return normalized

    @field_validator("headers", mode="before")
    @classmethod
    def _coerce_headers(cls, value: Any) -> Any:
        if value is None:
            return {}
        return value

    @field_validator("headers")
    @classmethod
    def _validate_headers(cls, value: Dict[str, str]) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        for key, item in value.items():
            name = key.strip()
            if not name:
                raise ValueError("header names must not be empty")
            normalized[name] = item.strip()
        return normalized


class WebClientRawContent(BaseModel):
    url: str
    final_url: str
    status_code: int = Field(ge=100, le=599)
    content_type: Optional[str] = None
    encoding: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    text: Optional[str] = None
    bytes_body: Optional[bytes] = None
    title: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("content_type")
    @classmethod
    def _normalize_content_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None

    @field_validator("encoding")
    @classmethod
    def _normalize_encoding(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_body(self) -> "WebClientRawContent":
        if self.text is None and self.bytes_body is None:
            raise ValueError("raw content must include text or bytes_body")
        return self


class WebClientResult(BaseModel):
    url: str
    final_url: str
    status_code: int = Field(ge=100, le=599)
    content_type: Optional[str] = None
    encoding: Optional[str] = None
    title: Optional[str] = None
    content_kind: WebContentKind
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        if not value:
            raise ValueError("text must not be empty")
        return value


class WebClientSettings(vars_mod.BaseVarModel):
    backend: str = "http"
    timeout_s: float = Field(default=20.0, gt=0)
    connect_timeout_s: Optional[float] = Field(default=10.0, gt=0)
    read_timeout_s: Optional[float] = Field(default=20.0, gt=0)
    max_redirects: int = Field(default=5, ge=0)
    follow_redirects: bool = True
    max_content_bytes: int = Field(default=2_000_000, gt=0)
    user_agent: str = "vocode-webclient/1"
    allowed_schemes: List[str] = Field(default_factory=lambda: ["http", "https"])
    url_blocklist: List[str] = Field(default_factory=list)
    allowed_content_types: List[str] = Field(default_factory=list)
    return_headers: bool = False

    @field_validator("backend")
    @classmethod
    def _normalize_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("backend must not be empty")
        return normalized

    @field_validator("user_agent")
    @classmethod
    def _validate_user_agent(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("user_agent must not be empty")
        return normalized

    @field_validator("allowed_schemes", mode="before")
    @classmethod
    def _coerce_allowed_schemes(cls, value: Any) -> Any:
        if value is None:
            return ["http", "https"]
        return value

    @field_validator("allowed_schemes")
    @classmethod
    def _normalize_allowed_schemes(cls, value: List[str]) -> List[str]:
        normalized: List[str] = []
        for item in value:
            scheme = item.strip().lower()
            if not scheme:
                continue
            if scheme not in normalized:
                normalized.append(scheme)
        if not normalized:
            raise ValueError("allowed_schemes must not be empty")
        return normalized

    @field_validator("url_blocklist", "allowed_content_types", mode="before")
    @classmethod
    def _coerce_string_list(cls, value: Any) -> Any:
        if value is None:
            return []
        return value

    @field_validator("url_blocklist")
    @classmethod
    def _normalize_url_blocklist(cls, value: List[str]) -> List[str]:
        normalized: List[str] = []
        for item in value:
            entry = item.strip()
            if not entry:
                continue
            if entry not in normalized:
                normalized.append(entry)
        return normalized

    @field_validator("allowed_content_types")
    @classmethod
    def _normalize_allowed_content_types(cls, value: List[str]) -> List[str]:
        normalized: List[str] = []
        for item in value:
            content_type = item.strip().lower()
            if not content_type:
                continue
            if content_type not in normalized:
                normalized.append(content_type)
        return normalized

    @model_validator(mode="after")
    def _validate_timeouts(self) -> "WebClientSettings":
        if (
            self.connect_timeout_s is not None
            and self.connect_timeout_s > self.timeout_s
        ):
            raise ValueError("connect_timeout_s must not exceed timeout_s")
        if self.read_timeout_s is not None and self.read_timeout_s > self.timeout_s:
            raise ValueError("read_timeout_s must not exceed timeout_s")
        return self


class HarnessWebClientPolicy(BaseModel):
    default_url_blocklist: List[str] = Field(default_factory=list)

    @field_validator("default_url_blocklist", mode="before")
    @classmethod
    def _coerce_default_url_blocklist(cls, value: Any) -> Any:
        if value is None:
            return []
        return value

    @field_validator("default_url_blocklist")
    @classmethod
    def _normalize_default_url_blocklist(cls, value: List[str]) -> List[str]:
        normalized: List[str] = []
        for item in value:
            entry = item.strip()
            if not entry:
                continue
            if entry not in normalized:
                normalized.append(entry)
        return normalized
