from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class SCMBase(ABC):
    """
    Abstract base class for Source Control Management integrations.

    Implementations should be able to detect whether a given path is inside a
    repository and, if so, return the repository root directory path.
    """

    @abstractmethod
    def find_repo(self, path: Path) -> Optional[Path]:
        """
        Given a filesystem path (file or directory), return the repository
        root directory if the path is inside a repository; otherwise None.
        """
        raise NotImplementedError
