from typing import Any, Dict, List
import re


def get_value_by_dotted_key(data: Dict[str, Any], key: str) -> Any | None:
    """Resolve a dotted key path (e.g. "a.b.c") inside a nested dict.

    Returns None when any path segment is missing or encounters a non-dict.
    """

    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def regex_matches_value(pattern: str, value: Any) -> bool:
    """Return True when the given regex pattern matches the value string.

    Assumes the pattern has already been validated/compiled elsewhere.
    """

    text = str(value)
    return re.search(pattern, text) is not None


def tool_auto_approve_matches(
    rules: List["ToolAutoApproveRule"], arguments: Dict[str, Any]
) -> bool:
    """Return True if any auto-approve rule matches the provided arguments.

    A rule matches when the dotted ``key`` resolves to a non-None value in
    ``arguments`` and the compiled regular expression ``pattern`` finds a
    match in ``str(value)``.
    """

    for rule in rules:
        value = get_value_by_dotted_key(arguments, rule.key)
        if value is None:
            continue
        if regex_matches_value(rule.pattern, value):
            return True
    return False
