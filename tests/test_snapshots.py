"""Replacement semantics and approval boundaries, including real ingestion paths."""

import csv
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from padbox.database.store import INVENTORY_SCHEMA, Store
from padbox.export.collection import collection, csv_export
from padbox.inventory.pipeline import import_media, rematch
from padbox.inventory.service import accept, add, entries, merge, reject, set_entry_quantity
from padbox.inventory.snapshots import apply_scan, history, preview_scan
from padbox.live.session import LiveSessions
from padbox.models import Layout


def draft(live, counts):
    sid = live.create(Layout())["id"]
    for mid, copies in counts.items():
        add(live.store, mid, copies, sid)
    live.control(sid, "finish")
    return sid


def apply(store, sid, expected=None):
    preview = preview_scan(store, sid)
    return apply_scan(store, sid, preview["preview_token"], True, expected)


def counts(store):
    return {r["monster_id"]: r["quantity"] for r in collection(store)["monsters"]}


def test_full_override_diff_duplicates_and_identical_rescan(store):
    add(store, 900001, 2)
    add(store, 900003)
    live = LiveSessions(store)
    first = draft(live, {900001: 3, 900002: 1})
    preview = preview_scan(store, first)
    assert counts(store) == {900001: 2, 900003: 1}
    assert [(r["monster_id"], r["before"], r["after"], r["delta"]) for r in preview["changes"]] == [
        (900001, 2, 3, 1),
        (900002, 0, 1, 1),
        (900003, 1, 0, -1),
    ]
    assert preview["can_apply"]
    apply(store, first, expected=4)
    assert counts(store) == {900001: 3, 900002: 1}
    second = draft(live, {900001: 3, 900002: 1})
    assert preview_scan(store, second)["changes"] == []
    apply(store, second)
    assert counts(store) == {900001: 3, 900002: 1}  # Never eight copies.
    assert len(history(store)) == 2
    assert history(store)[0]["diff"] == []
    assert history(store)[1]["diff"] == preview["changes"]
    csv_rows = list(csv.DictReader(io.StringIO(csv_export(store))))
    assert {int(r["monster_id"]): int(r["quantity"]) for r in csv_rows} == counts(store)
    with closing(store.connect()) as db:
        assert (
            json.loads(
                db.execute("SELECT before_document FROM box_history WHERE revision=1").fetchone()[0]
            )["monsters"][1]["monster_id"]
            == 900003
        )


def test_apply_requires_finished_nonempty_resolved_and_full_box_attestation(store, fixtures):
    live = LiveSessions(store)
    sid = live.create(Layout())["id"]
    preview = preview_scan(store, sid)
    assert not preview["can_apply"]
    with pytest.raises(ValueError, match="Confirm"):
        apply_scan(store, sid, preview["preview_token"], False)
    with pytest.raises(ValueError, match="Finish capture"):
        apply(store, sid)
    live.control(sid, "finish")
    with pytest.raises(ValueError, match="no active entries"):
        apply(store, sid)
    # Emulate an identity/tracking ambiguity left by a missed overlap.
    item = add(store, 900001, 1, sid)[0]
    with closing(store.connect()) as db, db:
        db.execute("UPDATE owned SET status='uncertain',tracking='ambiguous' WHERE id=?", (item,))
    preview = preview_scan(store, sid)
    assert preview["unresolved"][0]["reasons"] == ["identity", "copy tracking"]
    with pytest.raises(ValueError, match="Resolve"):
        apply(store, sid)
    accept(store, item, 900001, distinct=True)
    with pytest.raises(ValueError, match="slot count"):
        apply(store, sid, expected=2)
    assert counts(store) == {}
    apply(store, sid, expected=1)
    assert counts(store) == {900001: 1}


def test_draft_edit_other_application_and_replay_invalidate_preview(store):
    live = LiveSessions(store)
    first = draft(live, {900001: 1})
    token = preview_scan(store, first)["preview_token"]
    add(store, 900002, 1, first)
    with pytest.raises(ValueError, match="changed"):
        apply_scan(store, first, token, True)
    token = preview_scan(store, first)["preview_token"]
    second = draft(live, {900003: 1})
    apply(store, second)
    with pytest.raises(ValueError, match="changed"):
        apply_scan(store, first, token, True)
    assert counts(store) == {900003: 1}
    apply(store, first)
    with pytest.raises(ValueError, match="already been applied"):
        apply(store, first)
    assert len(history(store)) == 2


def test_applied_evidence_is_readonly_and_cross_scan_merges_forbidden(store):
    live = LiveSessions(store)
    first = draft(live, {900001: 1})
    item = next(r for r in entries(store) if r["recording_id"] == first)["id"]
    second = draft(live, {900001: 1})
    other = next(r for r in entries(store) if r["recording_id"] == second)["id"]
    with pytest.raises(ValueError, match="same snapshot"):
        merge(store, item, other)
    apply(store, first)
    for action in [
        lambda: accept(store, item, 900002),
        lambda: reject(store, item),
        lambda: set_entry_quantity(store, item, 1),
        lambda: add(store, 900001, 1, first),
    ]:
        with pytest.raises(ValueError, match="read-only|editable"):
            action()
    rematch(store)
    assert counts(store) == {900001: 1}


def test_stack_quantities_require_verification_and_preserve_export_counts(store):
    with closing(store.connect("reference")) as db, db:
        db.execute("UPDATE monster SET raw=? WHERE id=900001", (json.dumps({"stackable": True}),))
    live = LiveSessions(store)
    sid = draft(live, {900001: 1})
    item = next(r for r in entries(store) if r["recording_id"] == sid)["id"]
    assert preview_scan(store, sid)["unresolved"][0]["reasons"] == ["stack quantity"]
    with pytest.raises(ValueError, match="Resolve"):
        apply(store, sid)
    set_entry_quantity(store, item, 5)
    apply(store, sid)
    assert counts(store) == {900001: 5}
    other = draft(live, {900002: 1})
    other_item = next(r for r in entries(store) if r["recording_id"] == other)["id"]
    with pytest.raises(ValueError, match="Only stackable"):
        set_entry_quantity(store, other_item, 3)


def test_actual_file_import_accepts_strong_draft_only_and_preserves_corrections(store, fixtures):
    first = import_media(store, fixtures["image"])
    sid = first["recording_id"]
    rows = [r for r in entries(store) if r["recording_id"] == sid]
    assert len(rows) == 15
    assert all(r["status"] == "confirmed" for r in rows)
    assert counts(store) == {}
    assert preview_scan(store, sid)["after_total"] == 15
    assert (
        next(
            c
            for c in preview_scan(store, sid)["collection"]["monsters"]
            if c["monster_id"] == 900001
        )["quantity"]
        == 2
    )
    accept(store, rows[0]["id"], 900015)
    rematch(store)
    assert import_media(store, fixtures["image"])["reused"]
    assert entries(store)[0]["monster_id"] == 900015
    apply(store, sid)
    rematch(store)
    assert sum(counts(store).values()) == 15


def test_two_simultaneous_applies_cannot_both_use_the_same_baseline(store):
    live = LiveSessions(store)
    ids = [draft(live, {900001: 1}), draft(live, {900002: 2})]
    tokens = [preview_scan(store, sid)["preview_token"] for sid in ids]

    def attempt(pair):
        try:
            return apply_scan(store, pair[0], pair[1], True)["revision"]
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, zip(ids, tokens, strict=True)))
    assert results.count(1) == 1
    assert sum(isinstance(r, str) and "changed" in r for r in results) == 1
    assert len(history(store)) == 1
    assert counts(store) in [{900001: 1}, {900002: 2}]


def test_v1_migration_preserves_legacy_evidence_and_confirmed_baseline(tmp_path):
    home = tmp_path / "legacy"
    home.mkdir()
    with sqlite3.connect(home / "inventory.sqlite") as db:
        db.executescript(INVENTORY_SCHEMA + "PRAGMA user_version=1;")
        db.execute(
            "INSERT INTO owned(id,monster_id,status,created_at) "
            "VALUES ('old',900001,'confirmed','old')"
        )
        db.execute(
            "INSERT INTO owned(id,proposed_id,status,created_at) "
            "VALUES ('proposal',900002,'uncertain','old')"
        )
    store = Store(home)
    LiveSessions(store).create(Layout())
    assert counts(store) == {900001: 1}
    assert {r["id"] for r in entries(store)} == {"old", "proposal"}
    Store(home)  # Idempotent migration.
    with closing(store.connect()) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
