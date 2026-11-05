"""Evidence gates for repeated matches; fabricated scores test policy, not accuracy."""

import json
from contextlib import closing

import pytest

from padbox.inventory.snapshots import auto_accept_strong
from padbox.live.session import LiveSessions
from padbox.models import Layout


def candidate_entry(store, scores=(0.88, 0.89, 0.87), runner_up=0.78, **options):
    sid = LiveSessions(store).create(Layout())["id"]
    with closing(store.connect()) as db, db:
        db.execute(
            "INSERT INTO owned(id,proposed_id,tracking,confidence,high_confidence,status,"
            "recording_id,created_at) VALUES ('candidate',900001,?,?,0,'uncertain',?,'now')",
            (options.get("tracking", "clear"), max(scores), sid),
        )
        for i, score in enumerate(scores):
            candidates = [
                {"monster_id": 900001, "score": score},
                {"monster_id": 900002, "score": runner_up},
                {"monster_id": 900003, "score": 0.70},
            ]
            if options.get("disagree") and i == 1:
                candidates[0]["monster_id"] = 900004
            db.execute(
                "INSERT INTO observation(id,recording_id,frame_index,timestamp,candidates,"
                "matched_inventory_id) VALUES (?,?,?,?,?,'candidate')",
                (f"o{i}", sid, 0 if options.get("same_frame") else i, i, json.dumps(candidates)),
            )
        if options.get("reviewed"):
            db.execute(
                "INSERT INTO review_event(inventory_id,action,created_at) "
                "VALUES ('candidate','reject','now')"
            )
        if options.get("applied"):
            db.execute("UPDATE box_scan SET status='applied' WHERE recording_id=?", (sid,))
    if options.get("stackable"):
        with closing(store.connect("reference")) as db, db:
            db.execute(
                "UPDATE monster SET raw=? WHERE id=900001", (json.dumps({"stackable": True}),)
            )
    return sid


def test_repeated_separated_matches_accept_once_with_auditable_evidence(store):
    sid = candidate_entry(store)
    assert auto_accept_strong(store, sid) == 1
    assert auto_accept_strong(store, sid) == 0
    with closing(store.connect()) as db:
        row = db.execute("SELECT * FROM owned WHERE id='candidate'").fetchone()
        assert row["status"] == "confirmed"
        assert row["monster_id"] == 900001
        assert row["high_confidence"]
        audit = json.loads(db.execute("SELECT detail FROM review_event").fetchone()[0])
        assert audit["rule"] == "repeated_separated"
        assert set(audit["evidence_frames"]) == {0, 1, 2}
        assert (
            json.loads(db.execute("SELECT document FROM box_state").fetchone()[0])["monsters"] == []
        )


@pytest.mark.parametrize(
    "options",
    [
        {"scores": (0.88, 0.89)},
        {"scores": (0.88, 0.84, 0.89)},
        {"runner_up": 0.85},
        {"disagree": True},
        {"same_frame": True},
        {"tracking": "ambiguous"},
        {"stackable": True},
        {"reviewed": True},
        {"applied": True},
    ],
)
def test_repeat_acceptance_preserves_all_uncertainty_and_review_gates(store, options):
    sid = candidate_entry(store, **options)
    assert auto_accept_strong(store, sid) == 0
    with closing(store.connect()) as db:
        assert db.execute("SELECT monster_id FROM owned WHERE id='candidate'").fetchone()[0] is None


def test_recent_disagreement_blocks_using_only_earlier_agreement(store):
    sid = candidate_entry(store, scores=(0.89, 0.89, 0.89, 0.82))
    assert auto_accept_strong(store, sid) == 0
