from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
from typing import List, Optional
from urllib import parse

from vocode.webclient import errors
from vocode.webclient import models

_MARKDOWN_CONTENT_TYPES = {
    "text/markdown",
    "text/x-markdown",
    "application/markdown",
}

_TEXTUAL_CONTENT_TYPES = {
    "text/plain",
    "application/json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "text/yaml",
    "text/x-yaml",
    "text/xml",
    "text/csv",
}

_HTML_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
}

_TEXTUAL_EXTENSIONS = {
    ".txt",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".csv",
    ".log",
}

_MARKDOWN_EXTENSIONS = {
    ".md",
    ".markdown",
    ".mdx",
}

_HTML_EXTENSIONS = {
    ".html",
    ".htm",
    ".xhtml",
}

_BINARY_CONTENT_TYPE_PREFIXES = (
    "image/",
    "audio/",
    "video/",
    "font/",
)

_BINARY_CONTENT_TYPES = {
    "application/pdf",
    "application/zip",
    "application/gzip",
    "application/x-gzip",
    "application/octet-stream",
    "application/vnd",
}


def _strip_content_type_params(content_type: Optional[str]) -> Optional[str]:
    if content_type is None:
        return None
    normalized = content_type.strip().lower()
    if not normalized:
        return None
    return normalized.split(";", 1)[0].strip() or None


def _get_url_extension(url: str) -> str:
    parsed = parse.urlparse(url)
    path = parsed.path.lower()
    if "." not in path:
        return ""
    return "." + path.rsplit(".", 1)[1]


def is_llm_digestible_content_type(content_type: Optional[str]) -> bool:
    normalized = _strip_content_type_params(content_type)
    if normalized is None:
        return True
    if normalized.startswith("text/"):
        return True
    if normalized in _MARKDOWN_CONTENT_TYPES:
        return True
    if normalized in _TEXTUAL_CONTENT_TYPES:
        return True
    if normalized in _HTML_CONTENT_TYPES:
        return True
    return False


def classify_content_type(
    content_type: Optional[str],
    url: str,
) -> models.WebContentKind:
    normalized = _strip_content_type_params(content_type)
    if normalized in _MARKDOWN_CONTENT_TYPES:
        return models.WebContentKind.markdown
    if normalized in _HTML_CONTENT_TYPES:
        return models.WebContentKind.html_as_markdown
    if normalized in _TEXTUAL_CONTENT_TYPES:
        return models.WebContentKind.text
    if normalized is not None and normalized.startswith("text/"):
        return models.WebContentKind.text
    if normalized is not None:
        for prefix in _BINARY_CONTENT_TYPE_PREFIXES:
            if normalized.startswith(prefix):
                return models.WebContentKind.unsupported
        for item in _BINARY_CONTENT_TYPES:
            if normalized == item or normalized.startswith(item + "."):
                return models.WebContentKind.unsupported

    extension = _get_url_extension(url)
    if extension in _MARKDOWN_EXTENSIONS:
        return models.WebContentKind.markdown
    if extension in _HTML_EXTENSIONS:
        return models.WebContentKind.html_as_markdown
    if extension in _TEXTUAL_EXTENSIONS:
        return models.WebContentKind.text
    if normalized is None:
        return models.WebContentKind.text
    return models.WebContentKind.unsupported


class _HTMLToMarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._parts: List[str] = []
        self._href_stack: List[Optional[str]] = []
        self._list_stack: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.lower()
        if normalized in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return

        attrs_map = dict(attrs)
        if normalized in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(normalized[1])
            self._parts.append("\n" + ("#" * level) + " ")
        elif normalized in {"p", "div", "section", "article", "header", "footer"}:
            self._parts.append("\n\n")
        elif normalized == "br":
            self._parts.append("\n")
        elif normalized in {"ul", "ol"}:
            self._list_stack.append(normalized)
            self._parts.append("\n")
        elif normalized == "li":
            indent = "  " * max(len(self._list_stack) - 1, 0)
            bullet = (
                "- " if not self._list_stack or self._list_stack[-1] == "ul" else "1. "
            )
            self._parts.append("\n" + indent + bullet)
        elif normalized == "a":
            self._href_stack.append(attrs_map.get("href"))
            self._parts.append("[")
        elif normalized in {"strong", "b"}:
            self._parts.append("**")
        elif normalized in {"em", "i"}:
            self._parts.append("*")
        elif normalized == "code":
            self._parts.append("`")
        elif normalized == "pre":
            self._parts.append("\n\n```")
        elif normalized == "blockquote":
            self._parts.append("\n\n> ")
        elif normalized in {"th", "td"}:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"script", "style", "noscript"}:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return

        if normalized == "a":
            href = self._href_stack.pop() if self._href_stack else None
            if href:
                self._parts.append("](" + href.strip() + ")")
            else:
                self._parts.append("]")
        elif normalized in {"strong", "b"}:
            self._parts.append("**")
        elif normalized in {"em", "i"}:
            self._parts.append("*")
        elif normalized == "code":
            self._parts.append("`")
        elif normalized == "pre":
            self._parts.append("```\n")
        elif normalized in {"p", "div", "section", "article", "header", "footer"}:
            self._parts.append("\n\n")
        elif normalized in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            self._parts.append("\n")
        elif normalized in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._parts.append("\n\n")
        elif normalized == "tr":
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = unescape(data)
        if not text:
            return
        self._parts.append(text)

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth > 0:
            return
        self._parts.append(unescape("&" + name + ";"))

    def handle_charref(self, name: str) -> None:
        if self._skip_depth > 0:
            return
        self._parts.append(unescape("&#" + name + ";"))

    def to_markdown(self) -> str:
        return "".join(self._parts)


def html_to_markdown(html: str) -> str:
    parser = _HTMLToMarkdownParser()
    parser.feed(html)
    parser.close()
    return normalize_text_output(parser.to_markdown())


def normalize_text_output(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    normalized_lines: List[str] = []
    empty_count = 0
    for line in lines:
        compact = (
            " ".join(line.split())
            if line.strip() and not line.lstrip().startswith("```")
            else line.strip()
        )
        if compact:
            normalized_lines.append(compact)
            empty_count = 0
            continue
        empty_count += 1
        if empty_count <= 2:
            normalized_lines.append("")
    return "\n".join(normalized_lines).strip()


def ensure_supported_content(
    content_type: Optional[str], url: str
) -> models.WebContentKind:
    content_kind = classify_content_type(content_type, url)
    if content_kind == models.WebContentKind.unsupported:
        raise errors.WebClientContentError("unsupported content type")
    return content_kind
