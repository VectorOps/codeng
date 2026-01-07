from __future__ import annotations

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
