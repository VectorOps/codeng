import pytest
from vocode.patch.v4a import process_patch
from vocode.patch.models import FileApplyStatus


def run_patch(
    patch_text: str,
    *,
    initial_files: dict[str, str] | None = None,
    fail_writes: set[str] | None = None,
    fail_deletes: set[str] | None = None,
):
    initial_files = dict(initial_files or {})
    fail_writes = set(fail_writes or set())
    fail_deletes = set(fail_deletes or set())

    writes: dict[str, str] = {}
    deletes: list[str] = []
    opened: list[str] = []

    def open_fn(p: str) -> str:
        opened.append(p)
        if p not in initial_files:
            raise KeyError(p)
        return initial_files[p]

    def write_fn(p: str, c: str) -> None:
        if p in fail_writes:
            raise IOError("disk full")
        writes[p] = c

    def delete_fn(p: str) -> None:
        if p in fail_deletes:
            raise PermissionError("read-only filesystem")
        deletes.append(p)

    statuses, errs = process_patch(patch_text, open_fn, write_fn, delete_fn)
    return statuses, errs, writes, deletes, opened


def test_end_to_end_valid_patch_with_noise_multiple_files():
    patch_text = """
Noise preface that should be ignored.
*** Begin Patch
*** Update File: src/foo.py
@@ class Foo
@@     def bar(self):
 ctx1
 ctx2
 ctx3
- old_line
+ new_line
 ctxA
 ctxB
 ctxC
@@
 p1
 p2
 p3
- remove_this
+ add_that
 s1
 s2
 s3
*** Add File: src/new_file.txt
+ newly added line 1
+ newly added line 2
*** Delete File: src/obsolete.txt
*** End Patch
Noise footer that must be ignored."""
    initial = {
        "src/foo.py": "ctx1\nctx2\nctx3\n old_line\nctxA\nctxB\nctxC\nmid\np1\np2\np3\n remove_this\ns1\ns2\ns3\n",
    }
    statuses, errs, writes, deletes, opened = run_patch(
        patch_text, initial_files=initial
    )
    assert errs == []
    assert opened == ["src/foo.py"]
    assert writes["src/foo.py"] == (
        "ctx1\nctx2\nctx3\n new_line\nctxA\nctxB\nctxC\nmid\np1\np2\np3\n add_that\ns1\ns2\ns3\n"
    )
    assert writes["src/new_file.txt"] == " newly added line 1\n newly added line 2"
    assert deletes == ["src/obsolete.txt"]
    assert statuses == {
        "src/foo.py": FileApplyStatus.Update,
        "src/new_file.txt": FileApplyStatus.Create,
        "src/obsolete.txt": FileApplyStatus.Delete,
    }


def test_multiple_update_blocks_are_merged():
    text = """*** Begin Patch
*** Update File: src/dup.py
@@
- a
+ b
*** Update File: src/dup.py
@@
- c
+ d
*** End Patch"""
    statuses, errors, writes, deletes, opened = run_patch(
        text, initial_files={"src/dup.py": " a\n c\n"}
    )
    assert errors == []
    assert opened == ["src/dup.py"]
    assert writes["src/dup.py"] == " b\n d\n"
    assert deletes == []
    assert statuses == {"src/dup.py": FileApplyStatus.Update}


def test_delete_and_create_is_a_replace():
    text = """*** Begin Patch
*** Delete File: src/a.txt
*** Add File: src/a.txt
+ new content
*** End Patch"""
    statuses, errors, writes, deletes, opened = run_patch(
        text, initial_files={"src/a.txt": "old content"}
    )
    assert errors == []
    assert opened == []
    assert writes == {"src/a.txt": " new content"}
    assert deletes == []
    assert statuses == {"src/a.txt": FileApplyStatus.Create}


def test_create_and_update_is_error():
    text = """*** Begin Patch
*** Add File: src/a.txt
+ new content
*** Update File: src/a.txt
@@
- a
+ b
*** End Patch"""
    statuses, errors, writes, deletes, opened = run_patch(
        text, initial_files={"src/a.txt": "a"}
    )
    assert any("Cannot mix Update and Add sections" in e.msg for e in errors)
    assert writes == {}
    assert deletes == []
    assert statuses == {}


def test_create_and_delete_is_error():
    text = """*** Begin Patch
*** Add File: src/a.txt
+ new content
*** Delete File: src/a.txt
*** End Patch"""
    statuses, errors, writes, deletes, opened = run_patch(text)
    assert any("Add must follow Delete for" in e.msg for e in errors)
    assert writes == {}
    assert deletes == []
    assert statuses == {}


def test_absolute_path_is_error():
    text = """*** Begin Patch
*** Update File: /abs/path.py
@@
- a
+ b
*** End Patch"""
    statuses, errors, writes, deletes, opened = run_patch(text)
    assert any("Path must be relative" in e.msg for e in errors)
    assert writes == {}
    assert deletes == []
    assert statuses == {}


def test_delete_section_must_not_have_content():
    text = """*** Begin Patch
*** Delete File: data.bin
- should not be here
@@ anchor
+ nor this
*** End Patch"""
    statuses, errors, writes, deletes, opened = run_patch(text)
    assert any("Delete file section" in e.msg for e in errors)
    assert statuses == {}
    assert writes == {}
    assert deletes == []


def test_missing_envelope_markers():
    statuses, errors, *_ = run_patch("*** End Patch")
    assert any("Missing *** Begin Patch" in e.msg for e in errors)
    statuses, errors, *_ = run_patch("*** Begin Patch")
    assert any("Missing *** End Patch" in e.msg for e in errors)
    multi = """x
*** Begin Patch
*** Update File: a.txt
@@
- a
+ b
*** Begin Patch
*** End Patch
*** End Patch"""
    statuses, errors, *_ = run_patch(multi, initial_files={"a.txt": " a\n"})
    assert any("Multiple *** Begin Patch" in e.msg for e in errors)


def test_interleaved_without_anchor_is_allowed_but_must_match():
    text = """*** Begin Patch
*** Update File: src/x.py
 ctx1
 ctx2
 ctx3
- a
+ b
 ctxA
 ctxB
 ctxC
- c
+ d
*** End Patch"""
    # Empty file cannot match context; should report a normal locate failure (not ambiguity).
    statuses, errors, *_ = run_patch(text, initial_files={"src/x.py": ""})
    assert any("Failed to locate change block" in e.msg for e in errors)


def test_process_patch_reads_update_only_and_ignores_add_delete():
    text = """*** Begin Patch
*** Update File: exists.txt
@@
- a
+ b
*** Add File: added.txt
+ created
*** Delete File: missing.txt
*** End Patch"""
    initial = {"exists.txt": " a\n"}
    statuses, errs, writes, deletes, opened = run_patch(text, initial_files=initial)
    assert errs == []
    assert opened == ["exists.txt"]
    assert writes["exists.txt"] == " b\n"
    assert writes["added.txt"] == " created"
    assert deletes == ["missing.txt"]
    assert statuses == {
        "exists.txt": FileApplyStatus.Update,
        "added.txt": FileApplyStatus.Create,
        "missing.txt": FileApplyStatus.Delete,
    }


def test_add_file_rejects_context_no_anchor():
    text = """*** Begin Patch
*** Add File: src/new_module.py
 # pre1
 # pre2
 # pre3
+ line1
+ line2
 # post1
 # post2
 # post3
*** End Patch"""
    statuses, errors, writes, deletes, opened = run_patch(text)
    assert any("must not contain context" in e.msg for e in errors)
    assert statuses == {}
    assert writes == {}
    assert deletes == []


def test_add_file_only_additions_ok():
    text = """*** Begin Patch
*** Add File: src/only_adds.py
+ line1
+ line2
+ line3
*** End Patch"""
    statuses, errors, writes, deletes, opened = run_patch(text)
    assert errors == []
    assert writes["src/only_adds.py"] == " line1\n line2\n line3"
    assert deletes == []
    assert statuses == {"src/only_adds.py": FileApplyStatus.Create}


def test_add_file_absolute_path_is_error_without_anchor():
    text = """*** Begin Patch
*** Add File: /abs/new.txt
+ a
*** End Patch"""
    statuses, errors, *_ = run_patch(text)
    assert any("Path must be relative" in e.msg for e in errors)
    assert statuses == {}


def test_process_patch_applies_changes_and_calls_io():
    patch_text = """*** Begin Patch
*** Update File: f.txt
 pre
- old
+ new
 post
*** Add File: new.txt
+ hello
*** Delete File: gone.txt
*** End Patch"""
    initial = {"f.txt": "pre\n old\npost\n"}
    statuses, errs, writes, deletes, opened = run_patch(
        patch_text, initial_files=initial
    )
    assert errs == []
    assert opened == ["f.txt"]
    assert writes["f.txt"] == "pre\n new\npost\n"
    assert writes["new.txt"] == " hello"
    assert deletes == ["gone.txt"]
    assert statuses == {
        "f.txt": FileApplyStatus.Update,
        "new.txt": FileApplyStatus.Create,
        "gone.txt": FileApplyStatus.Delete,
    }


def test_process_patch_write_delete_errors_appended():
    patch_text = """*** Begin Patch
*** Update File: f.txt
 pre
- old
+ new
 post
*** Add File: new.txt
+ hello
*** Delete File: gone.txt
*** End Patch"""
    initial = {"f.txt": "pre\n old\npost\n"}
    statuses, errs, writes, deletes, _ = run_patch(
        patch_text,
        initial_files=initial,
        fail_writes={"new.txt"},
        fail_deletes={"gone.txt"},
    )
    assert len(errs) == 2
    msgs = [e.msg for e in errs]
    hints = [e.hint or "" for e in errs]
    files = [e.filename for e in errs]
    assert any("Failed to apply change to file: new.txt" in m for m in msgs)
    assert any(("IOError" in h or "OSError" in h) and "disk full" in h for h in hints)
    assert "new.txt" in files
    assert any("Failed to apply change to file: gone.txt" in m for m in msgs)
    assert any("PermissionError" in h and "read-only filesystem" in h for h in hints)
    assert "gone.txt" in files
    assert writes["f.txt"] == "pre\n new\npost\n"
    assert statuses == {
        "f.txt": FileApplyStatus.Update,
        "new.txt": FileApplyStatus.Create,
        "gone.txt": FileApplyStatus.Delete,
    }


def test_process_patch_partial_apply_and_collect_errors():
    patch_text = """*** Begin Patch
*** Update File: f.txt
 pre
- OLDX
+ NEWX
 post
@@
 x
 y
 z
- a
+ b
 u
 v
 w
*** End Patch"""
    initial = {"f.txt": "pre\nOLD\npost\nmid\nx\ny\nz\n a\nu\nv\nw\n"}
    statuses, errs, writes, deletes, _ = run_patch(patch_text, initial_files=initial)
    assert len(errs) == 1
    assert "Failed to locate change block" in errs[0].msg
    assert writes["f.txt"] == "pre\nOLD\npost\nmid\nx\ny\nz\n b\nu\nv\nw\n"
    assert statuses == {"f.txt": FileApplyStatus.PartialUpdate}


def test_update_with_context_blocks_applies_multiple_chunks():
    patch_text = """*** Begin Patch
*** Update File: src/multi.py
@@ class A
 a1
 a2
 a3
- X
+ Y
 a4
 a5
 a6
@@ class B
 b1
 b2
 b3
- P
+ Q
 b4
 b5
 b6
*** End Patch"""
    initial = {
        "src/multi.py": "a1\na2\na3\n X\na4\na5\na6\nmid\nb1\nb2\nb3\n P\nb4\nb5\nb6\n"
    }
    statuses, errs, writes, deletes, _ = run_patch(patch_text, initial_files=initial)
    assert errs == []
    assert (
        writes["src/multi.py"]
        == "a1\na2\na3\n Y\na4\na5\na6\nmid\nb1\nb2\nb3\n Q\nb4\nb5\nb6\n"
    )
    assert statuses == {"src/multi.py": FileApplyStatus.Update}


def test_add_and_delete_only_changes():
    patch_text = """*** Begin Patch
*** Add File: src/new.txt
+ line1
+ line2
*** Delete File: src/old.txt
*** End Patch"""
    statuses, errs, writes, deletes, _ = run_patch(patch_text)
    assert errs == []
    assert writes["src/new.txt"] == " line1\n line2"
    assert deletes == ["src/old.txt"]
    assert statuses == {
        "src/new.txt": FileApplyStatus.Create,
        "src/old.txt": FileApplyStatus.Delete,
    }


def test_update_partial_match_reports_hint_and_no_write():
    patch_text = """*** Begin Patch
*** Update File: src/t.py
 ctx1
 ctx2
 ctx3
- old
+ new
 ctxA
 ctxB
 ctxC
*** End Patch"""
    initial = {"src/t.py": "ctx1\nctx2\nctx3\nNOT_OLD\nctxA\nctxB\nctxC\n"}
    statuses, errs, writes, deletes, _ = run_patch(patch_text, initial_files=initial)
    assert statuses == {}
    assert writes == {}
    assert len(errs) == 1
    assert "Failed to locate change block" in errs[0].msg
    # New behavior: the hint should quote the provided block, not partial match diagnostics.
    hint = errs[0].hint or ""
    assert "Change block not found. Here is the block you provided:" in hint
    assert "---" in hint
    # Quoted block should contain the exact lines from the patch
    assert " ctx1" in hint
    assert " ctx2" in hint
    assert " ctx3" in hint
    assert "- old" in hint
    assert "+ new" in hint
    assert " ctxA" in hint
    assert " ctxB" in hint
    assert " ctxC" in hint
    assert "Matched" not in hint
    assert "Possible variants" not in hint


def test_partial_apply_on_some_chunks_updates_and_reports_error():
    patch_text = """*** Begin Patch
*** Update File: src/partial.py
 p1
 p2
 p3
- OLDX
+ NEWX
 pA
 pB
 pC
@@
 q1
 q2
 q3
- R
+ S
 qA
 qB
 qC
*** End Patch"""
    initial = {
        "src/partial.py": "p1\np2\np3\nOLD\npA\npB\npC\nmid\nq1\nq2\nq3\n R\nqA\nqB\nqC\n"
    }
    statuses, errs, writes, deletes, _ = run_patch(patch_text, initial_files=initial)
    assert len(errs) == 1
    assert "Failed to locate change block" in errs[0].msg
    assert (
        writes["src/partial.py"]
        == "p1\np2\np3\nOLD\npA\npB\npC\nmid\nq1\nq2\nq3\n S\nqA\nqB\nqC\n"
    )
    assert statuses == {"src/partial.py": FileApplyStatus.PartialUpdate}


def test_parse_context_normalization_and_apply_with_extra_blank():
    patch_text = """*** Begin Patch
*** Update File: src/ctx_norm_apply.py
 header1

- old
+ new
 footer1
*** End Patch"""
    initial = {"src/ctx_norm_apply.py": "header1\n\n\n old\nfooter1\n"}
    statuses, errs, writes, deletes, _ = run_patch(patch_text, initial_files=initial)
    assert errs == []
    assert writes["src/ctx_norm_apply.py"] == "header1\n\n\n new\nfooter1\n"
    assert statuses == {"src/ctx_norm_apply.py": FileApplyStatus.Update}


def test_add_file_without_plus_lines_treated_as_content_and_written():
    text = """*** Begin Patch
*** Add File: src/raw_add.txt
 line1
 line2
 line3
*** End Patch"""
    statuses, errors, writes, deletes, _ = run_patch(text)
    assert errors == []
    assert writes["src/raw_add.txt"] == "line1\nline2\nline3"
    assert statuses == {"src/raw_add.txt": FileApplyStatus.Create}


def test_process_patch_add_file_context_only_block_writes_all_content():
    text = """*** Begin Patch
*** Add File: src/raw_and_blank.txt
 line1

 line3
*** End Patch"""
    statuses, errs, writes, deletes, _ = run_patch(text)
    assert errs == []
    assert statuses == {"src/raw_and_blank.txt": FileApplyStatus.Create}
    assert writes["src/raw_and_blank.txt"] == "line1\n\nline3"


def test_update_chunk_with_no_modifications_is_ignored_and_reports_error():
    text = """*** Begin Patch
*** Update File: src/empty.py
@@ anchor only
 ctx1
 ctx2
 ctx3
*** End Patch"""
    statuses, errs, writes, deletes, _ = run_patch(
        text, initial_files={"src/empty.py": "ctx1\nctx2\nctx3\n"}
    )
    assert writes == {}
    assert deletes == []
    assert statuses == {}
    assert len(errs) == 1
    assert "No change lines (+/-) provided for file: src/empty.py" in errs[0].msg


def test_update_with_no_mods_reports_error():
    text = """*** Begin Patch
*** Update File: src/empty.py
@@
 ctx1
 ctx2
 ctx3
*** End Patch"""
    statuses, errs, writes, deletes, _ = run_patch(
        text, initial_files={"src/empty.py": "ctx1\nctx2\nctx3\n"}
    )
    assert writes == {}
    assert deletes == []
    assert statuses == {}
    assert len(errs) == 1
    assert "No change lines (+/-) provided for file: src/empty.py" in errs[0].msg
    assert errs[0].filename == "src/empty.py"


def test_handles_missing_empty_line_in_context():
    patch_text = """*** Begin Patch
*** Update File: src/missing_blank.py
 header1
- old
+ new
 footer1
*** End Patch"""
    initial = {"src/missing_blank.py": "header1\n\n old\nfooter1\n"}
    statuses, errs, writes, deletes, _ = run_patch(patch_text, initial_files=initial)
    assert errs == []
    assert writes["src/missing_blank.py"] == "header1\n\n new\nfooter1\n"
    assert statuses == {"src/missing_blank.py": FileApplyStatus.Update}


def test_interleaved_additions_deletions_single_block_applies():
    """
    Multiple interleaved deletions and additions within a single chunk (no @@) should apply.
    """
    patch_text = """*** Begin Patch
*** Update File: src/inter.txt
 A
- B
+ C
 D
- E
+ F
 G
*** End Patch"""
    # Context lines A, D, G are literal file content without extra indentation.
    # Deleted/added lines B, E include a leading space as part of their file content.
    initial = {"src/inter.txt": "A\n B\nD\n E\nG\n"}
    statuses, errs, writes, deletes, _ = run_patch(patch_text, initial_files=initial)
    assert errs == []
    assert deletes == []
    assert writes["src/inter.txt"] == "A\n C\nD\n F\nG\n"
    assert statuses == {"src/inter.txt": FileApplyStatus.Update}


def test_out_of_order_chunks_reports_error_and_partial_update():
    patch_text = """*** Begin Patch
*** Update File: src/order.txt
 L3
- B
+ Y
 L5
@@
 L1
- A
+ X
 L3
*** End Patch"""
    # Context lines L1, L3, L5 are literal file content without extra indentation.
    # Deleted/added lines A, B include a leading space as part of their file content.
    initial = {"src/order.txt": "L1\n A\nL3\n B\nL5\n"}
    statuses, errs, writes, deletes, opened = run_patch(
        patch_text, initial_files=initial
    )
    # Should open the file once
    assert opened == ["src/order.txt"]
    # Should report out-of-order error
    assert any("Out-of-order change block" in e.msg for e in errs)
    # Only the first (later) chunk should be applied; the second (earlier) one skipped
    assert writes["src/order.txt"] == "L1\n A\nL3\n Y\nL5\n"
    # Status should be PartialUpdate due to the skipped out-of-order chunk
    assert statuses == {"src/order.txt": FileApplyStatus.PartialUpdate}


def test_update_with_move_renames_file_and_writes_new_content():
    patch_text = """*** Begin Patch
*** Update File: src/a.txt
*** Move to: src/renamed.txt
 pre
- old
+ new
 post
*** End Patch"""
    initial = {"src/a.txt": "pre\n old\npost\n"}
    statuses, errs, writes, deletes, opened = run_patch(
        patch_text, initial_files=initial
    )
    # No errors expected
    assert errs == []
    # Old path should be opened, new path written, old path deleted
    assert opened == ["src/a.txt"]
    assert "src/renamed.txt" in writes
    assert "src/a.txt" not in writes
    assert deletes == ["src/a.txt"]
    # Content updated and written to the new path, preserving newline
    assert writes["src/renamed.txt"] == "pre\n new\npost\n"
    # Status reported under the original path
    assert statuses == {"src/a.txt": FileApplyStatus.Update}


def test_interleaved_replace_and_delete_in_one_block_reproduces_bug():
    """
    Tests a patch with interleaved additions and deletions within a single
    change block (no '@@' separator). The original buggy parser would merge
    these into a single, incorrect find/replace operation, causing the match
    to fail and resulting in data loss. The corrected parser should treat
    them as sequential operations and apply the patch correctly.
    """
    patch_text = """
*** Begin Patch
*** Update File: src/vocode/ui/terminal/app.py
@@
 async def run_terminal(project: Project) -> None:
-    # Backward-compatible wrapper
-    app = TerminalApp(project)
-    await app.run()
+    # Thin wrapper: defer to TerminalApp for all terminal behavior.
+    app = TerminalApp(project)
+    await app.run()
-    try:
-        hist_dir = project.base_path / ".vocode"
-        hist_dir.mkdir(parents=True, exist_ok=True)
-        hist_path = hist_dir / "data" / "terminal_history"
-        kwargs = {
-            "history": FileHistory(str(hist_path)),
-            "multiline": multiline,
-            "completer": completer,
-            "complete_while_typing": False,
-        }
-        if editing_mode is not None:
-            kwargs["editing_mode"] = editing_mode
-        session = PromptSession(**kwargs)
-    except Exception:
-        # Fall back to in-memory history if anything goes wrong
-        kwargs = {"multiline": multiline, "completer": completer, "complete_while_typing": False}
-        if editing_mode is not None:
-            kwargs["editing_mode"] = editing_mode
-        session = PromptSession(**kwargs)
+
*** End Patch
    """

    # Reconstruct the original file content from the patch's context and deleted lines
    initial_content = """
async def run_terminal(project: Project) -> None:
    # Backward-compatible wrapper
    app = TerminalApp(project)
    await app.run()
    try:
        hist_dir = project.base_path / ".vocode"
        hist_dir.mkdir(parents=True, exist_ok=True)
        hist_path = hist_dir / "data" / "terminal_history"
        kwargs = {
            "history": FileHistory(str(hist_path)),
            "multiline": multiline,
            "completer": completer,
            "complete_while_typing": False,
        }
        if editing_mode is not None:
            kwargs["editing_mode"] = editing_mode
        session = PromptSession(**kwargs)
    except Exception:
        # Fall back to in-memory history if anything goes wrong
        kwargs = {"multiline": multiline, "completer": completer, "complete_while_typing": False}
        if editing_mode is not None:
            kwargs["editing_mode"] = editing_mode
        session = PromptSession(**kwargs)

@click.command()
@click.argument(
"""

    # This is what the file content should be after the patch is applied correctly
    expected_content = """
async def run_terminal(project: Project) -> None:
    # Thin wrapper: defer to TerminalApp for all terminal behavior.
    app = TerminalApp(project)
    await app.run()


@click.command()
@click.argument(
"""

    statuses, errs, writes, deletes, _ = run_patch(
        patch_text, initial_files={"src/vocode/ui/terminal/app.py": initial_content}
    )

    assert errs == []
    assert deletes == []
    assert writes["src/vocode/ui/terminal/app.py"] == expected_content
    assert statuses == {"src/vocode/ui/terminal/app.py": FileApplyStatus.Update}


def test_duplicate_identical_blocks_apply_to_both_occurrences():
    """
    When multiple change chunks are identical and the file contains
    two occurrences of the same context/delete sequence, the parser
    should match the first chunk to the first occurrence and the second
    chunk to the second occurrence (non-overlapping).
    """
    patch_text = """*** Begin Patch
*** Update File: src/vocode/runner/executors/llm/__init__.py
@@
 # Test
-# Foobar
+# Bazz
@@
 # Test
-# Foobar
+# Bazz
*** End Patch"""
    initial = {
        "src/vocode/runner/executors/llm/__init__.py": "# Test\n# Foobar\n# Test\n# Foobar\n"
    }
    statuses, errs, writes, deletes, opened = run_patch(
        patch_text, initial_files=initial
    )
    assert errs == []
    assert deletes == []
    assert opened == ["src/vocode/runner/executors/llm/__init__.py"]
    assert (
        writes["src/vocode/runner/executors/llm/__init__.py"]
        == "# Test\n# Bazz\n# Test\n# Bazz\n"
    )
    assert statuses == {
        "src/vocode/runner/executors/llm/__init__.py": FileApplyStatus.Update
    }


def test_triple_identical_blocks_apply_to_all_occurrences():
    """
    When three identical change chunks target the same single-line pattern
    that appears three times in a row, all three should be applied without
    reporting an out-of-order error.
    """
    patch_text = """*** Begin Patch
*** Update File: src/repeated.py
@@
-tc.status = ToolCallStatus.rejected
+tc.status = v_state.ToolCallStatus.rejected
@@
-tc.status = ToolCallStatus.rejected
+tc.status = v_state.ToolCallStatus.rejected
@@
-tc.status = ToolCallStatus.rejected
+tc.status = v_state.ToolCallStatus.rejected
*** End Patch"""
    initial = {
        "src/repeated.py": "tc.status = ToolCallStatus.rejected\n"
        "tc.status = ToolCallStatus.rejected\n"
        "tc.status = ToolCallStatus.rejected\n"
    }
    statuses, errs, writes, deletes, opened = run_patch(
        patch_text, initial_files=initial
    )
    assert errs == []
    assert deletes == []
    assert opened == ["src/repeated.py"]
    assert (
        writes["src/repeated.py"] == "tc.status = v_state.ToolCallStatus.rejected\n"
        "tc.status = v_state.ToolCallStatus.rejected\n"
        "tc.status = v_state.ToolCallStatus.rejected\n"
    )
    assert statuses == {"src/repeated.py": FileApplyStatus.Update}
