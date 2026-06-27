from vocode.patch import apply_patch
from vocode.patch.models import FileApplyStatus


def test_apply_patch_summary_allows_full_file_reread_after_partial_apply(tmp_path):
    (tmp_path / "f.txt").write_text(
        "pre\nOLD\npost\nmid\nx\ny\nz\n a\nu\nv\nw\n", encoding="utf-8"
    )
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

    summary, outcome_name, changes_map, statuses, errs = apply_patch(
        "v4a", patch_text, tmp_path
    )

    assert outcome_name == "fail"
    assert changes_map == {"f.txt": "updated"}
    assert statuses == {"f.txt": FileApplyStatus.PartialUpdate}
    assert len(errs) == 1
    assert (
        "you may re-read the current full file contents before regenerating the remaining patch chunks"
        in summary.lower()
    )
