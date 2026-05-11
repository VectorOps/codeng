from pathlib import Path

from vocode import project as project_mod
import vocode.startup_timing as startup_timing_mod
from vocode.startup_timing import StartupTimer


def test_startup_timer_mark_returns_monotonic_snapshot() -> None:
    timer = StartupTimer(enabled=False)

    first = timer.mark("first")
    second = timer.mark("second")

    assert first.label == "first"
    assert first.elapsed_s >= 0.0
    assert first.delta_s >= 0.0
    assert second.label == "second"
    assert second.elapsed_s >= first.elapsed_s
    assert second.delta_s >= 0.0


def test_init_project_logs_startup_timing_when_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    config_dir = tmp_path / ".vocode"
    config_dir.mkdir()
    (config_dir / "config-ng.yaml").write_text(
        """
logging:
  startup_timing: true
know_enabled: false
""",
        encoding="utf-8",
    )

    events: list[tuple[str, dict[str, object]]] = []

    class StubLogger:
        def info(self, event: str, **kwargs: object) -> None:
            events.append((event, kwargs))

    monkeypatch.setattr(startup_timing_mod, "logger", StubLogger())

    project_mod.init_project(tmp_path, search_ancestors=True, use_scm=False)

    assert events
    assert all(event == "project init timing" for event, _ in events)
    labels = [payload["label"] for _, payload in events]
    assert "settings.load" in labels
    assert "project.constructed" in labels
