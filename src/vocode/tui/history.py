from __future__ import annotations

import asyncio
import json
from pathlib import Path
import typing

from vocode.config import default_config_dir


HISTORY_FILE_NAME: typing.Final[str] = "tui-input-history.json"
SAVE_INTERVAL_S: typing.Final[float] = 60.0


class HistoryManager:
    def __init__(
        self,
        max_entries: int | None = None,
        history_path: Path | None = None,
    ) -> None:
        self._entries: list[str] = []
        self._max_entries = max_entries
        self._history_path = history_path or default_config_dir() / HISTORY_FILE_NAME
        self._index: int | None = None
        self._current_buffer: str | None = None
        self._search_query: str | None = None
        self._search_index: int | None = None
        self._edits: dict[int, str] = {}
        self._dirty = False
        self._save_task: asyncio.Task[None] | None = None
        self.load()

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
        self._trim_entries()
        self._dirty = True
        self.reset_navigation()

    def load(self) -> None:
        path = self._history_path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        data = json.loads(raw)
        if not isinstance(data, list):
            return
        entries: list[str] = []
        for item in data:
            if not isinstance(item, str):
                continue
            text = item.rstrip("\n")
            if not text:
                continue
            entries.append(text)
        self._entries = entries
        self._trim_entries()

    def save(self) -> None:
        path = self._history_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._entries), encoding="utf-8")
        self._dirty = False

    async def start(self) -> None:
        if self._save_task is not None and not self._save_task.done():
            return
        loop = asyncio.get_running_loop()
        self._save_task = loop.create_task(self._run_periodic_save())

    async def stop(self) -> None:
        task = self._save_task
        self._save_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._dirty:
            await asyncio.to_thread(self.save)

    async def _run_periodic_save(self) -> None:
        while True:
            try:
                await asyncio.sleep(SAVE_INTERVAL_S)
            except asyncio.CancelledError:
                return
            if not self._dirty:
                continue
            await asyncio.to_thread(self.save)

    def _trim_entries(self) -> None:
        if self._max_entries is None or len(self._entries) <= self._max_entries:
            return
        overflow = len(self._entries) - self._max_entries
        if overflow > 0:
            self._entries = self._entries[overflow:]

    def reset_navigation(self) -> None:
        self._index = None
        self._current_buffer = None
        self._edits = {}
        self._search_query = None
        self._search_index = None

    def update_current(self, value: str) -> None:
        text = value.rstrip("\n")
        if self._index is not None:
            self._edits[self._index] = text
            return
        if self._current_buffer is not None:
            self._current_buffer = text

    def navigate_previous(self, current: str) -> str | None:
        if not self._entries:
            return None
        if self._index is None:
            self._current_buffer = current.rstrip("\n")
            self._index = len(self._entries) - 1
            return self._edits.get(self._index, self._entries[self._index])
        if self._index <= 0:
            self._index = 0
            return None
        self._index -= 1
        return self._edits.get(self._index, self._entries[self._index])

    def navigate_next(self) -> str | None:
        if self._index is None:
            return None
        if self._index >= len(self._entries) - 1:
            self._index = None
            value = self._current_buffer or ""
            return value
        self._index += 1
        return self._edits.get(self._index, self._entries[self._index])

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
