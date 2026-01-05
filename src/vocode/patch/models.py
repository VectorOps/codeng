from enum import Enum


class FileApplyStatus(Enum):
    Create = "Create"
    Update = "Update"
    PartialUpdate = "PartialUpdate"
    Delete = "Delete"
