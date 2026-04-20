from __future__ import annotations

from typing import Dict, Optional

from vocode import settings as vocode_settings
from vocode.mcp import models as mcp_models


class MCPRegistry:
    def __init__(self, settings: Optional[vocode_settings.MCPSettings]) -> None:
        self._settings = settings
        self._sources: Dict[str, mcp_models.MCPSourceDescriptor] = {}
        if settings is not None and settings.enabled:
            self._sources = self._build_sources(settings)

    def list_sources(self) -> Dict[str, mcp_models.MCPSourceDescriptor]:
        return dict(self._sources)

    def get_source(self, name: str) -> Optional[mcp_models.MCPSourceDescriptor]:
        return self._sources.get(name)

    def _build_sources(
        self, settings: vocode_settings.MCPSettings
    ) -> Dict[str, mcp_models.MCPSourceDescriptor]:
        protocol = settings.protocol or vocode_settings.MCPProtocolSettings()
        out: Dict[str, mcp_models.MCPSourceDescriptor] = {}
        for name, source in settings.sources.items():
            roots = []
            if source.roots is not None:
                for item in source.roots.entries:
                    if item.uri is None:
                        continue
                    roots.append(
                        mcp_models.MCPRootDescriptor(uri=item.uri, name=item.name)
                    )
            transport = mcp_models.MCPTransportKind.stdio
            if isinstance(source, vocode_settings.MCPExternalSourceSettings):
                transport = mcp_models.MCPTransportKind.http
            out[name] = mcp_models.MCPSourceDescriptor(
                source_name=name,
                transport=transport,
                scope=source.scope.value,
                startup_timeout_s=protocol.startup_timeout_s,
                shutdown_timeout_s=protocol.shutdown_timeout_s,
                request_timeout_s=protocol.request_timeout_s,
                max_request_timeout_s=protocol.max_request_timeout_s,
                roots=roots,
            )
        return out
