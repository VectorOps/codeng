import pytest

from vocode.webclient import base as webclient_base
from vocode.webclient import models as webclient_models
from vocode.webclient import service as webclient_service
from vocode.webclient.backends import base as backend_base


class FakeBackend(webclient_base.BaseWebClientBackend):
    async def fetch(
        self,
        request: webclient_models.WebClientRequest,
        settings: webclient_models.WebClientSettings,
    ) -> webclient_models.WebClientRawContent:
        return webclient_models.WebClientRawContent(
            url=request.url,
            final_url=request.url,
            status_code=200,
            content_type="text/html",
            text="<h1>Example</h1><p>Hello <a href='https://example.com'>link</a></p>",
            metadata={"backend": settings.backend},
            title="Example",
        )


@pytest.mark.asyncio
async def test_fake_backend_uses_shared_pipeline() -> None:
    backend_name = "fake-test"
    backend_base.register_backend(backend_name, FakeBackend)
    try:
        service = webclient_service.WebClientService(
            settings=webclient_models.WebClientSettings(backend=backend_name)
        )
        result = await service.fetch_url("https://example.com/page")
    finally:
        backend_base.unregister_backend(backend_name)

    assert result.content_kind == webclient_models.WebContentKind.html_as_markdown
    assert result.title == "Example"
    assert "# Example" in result.text
    assert "https://example.com" in result.text
    assert result.metadata["backend"] == backend_name
