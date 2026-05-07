from vocode.webclient import WebContentKind
from vocode.webclient import classify_content_type
from vocode.webclient import ensure_supported_content
from vocode.webclient import html_to_markdown
from vocode.webclient import normalize_text_output
from vocode.webclient.errors import WebClientContentError


def test_classify_plain_text_as_text() -> None:
    assert (
        classify_content_type("text/plain", "https://example.com/file.txt")
        == WebContentKind.text
    )


def test_classify_markdown_as_markdown() -> None:
    assert (
        classify_content_type("text/markdown", "https://example.com/readme.md")
        == WebContentKind.markdown
    )


def test_convert_html_to_markdown() -> None:
    output = html_to_markdown(
        "<h1>Hello</h1><p>world <a href='https://example.com'>link</a></p>"
    )
    assert "# Hello" in output
    assert "https://example.com" in output


def test_preserve_json_like_content_as_text() -> None:
    assert (
        classify_content_type("application/json", "https://example.com/data.json")
        == WebContentKind.text
    )


def test_reject_unsupported_binary_content() -> None:
    try:
        ensure_supported_content("image/png", "https://example.com/image.png")
    except WebClientContentError:
        return
    raise AssertionError("expected WebClientContentError")


def test_normalize_text_output_collapses_excess_blank_lines() -> None:
    assert normalize_text_output("a\n\n\n\n\nb") == "a\n\n\nb"
