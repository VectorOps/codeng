from .base import register_backend
from .local import LocalSubprocessBackend

# TODO: Better registration
# Register backend under 'local'
register_backend("local", lambda: LocalSubprocessBackend())
