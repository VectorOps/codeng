from __future__ import annotations

import asyncio
import json

from vocode.tui import history as tui_history


def test_navigate_previous_returns_none_at_oldest_entry() -> None:
    manager = tui_history.HistoryManager()
    manager.add("one")
    manager.add("two")

    first = manager.navigate_previous("current")
    assert first == "two"

    second = manager.navigate_previous("ignored")
    assert second == "one"

    third = manager.navigate_previous("ignored-again")
    assert third is None


def test_navigate_next_walks_forward_then_returns_none_at_buffer() -> None:
    manager = tui_history.HistoryManager()
    manager.add("one")
    manager.add("two")

    back = manager.navigate_previous("buffer")
    assert back == "two"

    older = manager.navigate_previous("ignored")
    assert older == "one"

    n1 = manager.navigate_next()
    assert n1 == "two"

    n2 = manager.navigate_next()
    assert n2 == "buffer"

    n3 = manager.navigate_next()
    assert n3 is None


def test_history_preserves_edits_for_current_item_across_navigation() -> None:
    manager = tui_history.HistoryManager()
    manager.add("one")
    manager.add("two")

    back = manager.navigate_previous("buffer")
    assert back == "two"

    manager.update_current("two edited")

    older = manager.navigate_previous("ignored")
    assert older == "one"

    forward = manager.navigate_next()
    assert forward == "two edited"


def test_history_preserves_edits_after_returning_to_buffer() -> None:
    manager = tui_history.HistoryManager()
    manager.add("one")
    manager.add("two")

    back = manager.navigate_previous("buffer")
    assert back == "two"

    manager.update_current("two edited")

    to_buffer = manager.navigate_next()
    assert to_buffer == "buffer"

    again = manager.navigate_previous("buffer")
    assert again == "two edited"


def test_history_loads_from_disk_and_trims_to_limit(tmp_path) -> None:
    path = tmp_path / "history.json"
    path.write_text(json.dumps(["one", "two", "three"]), encoding="utf-8")

    manager = tui_history.HistoryManager(max_entries=2, history_path=path)

    assert manager.entries == ("two", "three")


def test_history_save_persists_entries(tmp_path) -> None:
    path = tmp_path / "history.json"
    manager = tui_history.HistoryManager(history_path=path)
    manager.add("one")
    manager.add("two")

    manager.save()

    assert json.loads(path.read_text(encoding="utf-8")) == ["one", "two"]


def test_history_stop_flushes_dirty_entries(tmp_path) -> None:
    path = tmp_path / "history.json"
    manager = tui_history.HistoryManager(history_path=path)
    manager.add("one")

    asyncio.run(manager.stop())

    assert json.loads(path.read_text(encoding="utf-8")) == ["one"]
