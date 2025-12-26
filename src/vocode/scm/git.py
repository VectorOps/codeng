from __future__ import annotations

from pathlib import Path
from typing import Optional

from git import Repo, InvalidGitRepositoryError, NoSuchPathError, GitError

from .base import SCMBase


class GitSCM(SCMBase):
    """
    Git SCM implementation using GitPython.
    """

    def find_repo(self, path: Path) -> Optional[Path]:
        # Use absolute() but do not resolve symlinks.
        p = path if path.is_dir() else path.parent
        p = p.absolute()
        try:
            repo = Repo(p, search_parent_directories=True)
            wt = repo.working_tree_dir
            return Path(wt) if wt else None
        except (InvalidGitRepositoryError, NoSuchPathError, GitError):
            return None
