from typing import Any, Callable, Dict
from typing import ClassVar, List, Optional
from typing import Sequence
from dataclasses import dataclass
from vocode.models import PreprocessorSpec
from vocode.state import Message

# Callback signature: accepts (project, spec, text)
PreprocessorFunc = Callable[[Any, PreprocessorSpec, List[Message]], List[Message]]


@dataclass(frozen=True)
class Preprocessor:
    name: str
    description: str
    func: PreprocessorFunc


_registry: Dict[str, Preprocessor] = {}


class PreprocessorFactory:
    _registry: ClassVar[Dict[str, Preprocessor]] = _registry

    @classmethod
    def register(
        cls,
        name: str,
        func: PreprocessorFunc | None = None,
        description: str = "",
    ):
        def _do_register(inner: PreprocessorFunc) -> PreprocessorFunc:
            if not isinstance(name, str) or not name:
                raise ValueError("Preprocessor name must be a non-empty string")
            if name in cls._registry:
                raise ValueError(
                    f"Preprocessor with name '{name}' already registered."
                )
            cls._registry[name] = Preprocessor(
                name=name,
                description=description,
                func=inner,
            )
            return inner

        if func is None:
            return _do_register
        return _do_register(func)

    @classmethod
    def unregister(cls, name: str) -> bool:
        return cls._registry.pop(name, None) is not None

    @classmethod
    def get(cls, name: str) -> Optional[Preprocessor]:
        return cls._registry.get(name)

    @classmethod
    def all(cls) -> Dict[str, Preprocessor]:
        return dict(cls._registry)


def apply_preprocessors(
    preprocessors: Sequence[PreprocessorSpec], project: Any, messages: List[Message]
) -> List[Message]:
    """
    Apply a sequence of preprocessors to a list of messages.
    """
    current_messages = list(messages)
    for spec in preprocessors:
        if preprocessor := PreprocessorFactory.get(spec.name):
            current_messages = preprocessor.func(project, spec, current_messages)
    return current_messages
