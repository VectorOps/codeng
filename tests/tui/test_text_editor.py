from __future__ import annotations

from vocode.tui.lib.components import text_editor as components_text_editor


def test_text_editor_initial_empty() -> None:
    editor = components_text_editor.TextEditor("")
    assert editor.text == ""
    assert editor.lines == [""]
    assert editor.cursor_row == 0
    assert editor.cursor_col == 0


def test_text_editor_initial_multiline() -> None:
    editor = components_text_editor.TextEditor("one\ntwo")
    assert editor.lines == ["one", "two"]
    assert editor.cursor_row == 1
    assert editor.cursor_col == 3


def test_text_editor_text_setter_resets_cursor() -> None:
    editor = components_text_editor.TextEditor("hello")
    editor.text = "a\nbc"
    assert editor.lines == ["a", "bc"]
    assert editor.cursor_row == 1
    assert editor.cursor_col == 2


def test_move_cursor_left_and_right_single_line() -> None:
    editor = components_text_editor.TextEditor("abc")
    assert editor.cursor_col == 3
    editor.move_cursor_left()
    assert editor.cursor_col == 2
    editor.move_cursor_left()
    assert editor.cursor_col == 1
    editor.move_cursor_left()
    assert editor.cursor_col == 0
    editor.move_cursor_left()
    assert editor.cursor_col == 0
    editor.move_cursor_right()
    assert editor.cursor_col == 1
    editor.move_cursor_right()
    editor.move_cursor_right()
    assert editor.cursor_col == 3
    editor.move_cursor_right()
    assert editor.cursor_col == 3


def test_move_cursor_vertical_preserves_column() -> None:
    editor = components_text_editor.TextEditor("short\nlonger")
    editor.move_cursor_left()
    editor.move_cursor_left()
    assert editor.cursor_row == 1
    assert editor.cursor_col == 4
    editor.move_cursor_up()
    assert editor.cursor_row == 0
    assert editor.cursor_col == 4 or editor.cursor_col == len("short")
    editor.move_cursor_down()
    assert editor.cursor_row == 1


def test_insert_char_appends_and_inserts() -> None:
    editor = components_text_editor.TextEditor("")
    editor.insert_char("a")
    assert editor.text == "a"
    assert editor.cursor_col == 1
    editor.insert_char("b")
    assert editor.text == "ab"
    assert editor.cursor_col == 2
    editor._cursor_col = 1
    editor.insert_char("X")
    assert editor.text == "aXb"
    assert editor.cursor_col == 2


def test_backspace_within_line() -> None:
    editor = components_text_editor.TextEditor("abcd")
    editor._cursor_col = 2
    editor.backspace()
    assert editor.text == "acd"
    assert editor.cursor_col == 1


def test_backspace_merges_lines() -> None:
    editor = components_text_editor.TextEditor("one\ntwo")
    editor._cursor_row = 1
    editor._cursor_col = 0
    editor.backspace()
    assert editor.lines == ["onetwo"]
    assert editor.cursor_row == 0
    assert editor.cursor_col == len("one")


def test_delete_within_line_and_merge_empty_line() -> None:
    editor = components_text_editor.TextEditor("abc")
    editor._cursor_col = 1
    editor.delete()
    assert editor.text == "ac"
    assert editor.cursor_col == 1
    editor = components_text_editor.TextEditor("\nnext")
    assert editor.lines == ["", "next"]
    assert editor.cursor_row == 1
    editor._cursor_row = 0
    editor._cursor_col = 0
    editor.delete()
    assert editor.lines == ["next"]
    assert editor.cursor_row == 0


def test_break_line_splits_at_cursor() -> None:
    editor = components_text_editor.TextEditor("hello")
    editor._cursor_col = 2
    editor.break_line()
    assert editor.lines == ["he", "llo"]
    assert editor.cursor_row == 1
    assert editor.cursor_col == 0


def test_break_line_then_backspace_restores_text() -> None:
    editor = components_text_editor.TextEditor("hello")
    editor._cursor_col = 2
    editor.break_line()
    assert editor.text == "he\nllo"
    editor.backspace()
    assert editor.text == "hello"
    assert editor.cursor_row == 0
    assert editor.cursor_col == 2


def test_break_line_at_start_then_backspace() -> None:
    editor = components_text_editor.TextEditor("world")
    editor._cursor_col = 0
    editor.break_line()
    assert editor.lines == ["", "world"]
    assert editor.cursor_row == 1
    assert editor.cursor_col == 0
    editor.backspace()
    assert editor.lines == ["world"]
    assert editor.cursor_row == 0
    assert editor.cursor_col == 0


def test_move_cursor_line_start_and_end() -> None:
    editor = components_text_editor.TextEditor("hello world")
    assert editor.cursor_row == 0
    assert editor.cursor_col == len("hello world")
    editor.move_cursor_line_start()
    assert editor.cursor_row == 0
    assert editor.cursor_col == 0
    editor.move_cursor_line_end()
    assert editor.cursor_row == 0
    assert editor.cursor_col == len("hello world")


def test_move_cursor_word_left_and_right_single_line() -> None:
    editor = components_text_editor.TextEditor("hello world")
    assert editor.cursor_col == len("hello world")
    editor.move_cursor_word_left()
    assert editor.cursor_col == len("hello ")
    editor.move_cursor_word_left()
    assert editor.cursor_col == 0
    editor.move_cursor_word_left()
    assert editor.cursor_col == 0
    editor._cursor_col = 0
    editor.move_cursor_word_right()
    assert editor.cursor_col == len("hello ")
    editor.move_cursor_word_right()
    assert editor.cursor_col == len("hello world")
    editor.move_cursor_word_right()
    assert editor.cursor_col == len("hello world")


def test_move_cursor_word_across_lines() -> None:
    editor = components_text_editor.TextEditor("one\ntwo three")
    assert editor.lines == ["one", "two three"]
    editor._cursor_row = 1
    editor._cursor_col = 0
    editor.move_cursor_word_left()
    assert editor.cursor_row == 0
    assert editor.cursor_col == 0
    editor._cursor_row = 0
    editor._cursor_col = len("one")
    editor.move_cursor_word_right()
    assert editor.cursor_row == 1
    assert editor.cursor_col == len("two ")


def test_set_cursor_position_clamps_within_bounds() -> None:
    editor = components_text_editor.TextEditor("one\ntwo")
    editor.set_cursor_position(0, 1)
    assert editor.cursor_row == 0
    assert editor.cursor_col == 1
    editor.set_cursor_position(-1, -5)
    assert editor.cursor_row == 0
    assert editor.cursor_col == 0
    editor.set_cursor_position(10, 10)
    assert editor.cursor_row == 1
    assert editor.cursor_col == len("two")