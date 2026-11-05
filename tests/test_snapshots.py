"""Replacement semantics and approval boundaries, including real ingestion paths."""

from padbox.export.collection import collection
from padbox.inventory.pipeline import import_media, rematch
from padbox.inventory.service import accept, add, entries
from padbox.inventory.snapshots import apply_scan, preview_scan
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
