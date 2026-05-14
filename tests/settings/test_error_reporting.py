from pathlib import Path

import pytest

from vocode import error_reporting
from vocode.settings.loader import load_settings


def test_load_settings_wraps_yaml_errors(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("workflows:\n  bad: [\n", encoding="utf-8")

    with pytest.raises(error_reporting.ConfigLoadError) as exc_info:
        load_settings(str(path))

    error = exc_info.value
    assert error.message == "Invalid YAML syntax"
    assert error.source_path == path
    assert error.details is not None
    assert "line" in error.details


def test_load_settings_formats_validation_errors(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
internal_http:
  port: nope
""",
        encoding="utf-8",
    )

    with pytest.raises(error_reporting.ConfigLoadError) as exc_info:
        load_settings(str(path))

    error = exc_info.value
    assert error.message == "Configuration validation failed"
    assert error.details is not None
    assert "internal_http.port" in error.details
