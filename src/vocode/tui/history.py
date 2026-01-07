from __future__ import annotations

import typing


class HistoryManager:
    def __init__(self, max_entries: int | None = None) -> None:
        self._entries: list[str] = []
        self._max_entries = max_entries
        self._index: int | None = None
        self._current_buffer: str | None = None
        self._search_query: str | None = None
        self._search_index: int | None = None

    @property
    def entries(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def add(self, value: str) -> None:
        text = value.rstrip("\n")
        if not text:
            self.reset_navigation()
            return
        if self._entries and self._entries[-1] == text:
            self.reset_navigation()
            return
        self._entries.append(text)
        if self._max_entries is not None and len(self._entries) > self._max_entries:
            overflow = len(self._entries) - self._max_entries
            if overflow > 0:
                self._entries = self._entries[overflow:]
        self.reset_navigation()

    def reset_navigation(self) -> None:
        self._index = None
        self._current_buffer = None
        self._search_query = None
        self._search_index = None

    def navigate_previous(self, current: str) -> str | None:
        if not self._entries:
            return None
        if self._index is None:
            self._current_buffer = current
            self._index = len(self._entries) - 1
            return self._entries[self._index]
        if self._index <= 0:
            self._index = 0
            return None
        self._index -= 1
        return self._entries[self._index]

    def navigate_next(self) -> str | None:
        if self._index is None:
            return None
        if self._index >= len(self._entries) - 1:
            self._index = None
            value = self._current_buffer or ""
            self._current_buffer = None
            return value
        self._index += 1
        return self._entries[self._index]

    def reset_search(self) -> None:
        self._search_query = None
        self._search_index = None

    def search_backward(self, query: str) -> str | None:
        if not query:
            return None
        if not self._entries:
            return None
        if self._search_query != query or self._search_index is None:
            start = len(self._entries) - 1
        else:
            start = self._search_index - 1
        for index in range(start, -1, -1):
            entry = self._entries[index]
            if query in entry:
                self._search_query = query
                self._search_index = index
                return entry
        return None