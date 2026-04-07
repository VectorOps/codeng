from .base import CommandManager, CommandError, command, option
from . import auth as _auth_commands  # noqa: F401
from . import debug as _debug_commands  # noqa: F401
from . import help as _help_commands  # noqa: F401
from . import repos as _repo_commands  # noqa: F401
from . import vars as _vars_commands  # noqa: F401
