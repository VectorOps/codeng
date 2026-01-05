from vocode.patch.patch import process_patch
from vocode.patch.models import FileApplyStatus


def test_process_fenced_adds_file():

    writes = {}

    def write_fn(path: str, content: str) -> None:
        writes[path] = content

    def open_fn(path: str) -> str:
        raise FileNotFoundError

    def delete_fn(path: str) -> None:
        raise FileNotFoundError

    text = "\n".join(
        [
            "```text",
            "new.txt",
            "<<<<<<< SEARCH",
            "=======",
            "Hello",
            "World",
            ">>>>>>> REPLACE",
            "````",
        ]
    )

    statuses, errors = process_patch(text, open_fn, write_fn, delete_fn)

    assert errors == []
    assert statuses == {"new.txt": FileApplyStatus.Create}
    assert "new.txt" in writes
    assert writes["new.txt"].strip().splitlines() == ["Hello", "World"]


def test_update_successful_fenced_patch():
    writes = {}

    def write_fn(path: str, content: str) -> None:
        writes[path] = content

    def open_fn(path: str) -> str:
        assert path == "file.txt"
        return "pre\nold\npost\n"

    def delete_fn(path: str) -> None:
        raise AssertionError("delete_fn should not be called for update success")

    text = "\n".join(
        [
            "```text",
            "file.txt",
            "<<<<<<< SEARCH",
            "old",
            "=======",
            "new",
            ">>>>>>> REPLACE",
            "````",
        ]
    )

    statuses, errors = process_patch(text, open_fn, write_fn, delete_fn)

    assert errors == []
    assert statuses == {"file.txt": FileApplyStatus.Update}
    assert writes["file.txt"] == "pre\nnew\npost\n"


def test_update_partial_when_search_not_found():
    from vocode.patch.patch import process_patch
    from vocode.patch.models import FileApplyStatus

    writes = {}

    def write_fn(path: str, content: str) -> None:
        writes[path] = content

    def open_fn(path: str) -> str:
        return "pre\nactual\npost\n"

    def delete_fn(path: str) -> None:
        raise AssertionError("delete_fn should not be called for partial update")

    text = "\n".join(
        [
            "```text",
            "file.txt",
            "<<<<<<< SEARCH",
            "missing",
            "=======",
            "NEW",
            ">>>>>>> REPLACE",
            "````",
        ]
    )

    statuses, errors = process_patch(text, open_fn, write_fn, delete_fn)

    assert statuses == {"file.txt": FileApplyStatus.PartialUpdate}
    assert "file.txt" not in writes
    assert any(
        "Failed to locate exact SEARCH" in e.msg and e.filename == "file.txt"
        for e in errors
    )
    err = next(e for e in errors if "Failed to locate exact SEARCH" in e.msg)
    assert "Block not found" in err.hint
    assert "missing" in err.hint


def test_delete_success():
    deletions = []

    def write_fn(path: str, content: str) -> None:
        raise AssertionError("write_fn should not be called for delete")

    def open_fn(path: str) -> str:
        raise AssertionError("open_fn should not be called for delete")

    def delete_fn(path: str) -> None:
        deletions.append(path)

    text = "\n".join(
        [
            "```text",
            "dead.txt",
            "<<<<<<< SEARCH",
            "some content",
            "=======",
            "",
            ">>>>>>> REPLACE",
            "````",
        ]
    )

    statuses, errors = process_patch(text, open_fn, write_fn, delete_fn)

    assert errors == []
    assert statuses == {"dead.txt": FileApplyStatus.Delete}
    assert deletions == ["dead.txt"]


def test_duplicate_file_entry_error():
    writes: dict[str, str] = {}

    def write_fn(path: str, content: str) -> None:
        writes[path] = content

    def open_fn(path: str) -> str:
        assert path == "file.txt"
        return "pre\nold1\nmid\nold2\npost\n"

    def delete_fn(path: str) -> None:
        raise AssertionError("delete_fn should not be called")

    text = "\n".join(
        [
            # First block replaces old1 -> new1
            "```text",
            "file.txt",
            "<<<<<<< SEARCH",
            "old1",
            "=======",
            "new1",
            ">>>>>>> REPLACE",
            "````",
            # Second block replaces old2 -> new2
            "```text",
            "file.txt",
            "<<<<<<< SEARCH",
            "old2",
            "=======",
            "new2",
            ">>>>>>> REPLACE",
            "````",
        ]
    )

    statuses, errors = process_patch(text, open_fn, write_fn, delete_fn)

    assert errors == []
    assert statuses == {"file.txt": FileApplyStatus.Update}
    assert "file.txt" in writes
    assert writes["file.txt"] == "pre\nnew1\nmid\nnew2\npost\n"


def test_absolute_path_rejected():
    writes = {}
    deletions = []

    def write_fn(path: str, content: str) -> None:
        writes[path] = content

    def open_fn(path: str) -> str:
        return ""

    def delete_fn(path: str) -> None:
        deletions.append(path)

    text = "\n".join(
        [
            "```text",
            "/abs.txt",
            "<<<<<<< SEARCH",
            "",
            "=======",
            "data",
            ">>>>>>> REPLACE",
            "````",
        ]
    )

    statuses, errors = process_patch(text, open_fn, write_fn, delete_fn)

    assert statuses == {}
    assert any("Path must be relative" in e.msg for e in errors)
    assert "abs.txt" not in writes
    assert deletions == []


def test_read_error_marks_partial_update():
    writes = {}

    def write_fn(path: str, content: str) -> None:
        writes[path] = content

    def open_fn(path: str) -> str:
        raise FileNotFoundError("no such file")

    def delete_fn(path: str) -> None:
        raise AssertionError("delete_fn should not be called")

    text = "\n".join(
        [
            "```text",
            "missing.txt",
            "<<<<<<< SEARCH",
            "OLD",
            "=======",
            "NEW",
            ">>>>>>> REPLACE",
            "````",
        ]
    )

    statuses, errors = process_patch(text, open_fn, write_fn, delete_fn)

    # Read error -> early return; nothing applied
    assert statuses == {}
    assert "missing.txt" not in writes
    assert any(
        "Failed to read file" in e.msg and e.filename == "missing.txt" for e in errors
    )


def test_mixed_add_update_delete_and_partial():
    writes = {}
    deletions = []

    def write_fn(path: str, content: str) -> None:
        writes[path] = content

    def open_fn(path: str) -> str:
        if path == "upd.txt":
            return "A\nX\nB\n"
        elif path == "missing.txt":
            raise FileNotFoundError("no such file")
        else:
            raise AssertionError(f"unexpected open {path}")

    def delete_fn(path: str) -> None:
        deletions.append(path)

    text = "\n".join(
        [
            # add
            "```text",
            "new.txt",
            "<<<<<<< SEARCH",
            "",
            "=======",
            "hello",
            ">>>>>>> REPLACE",
            "````",
            # update success
            "```text",
            "upd.txt",
            "<<<<<<< SEARCH",
            "X",
            "=======",
            "Y",
            ">>>>>>> REPLACE",
            "````",
            # update missing file -> partial
            "```text",
            "missing.txt",
            "<<<<<<< SEARCH",
            "OLD",
            "=======",
            "NEW",
            ">>>>>>> REPLACE",
            "````",
            # delete
            "```text",
            "gone.txt",
            "<<<<<<< SEARCH",
            "something",
            "=======",
            "",
            ">>>>>>> REPLACE",
            "````",
        ]
    )

    statuses, errors = process_patch(text, open_fn, write_fn, delete_fn)

    # Read error on 'missing.txt' triggers early return; nothing applied
    assert statuses == {}
    assert writes == {}
    assert deletions == []
    assert any(
        e.filename == "missing.txt" and "Failed to read file" in e.msg for e in errors
    )
