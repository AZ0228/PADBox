import csv
import io
import json
from contextlib import closing

import cv2
import pytest
from typer.testing import CliRunner

from padbox.cli import app
from padbox.export.collection import collection, csv_export
from padbox.inventory.pipeline import import_media, rematch
from padbox.inventory.service import accept, add, entries, merge, reject, set_quantity, statistics
from padbox.inventory.snapshots import apply_scan, preview_scan


def test_video_import_dedup_copies_and_export(store, fixtures):
    result = import_media(store, fixtures["video"])
    assert result["new_entries"] == 20
    assert result["observations"] > 20
    assert result["redundant_frames"] > 0
    assert result["ambiguous_entries"] == 0
    rows = entries(store)
    assert len(rows) == 20
    assert any(row["monster_id"] is not None for row in rows)
    assert all(row["high_confidence"] for row in rows if row["status"] == "confirmed")
    assert len({r["id"] for r in rows if r["proposed_id"] == 900001}) == 2
    assert collection(store)["monsters"] == []
    assert collection(store)["unresolved"] == []
    assert preview_scan(store, result["recording_id"])["active_slots"] == 20
    # Correct an identity; reimporting byte-identical media retains it.
    accept(store, rows[0]["id"], 900015)
    again = import_media(store, fixtures["video"])
    assert again["reused"]
    assert entries(store)[0]["monster_id"] == 900015
    for row in entries(store):
        accept(store, row["id"], row["proposed_id"])
    preview = preview_scan(store, result["recording_id"])
    apply_scan(store, result["recording_id"], preview["preview_token"], True)
    exported = collection(store)
    assert exported["complete"]
    assert sum(m["quantity"] for m in exported["monsters"]) == 20
    assert next(m for m in exported["monsters"] if m["monster_id"] == 900001)["quantity"] == 2
    csv_rows = list(csv.DictReader(io.StringIO(csv_export(store))))
    assert sum(int(r["quantity"]) for r in csv_rows) == 20
    assert {int(r["monster_id"]) for r in csv_rows} == {
        m["monster_id"] for m in exported["monsters"]
    }


def test_manual_corrections_survive_rematch_and_failed_resume(store, fixtures):
    import_media(store, fixtures["image"])
    row = entries(store)[0]
    accept(store, row["id"], 900015)
    rematch(store)
    with closing(store.connect()) as db, db:
        db.execute("UPDATE recording SET status='failed'")
    import_media(store, fixtures["image"])
    saved = next(e for e in entries(store) if e["id"] == row["id"])
    assert saved["status"] == "confirmed"
    assert saved["monster_id"] == 900015
    assert len(entries(store)) == 15


def test_second_recording_is_an_isolated_snapshot(store, fixtures):
    first = import_media(store, fixtures["image"])["recording_id"]
    second = import_media(store, fixtures["video"])["recording_id"]
    assert first != second
    assert not [e for e in entries(store) if e["tracking"] == "ambiguous"]
    assert preview_scan(store, first)["after_total"] == 15
    assert preview_scan(store, second)["active_slots"] == 20
    assert statistics(store)["total_owned_copies"] == 0


def test_additions_explicitly_add_new_copies(store, fixtures):
    add(store, 900001)
    result = import_media(store, fixtures["image"], additions=True, snapshot=False)
    assert result["ambiguous_entries"] == 0
    assert len(entries(store)) == 16


def test_merge_and_reject_preserve_evidence(store, fixtures):
    import_media(store, fixtures["image"], snapshot=False)
    original = entries(store)
    with pytest.raises(ValueError, match="same frame"):
        merge(store, original[0]["id"], original[2]["id"])
    target = add(store, 900001)[0]
    merge(store, original[0]["id"], target)
    reject(store, original[1]["id"])
    assert len(collection(store)["unresolved"]) == 13
    assert sum(r["quantity"] for r in collection(store)["monsters"]) == 1
    with closing(store.connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 15
        assert db.execute("SELECT COUNT(*) FROM review_event").fetchone()[0] == 3


def test_quantities_adjust_and_do_not_delete_observations(store, fixtures):
    import_media(store, fixtures["image"], snapshot=False)
    add(store, 900001, 3)
    set_quantity(store, 900001, 1)
    assert statistics(store)["total_owned_copies"] == 1
    set_quantity(store, 900001, 4)
    assert statistics(store)["total_owned_copies"] == 4
    assert len(collection(store)["unresolved"]) == 15
    with pytest.raises(ValueError):
        add(store, 123456789)


def test_uncertainty_csv_and_json_consistent(store, fixtures):
    import_media(store, fixtures["image"], snapshot=False)
    data = collection(store)
    csv_rows = list(csv.DictReader(io.StringIO(csv_export(store))))
    assert len(csv_rows) == len(data["unresolved"]) == 15
    assert all(r["monster_id"] == "" for r in csv_rows)
    assert all(r["confirmed"] == "False" for r in csv_rows)
    assert not collection(store, confirmed_only=True)["complete"]


def test_empty_transition_records_failure(store, tmp_path):
    path = tmp_path / "transition.png"
    cv2.imwrite(str(path), __import__("numpy").zeros((400, 300, 3), dtype="uint8"))
    with pytest.raises(ValueError, match="No complete grid"):
        import_media(store, path)
    with closing(store.connect()) as db:
        assert db.execute("SELECT status FROM recording").fetchone()[0] == "failed"
    assert not entries(store)


def test_cli_vertical_slice(tmp_path, fixtures, monkeypatch):
    monkeypatch.setattr("padbox.cli.logging.basicConfig", lambda **kwargs: None)
    runner = CliRunner()
    home = tmp_path / "cli-home"
    args = ["--home", str(home)]
    assert (
        runner.invoke(app, args + ["sources", "load", str(fixtures["references"])]).exit_code == 0
    )
    imported = runner.invoke(app, args + ["import", str(fixtures["image"])])
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.stdout)["new_entries"] == 15
    sid = json.loads(imported.stdout)["recording_id"]
    preview_file = tmp_path / "diff.json"
    diff = runner.invoke(app, args + ["scans", "diff", sid, "--output", str(preview_file)])
    assert diff.exit_code == 0, diff.output
    assert json.loads(diff.stdout)["after_total"] == 15
    applied = runner.invoke(
        app, args + ["scans", "apply", sid, "--preview", str(preview_file), "--full-box"]
    )
    assert applied.exit_code == 0, applied.output
    exported = runner.invoke(app, args + ["export", "--format", "json"])
    assert exported.exit_code == 0
    assert sum(m["quantity"] for m in json.loads(exported.stdout)["monsters"]) == 15
    assert runner.invoke(app, args + ["export", "--format", "invalid"]).exit_code != 0


def test_reverse_scroll_reuses_historical_slots(store, fixtures, tmp_path):
    from padbox.demo import render_grid

    images = [
        cv2.imread(str(fixtures["references"].parent / "icons" / f"{i}.png")) for i in range(1, 16)
    ]
    sequence = list(range(15)) + [3, 7, 1, 9, 5, 14, 6, 8, 12, 0]
    video = tmp_path / "reverse.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (500, 430))
    try:
        for start in [0, 5, 10, 5, 0]:
            frame = render_grid([images[i] for i in sequence[start : start + 15]])
            for _ in range(10):
                writer.write(frame)
    finally:
        writer.release()
    result = import_media(store, video)
    assert result["new_entries"] == 25
    assert result["ambiguous_entries"] == 0
    assert len(entries(store)) == 25


def test_merge_cannot_indirectly_collapse_simultaneous_copies(store, fixtures):
    import_media(store, fixtures["image"], snapshot=False)
    rows = entries(store)
    manual = add(store, 900001)[0]
    merge(store, rows[0]["id"], manual)
    with pytest.raises(ValueError, match="same frame"):
        merge(store, rows[2]["id"], manual)


def test_empty_failed_import_can_recalibrate_in_place(store, fixtures):
    from padbox.models import Layout

    # Wrong ROI gives no cells. With no inventory evidence yet, changing layout is safe.
    with pytest.raises(ValueError, match="No complete grid"):
        import_media(store, fixtures["image"], Layout(roi=(0, 0, 1, 0.1)))
    result = import_media(store, fixtures["image"])
    assert result["new_entries"] == 15
