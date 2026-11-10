"""Explicit count-as-one decisions resolve stack work without accepting identities."""

import json
import sqlite3
from contextlib import closing

import pytest

from padbox.export.collection import collection
from padbox.inventory.service import (
    accept,
    add,
    assume_pending_stack_quantities,
    entries,
    reject,
    set_entry_quantity,
)
from padbox.inventory.snapshots import apply_scan, preview_scan
from padbox.live.session import LiveSessions
from padbox.models import Layout


@pytest.fixture
def stack_draft(store):
    with closing(store.connect("reference")) as db, db:
        db.execute("UPDATE monster SET raw=? WHERE id=900001", (json.dumps({"stackable": True}),))
    live = LiveSessions(store)
    sid = live.create(Layout())["id"]
    stacks = add(store, 900001, 4, sid)
    regular = add(store, 900002, 1, sid)[0]
    set_entry_quantity(store, stacks[1], 17)
    reject(store, stacks[2])
    with closing(store.connect()) as db, db:
        db.execute(
            "UPDATE owned SET status='merged',merged_into=? WHERE id=?", (stacks[0], stacks[3])
        )
        # Set an arbitrary unverified count: the action really resets it to one.
        db.execute("UPDATE owned SET quantity=8 WHERE id=?", (stacks[0],))
    live.control(sid, "finish")
    return sid, stacks, regular, live


def test_assume_one_preserves_verified_counts_copies_other_scans_and_apply_boundary(
    store, stack_draft
):
    sid, stacks, regular, live = stack_draft
    other = live.create(Layout())["id"]
    other_entry = add(store, 900001, 1, other)[0]
    before = {r["id"]: r for r in entries(store)}
    stale = preview_scan(store, sid)["preview_token"]
    assert assume_pending_stack_quantities(store, sid) == 1
    after = {r["id"]: r for r in entries(store)}
    assert after.keys() == before.keys()
    assert after[stacks[0]]["quantity"] == 1 and after[stacks[0]]["quantity_verified"]
    for item in [stacks[1], stacks[2], stacks[3], regular, other_entry]:
        assert after[item] == before[item]
    assert collection(store)["monsters"] == []
    with closing(store.connect()) as db:
        detail = json.loads(
            db.execute(
                "SELECT detail FROM review_event WHERE inventory_id=? AND action='quantity'",
                (stacks[0],),
            ).fetchone()[0]
        )
        assert detail["method"] == "assume_one" and detail["previous_quantity"] == 8
    assert assume_pending_stack_quantities(store, sid) == 0
    with pytest.raises(ValueError, match="changed"):
        apply_scan(store, sid, stale, True)
    preview = preview_scan(store, sid)
    assert preview["can_apply"]
    apply_scan(store, sid, preview["preview_token"], True)
    assert {r["monster_id"]: r["quantity"] for r in collection(store)["monsters"]} == {
        900001: 18,
        900002: 1,
    }
    with pytest.raises(ValueError, match="read-only"):
        assume_pending_stack_quantities(store, sid)


def test_assume_one_leaves_identity_and_tracking_review_pending(store, stack_draft):
    sid, stacks, _, _ = stack_draft
    with closing(store.connect()) as db, db:
        db.execute(
            "UPDATE owned SET monster_id=NULL,proposed_id=900001,status='uncertain',"
            "tracking='ambiguous' WHERE id=?",
            (stacks[0],),
        )
    assert assume_pending_stack_quantities(store, sid) == 1
    item = next(r for r in entries(store, sid) if r["id"] == stacks[0])
    assert item["status"] == "uncertain" and item["tracking"] == "ambiguous"
    assert item["quantity_verified"] and item["quantity"] == 1
    assert not preview_scan(store, sid)["can_apply"]
    accept(store, item["id"], 900001, distinct=True)
    assert preview_scan(store, sid)["can_apply"]


def test_assume_one_batch_failure_rolls_back_quantities_and_audits(store, stack_draft):
    sid, stacks, _, _ = stack_draft
    extra = add(store, 900001, 1, sid)[0]
    before = entries(store, sid)
    last = sorted([stacks[0], extra])[-1]
    with closing(store.connect()) as db, db:
        db.execute(
            "CREATE TRIGGER fail_quantity BEFORE INSERT ON review_event "
            f"WHEN NEW.inventory_id='{last}' AND NEW.action='quantity' "
            "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
        )
        events = db.execute("SELECT COUNT(*) FROM review_event").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="simulated failure"):
        assume_pending_stack_quantities(store, sid)
    assert entries(store, sid) == before
    with closing(store.connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM review_event").fetchone()[0] == events


def test_assume_one_requires_paused_or_complete_editable_draft(store, stack_draft):
    sid, _, _, live = stack_draft
    untouched = entries(store, sid)
    with pytest.raises(ValueError, match="editable"):
        assume_pending_stack_quantities(store, "missing")
    running = live.create(Layout())["id"]
    item = add(store, 900001, 1, running)[0]
    with closing(store.connect()) as db, db:
        db.execute("UPDATE recording SET status='running' WHERE id=?", (running,))
    with pytest.raises(ValueError, match="Pause or finish"):
        assume_pending_stack_quantities(store, running)
    live.control(running, "pause")
    assert assume_pending_stack_quantities(store, running) == 1
    assert next(r for r in entries(store, running) if r["id"] == item)["quantity_verified"]
    assert entries(store, sid) == untouched
