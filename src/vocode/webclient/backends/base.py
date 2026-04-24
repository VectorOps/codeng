from __future__ import annotations

from typing import Dict, Optional, Type
from typing import ClassVar

from vocode.webclient import base as webclient_base

_registry: Dict[str, Type[webclient_base.BaseWebClientBackend]] = {}


class WebClientBackendFactory:
    _registry: ClassVar[Dict[str, Type[webclient_base.BaseWebClientBackend]]] = (
        _registry
    )

    @classmethod
    def register(
        cls,
        name: str,
        backend_cls: Optional[Type[webclient_base.BaseWebClientBackend]] = None,
    ):
        normalized_name = name.strip().lower()
        if not normalized_name:
            raise ValueError("backend name must not be empty")

        def _register(
            concrete_cls: Type[webclient_base.BaseWebClientBackend],
        ) -> Type[webclient_base.BaseWebClientBackend]:
            cls._registry[normalized_name] = concrete_cls
            concrete_cls.name = normalized_name
            return concrete_cls

        if backend_cls is None:
            return _register
        return _register(backend_cls)

    @classmethod
    def unregister(cls, name: str) -> bool:
        normalized_name = name.strip().lower()
        if normalized_name not in cls._registry:
            return False
        del cls._registry[normalized_name]
        return True

    @classmethod
    def get(cls, name: str) -> Optional[Type[webclient_base.BaseWebClientBackend]]:
        normalized_name = name.strip().lower()
        return cls._registry.get(normalized_name)

    @classmethod
    def all(cls) -> Dict[str, Type[webclient_base.BaseWebClientBackend]]:
        return dict(cls._registry)


def register_backend(
    name: str,
    backend: Type[webclient_base.BaseWebClientBackend],
) -> None:
    WebClientBackendFactory.register(name, backend)


def unregister_backend(name: str) -> bool:
    return WebClientBackendFactory.unregister(name)


def get_backend(
    name: str,
) -> Optional[Type[webclient_base.BaseWebClientBackend]]:
    return WebClientBackendFactory.get(name)


def get_all_backends() -> Dict[str, Type[webclient_base.BaseWebClientBackend]]:
    return WebClientBackendFactory.all()
