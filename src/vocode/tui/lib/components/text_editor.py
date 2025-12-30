from __future__ import annotations

import typing


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


class TextEditor:
    def __init__(self, text: str = "") -> None:
        lines = text.splitlines() if text else []
        if not lines:
            lines = [""]
        self._lines: list[str] = lines
        last_row = len(self._lines) - 1
        last_line = self._lines[last_row]
        if last_line:
            self._cursor_row = last_row
            self._cursor_col = len(last_line)
        else:
            self._cursor_row = last_row
            self._cursor_col = 0

    @property
    def text(self) -> str:
        return "\n".join(self._lines)

    @text.setter
    def text(self, value: str) -> None:
        lines = value.splitlines() if value else []
        if not lines:
            lines = [""]
        self._lines = lines
        last_row = len(self._lines) - 1
        last_line = self._lines[last_row]
        if last_line:
            self._cursor_row = last_row
            self._cursor_col = len(last_line)
        else:
            self._cursor_row = last_row
            self._cursor_col = 0

    @property
    def lines(self) -> list[str]:
        return self._lines

    @property
    def cursor_row(self) -> int:
        return self._cursor_row

    @property
    def cursor_col(self) -> int:
        return self._cursor_col

    def move_cursor_left(self) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        if self._cursor_col > 0:
            self._cursor_col -= 1
        elif self._cursor_row > 0:
            self._cursor_row -= 1
            line = self._lines[self._cursor_row]
            self._cursor_col = max(len(line) - 1, 0)

    def move_cursor_right(self) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        line = self._lines[self._cursor_row]
        last_index = len(line)
        if self._cursor_col < last_index:
            self._cursor_col += 1
        elif self._cursor_row < len(self._lines) - 1:
            self._cursor_row += 1
            line = self._lines[self._cursor_row]
            self._cursor_col = len(line)

    def move_cursor_up(self) -> None:
        if self._cursor_row <= 0:
            return
        self._cursor_row -= 1
        line = self._lines[self._cursor_row]
        max_index = len(line)
        self._cursor_col = min(self._cursor_col, max_index)

    def move_cursor_down(self) -> None:
        if self._cursor_row >= len(self._lines) - 1:
            return
        self._cursor_row += 1
        line = self._lines[self._cursor_row]
        max_index = len(line)
        self._cursor_col = min(self._cursor_col, max_index)

    def move_cursor_line_start(self) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        self._cursor_col = 0

    def move_cursor_line_end(self) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        line = self._lines[self._cursor_row]
        self._cursor_col = len(line)

    def move_cursor_word_left(self) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        row = self._cursor_row
        col = self._cursor_col
        while True:
            line = self._lines[row]
            if col > 0:
                i = col
                while i > 0 and line[i - 1].isspace():
                    i -= 1
                while i > 0 and _is_word_char(line[i - 1]):
                    i -= 1
                if i == col and i > 0:
                    i -= 1
                self._cursor_row = row
                self._cursor_col = i
                return
            if row == 0:
                self._cursor_row = 0
                self._cursor_col = 0
                return
            row -= 1
            col = len(self._lines[row])

    def move_cursor_word_right(self) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        last_row = len(self._lines) - 1
        row = self._cursor_row
        col = self._cursor_col
        while True:
            line = self._lines[row]
            n = len(line)
            if col < n:
                i = col
                while i < n and _is_word_char(line[i]):
                    i += 1
                while i < n and line[i].isspace():
                    i += 1
                if i == col and i < n:
                    i += 1
                self._cursor_row = row
                self._cursor_col = i
                return
            if row >= last_row:
                self._cursor_row = row
                self._cursor_col = n
                return
            row += 1
            col = 0

    def insert_char(self, ch: str) -> None:
        if not ch:
            return
        line = self._lines[self._cursor_row]
        col = self._cursor_col
        self._lines[self._cursor_row] = line[:col] + ch + line[col:]
        self._cursor_col = col + len(ch)

    def backspace(self) -> None:
        if self._cursor_row == 0 and self._cursor_col == 0:
            return
        line = self._lines[self._cursor_row]
        if self._cursor_col > 0 and line:
            col = self._cursor_col
            self._lines[self._cursor_row] = line[: col - 1] + line[col:]
            self._cursor_col -= 1
        elif self._cursor_row > 0:
            prev_row = self._cursor_row - 1
            prev_line = self._lines[prev_row]
            new_line = prev_line + line
            self._lines[prev_row] = new_line
            del self._lines[self._cursor_row]
            self._cursor_row = prev_row
            self._cursor_col = len(prev_line)

    def delete(self) -> None:
        line = self._lines[self._cursor_row]
        if line and self._cursor_col < len(line):
            col = self._cursor_col
            new_line = line[:col] + line[col + 1 :]
            self._lines[self._cursor_row] = new_line
            max_index = len(new_line)
            self._cursor_col = min(self._cursor_col, max_index)
        elif not line and self._cursor_row < len(self._lines) - 1:
            next_row = self._cursor_row + 1
            next_line = self._lines[next_row]
            self._lines[self._cursor_row] = line + next_line
            del self._lines[next_row]
            self._cursor_col = min(
                self._cursor_col,
                len(self._lines[self._cursor_row]),
            )

    def break_line(self) -> None:
        line = self._lines[self._cursor_row]
        split_index = min(self._cursor_col, len(line))
        first = line[:split_index]
        second = line[split_index:]
        self._lines[self._cursor_row] = first
        insert_row = self._cursor_row + 1
        self._lines.insert(insert_row, second)
        self._cursor_row = insert_row
        self._cursor_col = 0

    def kill_to_line_end(self) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        line = self._lines[self._cursor_row]
        col = self._cursor_col
        if col < len(line):
            self._lines[self._cursor_row] = line[:col]

    def kill_to_line_start(self) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        line = self._lines[self._cursor_row]
        col = self._cursor_col
        if col > 0:
            self._lines[self._cursor_row] = line[col:]
            self._cursor_col = 0

    def kill_word_backward(self) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        old_row = self._cursor_row
        old_col = self._cursor_col
        self.move_cursor_word_left()
        new_row = self._cursor_row
        new_col = self._cursor_col
        if (new_row, new_col) == (old_row, old_col):
            return
        if new_row == old_row:
            line = self._lines[old_row]
            self._lines[old_row] = line[:new_col] + line[old_col:]
            self._cursor_col = new_col
            return
        prev_line = self._lines[new_row]
        curr_line = self._lines[old_row]
        self._lines[new_row] = prev_line[:new_col] + curr_line[old_col:]
        del self._lines[old_row]
        self._cursor_row = new_row
        self._cursor_col = new_col

    def kill_word_forward(self) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        old_row = self._cursor_row
        old_col = self._cursor_col
        self.move_cursor_word_right()
        new_row = self._cursor_row
        new_col = self._cursor_col
        if (new_row, new_col) == (old_row, old_col):
            return
        if new_row == old_row:
            line = self._lines[old_row]
            self._lines[old_row] = line[:old_col] + line[new_col:]
            self._cursor_col = old_col
            return
        line = self._lines[old_row]
        next_line = self._lines[new_row]
        self._lines[old_row] = line[:old_col] + next_line[new_col:]
        del self._lines[new_row]
        self._cursor_row = old_row
        self._cursor_col = old_col

    def _transform_word(self, transform: typing.Callable[[str], str]) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        line = self._lines[self._cursor_row]
        n = len(line)
        col = self._cursor_col
        if col > n:
            col = n
        i = col
        while i < n and not _is_word_char(line[i]):
            i += 1
        if i >= n:
            return
        j = i
        while j < n and _is_word_char(line[j]):
            j += 1
        word = line[i:j]
        if not word:
            return
        new_word = transform(word)
        self._lines[self._cursor_row] = line[:i] + new_word + line[j:]
        self._cursor_col = j

    def uppercase_word(self) -> None:
        self._transform_word(lambda s: s.upper())

    def lowercase_word(self) -> None:
        self._transform_word(lambda s: s.lower())

    def capitalize_word(self) -> None:
        def _cap(s: str) -> str:
            if not s:
                return s
            return s[0].upper() + s[1:].lower()

        self._transform_word(_cap)