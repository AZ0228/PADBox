"""Map coordinates, direction, durable relocalization and ambiguous repeated blocks."""

import json
from contextlib import closing
from dataclasses import replace

import cv2
import numpy as np
import pytest

from padbox.demo import render_grid
from padbox.detection.grid import Cell, detect
from padbox.inventory.box_map import BoxMap, read_map
from padbox.inventory.service import accept, entries, reject
from padbox.live.session import LiveSessions
from padbox.models import Layout


def jpeg(image):
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return encoded.tobytes()


def images(fixtures):
    return [
        cv2.imread(str(fixtures["references"].parent / "icons" / f"{i}.png")) for i in range(1, 16)
    ]


def start(store):
    live = LiveSessions(store)
    sid = live.create(Layout())["id"]
    live.control(sid, "resume")
    return live, sid


def test_world_map_forward_reverse_viewport_and_real_duplicate_slots(store, fixtures):
    art = images(fixtures)
    sequence = [0, 1, 0, 2, 3] + list(range(4, 14)) + [14, 2, 5, 8, 11] + [3, 7, 1, 9, 5]
    live, sid = start(store)
    first = live.submit(sid, 0, 0, jpeg(render_grid([art[i] for i in sequence[:15]])))
    first_ids = {s["inventory_id"] for s in first["box_map"]["slots"]}
    original_top = first["box_map"]["viewport"]["top"]
    last = None
    for seq, (start_index, direction) in enumerate(
        [(5, "down"), (10, "down"), (5, "up"), (0, "up")], 1
    ):
        last = live.submit(
            sid,
            seq,
            seq * 0.4,
            jpeg(render_grid([art[i] for i in sequence[start_index : start_index + 15]])),
        )
        assert last["box_map"]["direction"] == direction
        assert len(last["box_map"]["segments"]) == 1
        assert last["ambiguous_entries"] == 0
    assert last["box_map"]["known_slots"] == last["active_entries"] == 25
    assert last["box_map"]["position"] == "revisited"
    assert last["box_map"]["viewport"]["top"] == pytest.approx(original_top, abs=3)
    assert first_ids <= {s["inventory_id"] for s in last["box_map"]["slots"]}
    same_monster = [r for r in entries(store) if r["proposed_id"] == 900001]
    assert len(same_monster) == 2  # Identical icons stay two distinct mapped positions.
    assert len({r["id"] for r in same_monster}) == 2


def test_known_section_relocalizes_after_gap_restart_and_preserves_manual_id(store, fixtures):
    live, sid = start(store)
    original = jpeg(cv2.imread(str(fixtures["image"])))
    first = live.submit(sid, 0, 0, original)
    item = first["entries"][0]["id"]
    accept(store, item, 900015)
    moved = live.submit(
        sid, 1, 0.3, jpeg(cv2.imread(str(fixtures["image"].with_name("box-shifted.png"))))
    )
    assert moved["active_entries"] == 20
    gap = live.submit(sid, 2, 0.6, jpeg(np.zeros((430, 500, 3), np.uint8)))
    assert gap["box_map"]["viewport"] is None
    assert gap["box_map"]["known_slots"] == 20
    restarted = LiveSessions(store)
    restored = restarted.snapshot(sid)
    assert restored["box_map"] == gap["box_map"]
    restarted.control(sid, "resume")
    recovered = restarted.submit(sid, 3, 0.9, original)
    assert recovered["active_entries"] == 20
    assert recovered["ambiguous_entries"] == 0
    assert recovered["box_map"]["position"] == "relocalized"
    assert recovered["box_map"]["direction"] == "up"
    assert next(r for r in recovered["entries"] if r["id"] == item)["monster_id"] == 900015
    assert "map relocalization" in recovered["result"]["message"]


def test_unseen_gap_is_separate_section_and_return_to_known_section_is_safe(store, fixtures):
    live, sid = start(store)
    art = images(fixtures)
    first_frame = jpeg(render_grid(art))
    first = live.submit(sid, 0, 0, first_frame)
    live.submit(sid, 1, 0.2, jpeg(np.zeros((430, 500, 3), np.uint8)))
    unseen = live.submit(sid, 2, 0.4, jpeg(render_grid(art[::-1])))
    assert unseen["active_entries"] == 30
    assert unseen["ambiguous_entries"] == 15
    assert len(unseen["box_map"]["segments"]) == 2
    assert unseen["box_map"]["position"] == "unlocated"
    assert unseen["box_map"]["segments"][1]["uncertain"]
    returned = live.submit(sid, 3, 0.6, first_frame)
    assert returned["active_entries"] == 30
    assert returned["box_map"]["viewport"]["segment"] == first["box_map"]["viewport"]["segment"]
    assert returned["box_map"]["position"] == "relocalized"
    assert returned["ambiguous_entries"] == 15  # Unjoined evidence wasn't silently deleted.


def test_repeated_multimonster_blocks_cannot_be_disambiguated_by_direction(store, fixtures):
    live, sid = start(store)
    frame = jpeg(render_grid(images(fixtures)))
    live.submit(sid, 0, 0, frame)
    mapped = BoxMap(store, sid)
    # Simulate another visually identical three-row block at a different map position.
    pitch = mapped.state["segments"][0]["row_pitch"]
    for key, slot in list(mapped.slots.items()):
        mapped.slots[f"other-{key}"] = replace(
            slot, inventory_id=f"other-{key}", world_y=slot.world_y + 5 * pitch
        )
    current = detect(cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_COLOR))
    for direction in ["down", "up", "unknown"]:
        mapped.state["direction"] = direction
        assert mapped.locate(current) is None


def test_single_repeated_artwork_cannot_locate_itself_after_capture_gap(store, fixtures):
    live, sid = start(store)
    frame = jpeg(render_grid([images(fixtures)[0]] * 15))
    live.submit(sid, 0, 0, frame)
    live.control(sid, "resume", discontinuity=True)
    result = live.submit(sid, 1, 0.3, frame)
    assert result["active_entries"] == 30
    assert result["ambiguous_entries"] == 15
    assert result["box_map"]["position"] == "unlocated"
    assert len(result["box_map"]["segments"]) == 2


def test_map_filters_rejections_and_keeps_unknown_and_manual_entries_separate(store, fixtures):
    live, sid = start(store)
    first = live.submit(sid, 0, 0, jpeg(cv2.imread(str(fixtures["image"]))))
    item = first["entries"][0]["id"]
    reject(store, item)
    result = live.snapshot(sid)["box_map"]
    assert result["known_slots"] == 14
    assert item not in {s["inventory_id"] for s in result["slots"]}
    assert read_map(store, "not-a-scan")["slots"] == []


def test_map_templates_missing_on_disk_are_not_used_as_identity_evidence(store, fixtures):
    live, sid = start(store)
    frame = jpeg(cv2.imread(str(fixtures["image"])))
    live.submit(sid, 0, 0, frame)
    with closing(store.connect()) as db, db:
        db.execute(
            "UPDATE observation SET crop='missing-template.png' WHERE recording_id=?", (sid,)
        )
    mapped = BoxMap(store, sid)
    assert not mapped.slots
    assert (
        mapped.locate(detect(cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_COLOR)))
        is None
    )
    assert read_map(store, sid)["known_slots"] == 15  # UI can still show the saved positions.


def test_map_migration_from_v2_keeps_evidence(store, fixtures):
    # Exercise actual v2 migration on a separate copy of the fixture DB.
    with closing(store.connect()) as db, db:
        db.executescript(
            "DROP TABLE scan_map_slot; DROP TABLE scan_map_state; PRAGMA user_version=2;"
        )
    from padbox.database.store import Store

    reopened = Store(store.home)
    assert reopened.references()
    assert read_map(reopened, "none")["known_slots"] == 0
    with closing(reopened.connect()) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_failed_frame_map_changes_rollback_with_observations(store, fixtures, monkeypatch):
    live, sid = start(store)
    original = jpeg(cv2.imread(str(fixtures["image"])))
    first = live.submit(sid, 0, 0, original)
    processor = live.processors[sid]

    def fail(*args):
        raise OSError("Map checkpoint failure")

    monkeypatch.setattr(processor.box_map, "checkpoint", fail)
    moved = jpeg(cv2.imread(str(fixtures["image"].with_name("box-shifted.png"))))
    with pytest.raises(OSError, match="checkpoint"):
        live.submit(sid, 1, 0.4, moved)
    assert read_map(store, sid) == first["box_map"]
    assert live.snapshot(sid)["active_entries"] == 15
    live.control(sid, "resume")
    recovered = live.submit(sid, 1, 0.4, moved)
    assert recovered["active_entries"] == 20
    assert recovered["box_map"]["known_slots"] == 20
    assert len(recovered["box_map"]["segments"]) == 1


def test_map_retains_first_and_latest_visual_evidence_when_overlays_change(store, fixtures):
    live, sid = start(store)
    original = cv2.imread(str(fixtures["image"]))
    live.submit(sid, 0, 0, jpeg(original))
    mapped = BoxMap(store, sid)
    with closing(store.connect()) as db, db:
        for row in db.execute("SELECT * FROM observation WHERE recording_id=?", (sid,)).fetchall():
            crop = cv2.imread(row["crop"])
            # Occlude artwork in the latest observation, while keeping the first
            # observation untouched. Real PAD overlays can animate this way.
            crop[15:65, 10:70] = (30, 40, 220)
            path = store.home / f"overlay-{row['id']}.png"
            assert cv2.imwrite(str(path), crop)
            new_id = f"overlay-{row['id']}"
            db.execute(
                "INSERT INTO observation SELECT ?,recording_id,1,1,rect,?,candidates,"
                "matched_inventory_id,match_reason,tracking_reason FROM observation WHERE id=?",
                (new_id, str(path), row["id"]),
            )
            x, y, w, h = json.loads(row["rect"])
            slot = mapped.slots[row["matched_inventory_id"]]
            mapped.record(
                db,
                slot.inventory_id,
                Cell(x, y, w, h, slot.cell.column, crop),
                slot.world_y,
                new_id,
            )
    # Reloading, rather than the in-memory object, proves both evidence images survive.
    recovered = BoxMap(store, sid)
    current = detect(cv2.imdecode(np.frombuffer(jpeg(original), np.uint8), cv2.IMREAD_COLOR))
    location = recovered.locate(current)
    assert location is not None
    assert len(location.links) == 15
    assert len(recovered.slots) == 15
    for slot in recovered.slots.values():
        assert slot.latest_image is not None
        assert not np.array_equal(slot.cell.image, slot.latest_image)


def disconnected_scroll(store, fixtures, legacy=True):
    """Seven distinct rows, with real copies of the same artwork in different slots."""
    art = images(fixtures)
    sequence = (
        [0, 1, 0, 2, 3]
        + list(range(4, 14))
        + [14, 2, 5, 8, 11]
        + [3, 7, 1, 9, 5]
        + [8, 0, 13, 2, 6]
        + [10, 12, 4, 7, 14]
    )

    def view(start_index):
        return jpeg(render_grid([art[i] for i in sequence[start_index : start_index + 15]]))

    live, sid = start(store)
    first = live.submit(sid, 0, 0, view(0))
    live.submit(sid, 1, 0.2, jpeg(np.zeros((430, 500, 3), np.uint8)))
    for seq, index in enumerate([20, 15, 10], 2):
        if legacy and index == 10:
            # Recreate a saved fragment from the old two-row-only tracker. New
            # scans now join at this single-row bridge instead of fragmenting.
            mapped = live.processors[sid].box_map
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(mapped, "locate", lambda *args, **kwargs: None)
                patch.setattr(mapped, "reconcile", lambda db: ({}, 0))
                last = live.submit(sid, seq, seq * 0.2, view(index))
        else:
            last = live.submit(sid, seq, seq * 0.2, view(index))
    assert len(last["box_map"]["segments"]) == (2 if legacy else 1)
    assert last["active_entries"] == (40 if legacy else 35)
    return live, sid, first, view


def test_later_overlap_joins_fragments_and_resolves_only_copy_tracking(store, fixtures):
    live, sid, first, view = disconnected_scroll(store, fixtures)
    original_ids = {r["id"] for r in first["entries"]}
    corrected = first["entries"][0]["id"]
    accept(store, corrected, 900015)
    joined = live.submit(sid, 5, 1, view(5))
    assert joined["active_entries"] == 35
    assert joined["box_map"]["known_slots"] == 35
    assert len(joined["box_map"]["segments"]) == 1
    assert joined["ambiguous_entries"] == 0
    assert "Joined map sections" in joined["result"]["message"]
    active = [r for r in joined["entries"] if r["status"] not in {"merged", "rejected"}]
    assert original_ids <= {r["id"] for r in active}
    assert next(r for r in active if r["id"] == corrected)["monster_id"] == 900015
    # Same artwork in three genuinely different positions remains three copies.
    assert len([r for r in active if r["proposed_id"] == 900001]) == 3
    with closing(store.connect()) as db:
        merged = db.execute(
            "SELECT id FROM owned WHERE status='merged' AND recording_id=?", (sid,)
        ).fetchall()
        assert len(merged) == 5
        assert all(
            db.execute("SELECT 1 FROM observation WHERE matched_inventory_id=?", (r[0],)).fetchone()
            for r in merged
        )  # Evidence wasn't deleted/reassigned.
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    # Joined positions and counts survive restart, including the archived merge audit.
    restarted = LiveSessions(store)
    restored = restarted.snapshot(sid)
    assert restored["box_map"] == joined["box_map"]
    restarted.control(sid, "resume")
    revisited = restarted.submit(sid, 6, 1.2, view(20))
    assert revisited["active_entries"] == 35
    assert revisited["ambiguous_entries"] == 0


def test_manual_distinct_copy_decision_blocks_automatic_fragment_merge(store, fixtures):
    live, sid, _, view = disconnected_scroll(store, fixtures)
    mapped = live.processors[sid].box_map
    source = [s for s in mapped.slots.values() if s.segment == 1]
    # The uppermost source row overlaps the last row in the original section.
    item = min(source, key=lambda s: (s.world_y, s.cell.column)).inventory_id
    accept(store, item, 900010, distinct=True)
    # Check saved evidence before a new unobscured sample can repair it.
    live.processors[sid].reconcile_map()
    result = live.snapshot(sid)
    assert len(result["box_map"]["segments"]) == 2
    assert next(r for r in result["entries"] if r["id"] == item)["status"] == "confirmed"
    assert result["ambiguous_entries"] > 0


@pytest.mark.parametrize("conflict", ["artwork", "simultaneous"])
def test_fragment_join_validates_saved_overlap_and_never_merges_simultaneous_slots(
    store, fixtures, conflict
):
    live, sid, _, view = disconnected_scroll(store, fixtures)
    mapped = live.processors[sid].box_map
    duplicate = min(
        (s for s in mapped.slots.values() if s.segment == 1),
        key=lambda s: (s.world_y, s.cell.column),
    )
    if conflict == "artwork":
        # An incompatible row blocks a join; one animated/obscured card is now
        # intentionally tolerated when the other columns establish the row.
        for slot in mapped.slots.values():
            if slot.segment == 1 and abs(slot.world_y - duplicate.world_y) < 5:
                slot.cell.image[:] = (20, 100, 220)
                slot.latest_image[:] = (20, 100, 220)
    else:
        with closing(store.connect()) as db, db:
            db.execute(
                "INSERT INTO observation(id,recording_id,frame_index,matched_inventory_id) "
                "VALUES ('simultaneous',?,0,?)",
                (sid, duplicate.inventory_id),
            )
    live.processors[sid].reconcile_map()
    result = live.snapshot(sid)
    assert len(result["box_map"]["segments"]) == 2
    assert result["ambiguous_entries"] > 0
    with closing(store.connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM owned WHERE status='merged'").fetchone()[0] == 0


def test_fragment_join_rolls_back_with_failed_frame_and_can_retry(store, fixtures, monkeypatch):
    live, sid, _, view = disconnected_scroll(store, fixtures)
    before = live.snapshot(sid)
    processor = live.processors[sid]

    def fail(*args):
        raise OSError("Failed joined-map checkpoint")

    monkeypatch.setattr(processor.box_map, "checkpoint", fail)
    with pytest.raises(OSError, match="joined-map checkpoint"):
        live.submit(sid, 5, 1, view(5))
    assert read_map(store, sid) == before["box_map"]
    assert live.snapshot(sid)["active_entries"] == 40
    assert processor.counts == before["counts"]
    live.control(sid, "resume")
    recovered = live.submit(sid, 5, 1, view(5))
    # After the rollback the processor has no adjacent frame. It must relocate,
    # then a fresh overlapping sample can provide the bridge again.
    recovered = live.submit(sid, 6, 1.2, view(10))
    recovered = live.submit(sid, 7, 1.4, view(5))
    assert recovered["active_entries"] == 35
    assert recovered["ambiguous_entries"] == 0


def test_single_row_bridge_joins_without_waiting_for_a_second_row(store, fixtures):
    live, sid, _, _ = disconnected_scroll(store, fixtures, legacy=False)
    result = live.snapshot(sid)
    assert result["active_entries"] == 35
    assert result["ambiguous_entries"] == 0


def test_resume_reconciles_saved_overlap_without_another_captured_frame(store, fixtures):
    live, sid, _, _ = disconnected_scroll(store, fixtures)
    before = live.snapshot(sid)
    source_slots = [s for s in before["box_map"]["slots"] if s["segment"] == 1]
    survivor = max(source_slots, key=lambda s: s["world_y"])
    restarted = LiveSessions(store)
    restarted.control(sid, "resume")
    after = restarted.snapshot(sid)
    assert after["last_sequence"] == before["last_sequence"]
    assert after["counts"] == before["counts"]
    assert after["active_entries"] == 35
    assert len(after["box_map"]["segments"]) == 1
    assert after["ambiguous_entries"] == 0
    same_slot = next(
        s for s in after["box_map"]["slots"] if s["inventory_id"] == survivor["inventory_id"]
    )
    shift = same_slot["world_y"] - survivor["world_y"]
    assert after["box_map"]["viewport"]["top"] == pytest.approx(
        before["box_map"]["viewport"]["top"] + shift
    )
    # Repair is idempotent and doesn't fabricate new observations.
    restarted.control(sid, "pause")
    assert restarted.control(sid, "resume")["box_map"] == after["box_map"]


@pytest.mark.parametrize("restart", [False, True])
def test_five_row_jumps_with_one_row_overlap_and_an_occluded_icon(store, fixtures, restart):
    art = images(fixtures)
    rng = np.random.default_rng(73)
    indices = [int(i) for _ in range(6) for i in rng.permutation(15)][:80]
    live, sid = start(store)
    for seq, (index, phase) in enumerate([(0, 0), (25, 17), (50, 9), (25, 21), (0, 0)]):
        if restart and seq == 1:
            live = LiveSessions(store)
            live.control(sid, "resume")
        frame = render_grid([art[i] for i in indices[index : index + 30]], y=80 + phase, height=660)
        if seq in {1, 2}:
            # Only the first row overlaps; an animated overlay obscures one of
            # its five icons, leaving four ordered artwork anchors.
            cv2.rectangle(
                frame,
                (20 + 94 * 2 + 8, 80 + phase + 15),
                (20 + 94 * 2 + 70, 80 + phase + 60),
                (25, 40, 160),
                -1,
            )
        result = live.submit(sid, seq, seq * 0.8, jpeg(frame))
        assert len(result["result"]["cells"]) == 30
        assert len(result["box_map"]["segments"]) == 1
        assert result["ambiguous_entries"] == 0
    assert result["active_entries"] == result["box_map"]["known_slots"] == 80


def legacy_duplicate_row(store, fixtures, *, single=False, simultaneous=False, wrong_row=False):
    live, sid = start(store)
    live.submit(sid, 0, 0, jpeg(cv2.imread(str(fixtures["image"]))))
    with closing(store.connect()) as db, db:
        row = db.execute(
            "SELECT m.*,v.* FROM scan_map_slot m JOIN observation v "
            "ON v.id=m.observation_id WHERE m.recording_id=? "
            "ORDER BY m.world_y,m.column_index LIMIT 5",
            (sid,),
        ).fetchall()
        for i, original in enumerate(row):
            inventory_id = original["inventory_id"]
            if not single or i == 0:
                inventory_id = f"revisit-{i}"
                db.execute(
                    "INSERT INTO owned(id,proposed_id,status,tracking,recording_id,created_at) "
                    "SELECT ?,proposed_id,'uncertain','ambiguous',recording_id,created_at "
                    "FROM owned WHERE id=?",
                    (inventory_id, original["inventory_id"]),
                )
            rect = json.loads(original["rect"])
            if wrong_row and i:
                rect[1] += 94
            db.execute(
                "INSERT INTO observation SELECT ?,recording_id,100,?, ?,crop,candidates,?,"
                "match_reason,tracking_reason FROM observation WHERE id=?",
                (
                    f"repeat-observation-{i}",
                    0 if simultaneous else 100,
                    json.dumps(rect),
                    inventory_id,
                    original["observation_id"],
                ),
            )
            if not single or i == 0:
                db.execute(
                    "INSERT INTO scan_map_slot SELECT ?,recording_id,segment,column_index,"
                    "world_y,rect,? FROM scan_map_slot WHERE inventory_id=?",
                    (inventory_id, f"repeat-observation-{i}", original["inventory_id"]),
                )
    # Reload the durable legacy evidence; the server has not reconciled it yet.
    return LiveSessions(store), sid


@pytest.mark.parametrize("single", [False, True])
def test_same_position_revisits_repair_with_ordered_rows_and_shared_neighbors(
    store, fixtures, single
):
    live, sid = legacy_duplicate_row(store, fixtures, single=single)
    assert live.snapshot(sid)["active_entries"] == (16 if single else 20)
    result = live.control(sid, "resume")
    assert result["active_entries"] == 15
    assert result["ambiguous_entries"] == 0
    assert len(result["box_map"]["segments"]) == 1
    with closing(store.connect()) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("options", [{"simultaneous": True}, {"single": True, "wrong_row": True}])
def test_same_position_repair_requires_independent_row_context_and_distinct_frames(
    store, fixtures, options
):
    live, sid = legacy_duplicate_row(store, fixtures, **options)
    count = live.snapshot(sid)["active_entries"]
    result = live.control(sid, "resume")
    assert result["active_entries"] == count


def test_manual_distinct_copy_in_legacy_revisit_is_not_merged_away(store, fixtures):
    live, sid = legacy_duplicate_row(store, fixtures, single=True)
    accept(store, "revisit-0", 900001, distinct=True)
    result = live.control(sid, "resume")
    assert result["active_entries"] == 16
    assert next(r for r in result["entries"] if r["id"] == "revisit-0")["status"] == "confirmed"


def test_resume_repair_failure_rolls_back_evidence_and_can_retry(store, fixtures, monkeypatch):
    live, sid = legacy_duplicate_row(store, fixtures)
    before = live.snapshot(sid)

    def fail(*args):
        raise OSError("Repair checkpoint failed")

    with monkeypatch.context() as patch:
        patch.setattr(BoxMap, "save", fail)
        with pytest.raises(OSError, match="Repair checkpoint"):
            live.control(sid, "resume")
    assert live.snapshot(sid)["active_entries"] == before["active_entries"]
    assert read_map(store, sid) == before["box_map"]
    assert live.control(sid, "resume")["active_entries"] == 15


def test_applied_history_cannot_be_repaired_or_deduplicated(store, fixtures):
    _, sid = legacy_duplicate_row(store, fixtures)
    before = read_map(store, sid)
    with closing(store.connect()) as db, db:
        db.execute("UPDATE box_scan SET status='applied' WHERE recording_id=?", (sid,))
    mapped = BoxMap(store, sid)
    with closing(store.connect()) as db, db:
        assert mapped.reconcile(db) == ({}, 0)
        assert db.execute("SELECT COUNT(*) FROM owned WHERE status='merged'").fetchone()[0] == 0
    assert read_map(store, sid) == before


def test_reconciliation_canonicalizes_visible_ids_when_older_fragments_compete(store, fixtures):
    live, sid, _, view = disconnected_scroll(store, fixtures)
    result = live.submit(sid, 5, 1, view(0))
    assert result["active_entries"] == 35
    assert len(result["box_map"]["segments"]) == 1
    ids = [c["inventory_id"] for c in result["result"]["cells"]]
    assert len(ids) == len(set(ids)) == 15
    assert set(ids) <= {s["inventory_id"] for s in result["box_map"]["slots"]}


def test_returning_historical_positions_survive_changed_overlays_without_new_copies(
    store, fixtures
):
    art = images(fixtures)
    rng = np.random.default_rng(128)
    indices = [int(i) for _ in range(4) for i in rng.permutation(15)]
    live, sid = start(store)
    for seq, index in enumerate([0, 25, 0]):
        frame = render_grid([art[i] for i in indices[index : index + 30]], height=660)
        if seq == 2:
            # These rows were absent from the previous viewport. Their slot
            # positions are known, but individual artwork no longer matches the
            # old near-exact threshold because the game overlay changed.
            for col in range(5):
                cv2.rectangle(
                    frame, (20 + col * 94 + 9, 95), (20 + col * 94 + 70, 139), (50, 65, 160), -1
                )
        result = live.submit(sid, seq, seq * 0.5, jpeg(frame))
    assert result["active_entries"] == 55
    assert result["ambiguous_entries"] == 0
    assert len(result["box_map"]["segments"]) == 1
