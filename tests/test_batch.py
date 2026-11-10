"""Threshold review uses saved evidence and preserves full-box approval boundaries."""

import json
import sqlite3
from contextlib import closing

import pytest

from padbox.export.collection import collection
from padbox.inventory.batch import BatchPolicy, accept_batch, preview_batch
from padbox.inventory.pipeline import import_media
from padbox.inventory.service import accept, entries
from padbox.inventory.snapshots import apply_scan, preview_scan


@pytest.fixture
def batch_draft(store, fixtures):
    sid = import_media(store, fixtures["image"])["recording_id"]
    rows = entries(store, sid)
    with closing(store.connect()) as db, db:
        db.execute(
            "DELETE FROM review_event WHERE inventory_id IN "
            "(SELECT id FROM owned WHERE recording_id=?)",
            (sid,),
        )
        db.execute(
            "UPDATE owned SET monster_id=NULL,status='uncertain',high_confidence=0,"
            "confidence=0.82 WHERE recording_id=?",
            (sid,),
        )
        for row in rows:
            mid = row["proposed_id"]
            candidates = [
                {"monster_id": mid, "score": 0.82},
                {"monster_id": 900015 if mid != 900015 else 900014, "score": 0.74},
                {"monster_id": 900013, "score": 0.70},
            ]
            db.execute(
                "UPDATE observation SET candidates=? WHERE matched_inventory_id=?",
                (json.dumps(candidates), row["id"]),
            )
    return sid, rows


def test_batch_accepts_existing_finished_draft_preserves_duplicates_and_exports(store, batch_draft):
    sid, rows = batch_draft
    before = collection(store)
    policy = BatchPolicy()
    preview = preview_batch(store, sid, policy)
    assert preview["eligible_count"] == 15
    assert preview["remaining_identity_count"] == 0
    assert collection(store) == before  # Preview is read-only.
    result = accept_batch(store, sid, policy, preview["preview_token"])
    assert result["accepted_count"] == 15
    assert len(entries(store, sid)) == 15
    assert all(r["status"] == "confirmed" and not r["high_confidence"] for r in entries(store, sid))
    assert len([r for r in entries(store, sid) if r["monster_id"] == 900001]) == 2
    assert collection(store) == before  # Acceptance changes only the draft.
    with closing(store.connect()) as db:
        audits = db.execute(
            "SELECT detail FROM review_event WHERE action='batch_accept'"
        ).fetchall()
        assert len(audits) == 15
        assert all(json.loads(r[0])["policy"] == policy.model_dump() for r in audits)
        assert len({json.loads(r[0])["batch_id"] for r in audits}) == 1
    diff = preview_scan(store, sid)
    assert diff["can_apply"]
    apply_scan(store, sid, diff["preview_token"], True)
    assert sum(m["quantity"] for m in collection(store)["monsters"]) == 15


def test_thresholds_margins_and_weakest_first_preview(store, batch_draft):
    sid, rows = batch_draft
    with closing(store.connect()) as db, db:
        for row, score in zip(rows[:3], [0.60, 0.75, 0.90], strict=True):
            db.execute("UPDATE owned SET confidence=? WHERE id=?", (score, row["id"]))
            ranked = [
                {"monster_id": row["proposed_id"], "score": score},
                {"monster_id": 900015, "score": score - 0.06},
            ]
            db.execute(
                "UPDATE observation SET candidates=? WHERE matched_inventory_id=?",
                (json.dumps(ranked), row["id"]),
            )
    preview = preview_batch(store, sid, BatchPolicy(min_score=0.75, min_margin=0.06))
    assert preview["eligible_count"] == 14
    assert preview["entries"][0]["score"] == 0.75
    assert preview["skipped"]["below_score"] == 1
    assert next(b["count"] for b in preview["score_bands"] if b["min_score"] == 0.60) == 15
    assert preview_batch(store, sid, BatchPolicy(min_score=0.85))["eligible_count"] == 1
    assert preview_batch(store, sid, BatchPolicy(min_margin=0.09))["eligible_count"] == 0


@pytest.mark.parametrize(
    "issue,reason",
    [
        ("ambiguous", "copy_tracking"),
        ("unmatched", "unmatched"),
        ("missing_reference", "missing_reference"),
        ("missing_evidence", "missing_ranked_evidence"),
        ("wrong_evidence", "missing_ranked_evidence"),
        ("tie", "close_alternatives"),
        ("previous_review", "previous_identity_review"),
    ],
)
def test_batch_preserves_uncertainty_and_manual_decisions(store, batch_draft, issue, reason):
    sid, rows = batch_draft
    item = rows[0]["id"]
    with closing(store.connect()) as db, db:
        if issue == "ambiguous":
            db.execute("UPDATE owned SET tracking='ambiguous' WHERE id=?", (item,))
        elif issue == "unmatched":
            db.execute("UPDATE owned SET status='unmatched',proposed_id=NULL WHERE id=?", (item,))
        elif issue == "missing_reference":
            db.execute("UPDATE owned SET proposed_id=99999999 WHERE id=?", (item,))
        elif issue == "previous_review":
            db.execute(
                "INSERT INTO review_event(inventory_id,action,created_at) "
                "VALUES (?,'accept','now')",
                (item,),
            )
        else:
            candidates = (
                []
                if issue == "missing_evidence"
                else [
                    {
                        "monster_id": 900015
                        if issue == "wrong_evidence"
                        else rows[0]["proposed_id"],
                        "score": 0.82,
                    },
                    {"monster_id": 900014, "score": 0.82 if issue == "tie" else 0.70},
                ]
            )
            db.execute(
                "UPDATE observation SET candidates=? WHERE matched_inventory_id=?",
                (json.dumps(candidates), item),
            )
    policy = BatchPolicy(min_margin=0)
    preview = preview_batch(store, sid, policy)
    assert preview["skipped"][reason] == 1
    assert preview["eligible_count"] == 14
    accept_batch(store, sid, policy, preview["preview_token"])
    assert next(r for r in entries(store, sid) if r["id"] == item)["status"] != "confirmed"


def test_accepting_stack_identity_does_not_verify_quantity_or_bypass_apply(store, batch_draft):
    sid, _ = batch_draft
    with closing(store.connect("reference")) as db, db:
        db.execute("UPDATE monster SET raw=?", (json.dumps({"stackable": True}),))
    policy = BatchPolicy()
    preview = preview_batch(store, sid, policy)
    assert preview["quantity_checks_remaining"] == 15
    accept_batch(store, sid, policy, preview["preview_token"])
    assert all(not r["quantity_verified"] and r["quantity"] == 1 for r in entries(store, sid))
    assert not preview_scan(store, sid)["can_apply"]


@pytest.mark.parametrize("change", ["correction", "evidence", "quantity", "policy", "reference"])
def test_stale_batch_preview_is_rejected_atomically(store, batch_draft, change):
    sid, rows = batch_draft
    policy = BatchPolicy()
    token = preview_batch(store, sid, policy)["preview_token"]
    if change == "correction":
        accept(store, rows[0]["id"], 900015)
    elif change == "reference":
        with closing(store.connect("reference")) as db, db:
            db.execute("UPDATE monster SET name='Updated' WHERE id=900001")
    elif change == "policy":
        policy = BatchPolicy(min_score=0.75)
    else:
        with closing(store.connect()) as db, db:
            if change == "quantity":
                db.execute("UPDATE owned SET quantity_verified=1 WHERE id=?", (rows[0]["id"],))
            else:
                db.execute(
                    "UPDATE observation SET candidates='[]' WHERE matched_inventory_id=?",
                    (rows[0]["id"],),
                )
    before = entries(store, sid)
    with pytest.raises(ValueError, match="changed"):
        accept_batch(store, sid, policy, token)
    assert entries(store, sid) == before


def test_batch_write_failure_rolls_back_prior_acceptances(store, batch_draft):
    sid, _ = batch_draft
    policy = BatchPolicy()
    preview = preview_batch(store, sid, policy)
    item = preview["entries"][1]["inventory_id"]
    with closing(store.connect()) as db, db:
        # Force a mid-batch disk/constraint failure after the first acceptance.
        db.execute(
            "CREATE TRIGGER fail_batch BEFORE INSERT ON review_event "
            f"WHEN NEW.inventory_id='{item}' BEGIN SELECT RAISE(ABORT,'audit failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="audit failed"):
        accept_batch(store, sid, policy, preview["preview_token"])
    assert all(r["status"] == "uncertain" for r in entries(store, sid))
    with closing(store.connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM review_event").fetchone()[0] == 0


def test_running_unknown_and_applied_scans_cannot_batch_accept(store, batch_draft):
    sid, _ = batch_draft
    policy = BatchPolicy()
    with pytest.raises(ValueError, match="editable"):
        preview_batch(store, "missing", policy)
    with closing(store.connect()) as db, db:
        db.execute("UPDATE recording SET status='running' WHERE id=?", (sid,))
    with pytest.raises(ValueError, match="Pause or finish"):
        preview_batch(store, sid, policy)
    with closing(store.connect()) as db, db:
        db.execute("UPDATE box_scan SET status='applied' WHERE recording_id=?", (sid,))
    with pytest.raises(ValueError, match="read-only"):
        preview_batch(store, sid, policy)


def test_previous_batch_cannot_be_replayed(store, batch_draft):
    sid, _ = batch_draft
    policy = BatchPolicy()
    token = preview_batch(store, sid, policy)["preview_token"]
    accept_batch(store, sid, policy, token)
    with pytest.raises(ValueError, match="changed"):
        accept_batch(store, sid, policy, token)


@pytest.mark.parametrize(
    "values",
    [
        {"min_score": -0.01},
        {"min_score": float("nan")},
        {"min_margin": -0.01},
        {"min_margin": 1.01},
        {"min_score": 1.01},
        {"include_ambiguous": True},
    ],
)
def test_policy_validation(values):
    with pytest.raises(ValueError):
        BatchPolicy(**values)


@pytest.mark.parametrize("score", [0.0, 0.20, 0.55, 0.59])
def test_explicit_batch_threshold_can_be_below_matcher_proposal_cutoff(store, batch_draft, score):
    sid, rows = batch_draft
    item = rows[0]["id"]
    ranked = [
        {"monster_id": rows[0]["proposed_id"], "score": 0.55},
        {"monster_id": 900015, "score": 0.40},
    ]
    with closing(store.connect()) as db, db:
        db.execute("UPDATE owned SET confidence=0.55 WHERE id=?", (item,))
        db.execute(
            "UPDATE observation SET candidates=? WHERE matched_inventory_id=?",
            (json.dumps(ranked), item),
        )
    policy = BatchPolicy(min_score=score)
    preview = preview_batch(store, sid, policy)
    assert preview["eligible_count"] == (15 if score <= 0.55 else 14)
    accept_batch(store, sid, policy, preview["preview_token"])
    assert (next(r for r in entries(store, sid) if r["id"] == item)["status"] == "confirmed") == (
        score <= 0.55
    )


def test_zero_threshold_does_not_invent_an_identity_for_unmatched_cards(store, batch_draft):
    sid, rows = batch_draft
    with closing(store.connect()) as db, db:
        db.execute(
            "UPDATE owned SET proposed_id=NULL,status='unmatched',confidence=0.50 WHERE id=?",
            (rows[0]["id"],),
        )
    preview = preview_batch(store, sid, BatchPolicy(min_score=0, min_margin=0))
    assert preview["eligible_count"] == 14
    assert preview["skipped"]["unmatched"] == 1
