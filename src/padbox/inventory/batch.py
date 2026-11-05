"""Explicit threshold-based identity review of an existing snapshot draft."""

import hashlib
import json
import secrets
import sqlite3
import uuid
from collections import Counter
from contextlib import closing

from pydantic import BaseModel, ConfigDict, Field

from padbox.database.store import Store, now


class BatchPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_score: float = Field(default=0.80, ge=0, le=1, allow_inf_nan=False)
    min_margin: float = Field(default=0.04, ge=0, le=1, allow_inf_nan=False)


def _preview(
    db: sqlite3.Connection, recording_id: str, policy: BatchPolicy, references: dict[int, dict]
) -> dict:
    scan = db.execute(
        "SELECT s.status,r.status AS capture_status FROM box_scan s "
        "JOIN recording r ON r.id=s.recording_id WHERE s.recording_id=?",
        (recording_id,),
    ).fetchone()
    if not scan or scan["status"] != "draft":
        raise ValueError("Choose an editable snapshot draft; applied history is read-only")
    if scan["capture_status"] not in {"paused", "complete"}:
        raise ValueError("Pause or finish capture before batch review")
    rows = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM owned WHERE recording_id=? ORDER BY id",
            (recording_id,),
        )
    ]
    evidence: dict[str, list[dict]] = {}
    for row in db.execute(
        "SELECT id,matched_inventory_id,candidates FROM observation "
        "WHERE recording_id=? ORDER BY rowid",
        (recording_id,),
    ):
        evidence.setdefault(row["matched_inventory_id"], []).append(dict(row))
    events = [
        dict(r)
        for r in db.execute(
            "SELECT e.* FROM review_event e JOIN owned o ON o.id=e.inventory_id "
            "WHERE o.recording_id=? ORDER BY e.id",
            (recording_id,),
        )
    ]
    protected = {
        r["inventory_id"]
        for r in events
        if r["action"] in {"accept", "reject", "merge", "manual_add", "batch_accept"}
    }
    skipped: Counter[str] = Counter()
    eligible, separated = [], []
    for row in rows:
        if row["status"] in {"merged", "rejected"}:
            continue
        reason = None
        if row["status"] == "confirmed":
            reason = "already_confirmed"
        elif row["status"] != "uncertain" or row["proposed_id"] is None:
            reason = "unmatched"
        elif row["tracking"] != "clear":
            reason = "copy_tracking"
        elif row["id"] in protected:
            reason = "previous_identity_review"
        elif row["proposed_id"] not in references:
            reason = "missing_reference"
        candidates, observation_id = [], None
        if reason is None:
            for observation in evidence.get(row["id"], []):
                ranked = json.loads(observation["candidates"] or "[]")
                if (
                    len(ranked) >= 2
                    and ranked[0]["monster_id"] == row["proposed_id"]
                    and abs(ranked[0]["score"] - row["confidence"]) < 1e-6
                ):
                    candidates, observation_id = ranked, observation["id"]
                    break
            if not candidates:
                reason = "missing_ranked_evidence"
        if reason:
            skipped[reason] += 1
            continue
        margin = candidates[0]["score"] - max(c["score"] for c in candidates[1:])
        if margin <= 0 or margin + 1e-9 < policy.min_margin:
            skipped["close_alternatives"] += 1
            continue
        reference = references[row["proposed_id"]]
        item = {
            "inventory_id": row["id"],
            "monster_id": row["proposed_id"],
            "name": reference["name"],
            "score": row["confidence"],
            "margin": margin,
            "quantity": row["quantity"],
            "observation_id": observation_id,
            "needs_quantity": bool(json.loads(reference["raw"]).get("stackable"))
            and not row["quantity_verified"],
        }
        separated.append(item)
        if row["confidence"] + 1e-9 < policy.min_score:
            skipped["below_score"] += 1
        else:
            eligible.append(item)
    eligible.sort(key=lambda r: (r["score"], r["margin"], r["inventory_id"]))
    # Bind confirmation to the actual evidence, decisions and policy. Recompute
    # inside the acceptance transaction so a stale page cannot accept changed IDs.
    document = {
        "scan": recording_id,
        "status": dict(scan),
        "policy": policy.model_dump(),
        "rows": rows,
        "evidence": evidence,
        "events": events,
        "references": [
            references[mid] for mid in sorted({r["proposed_id"] for r in rows} & references.keys())
        ],
    }
    token = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    return {
        "recording_id": recording_id,
        "policy": policy.model_dump(),
        "preview_token": token,
        "eligible_count": len(eligible),
        "entries": eligible,
        "skipped": dict(skipped),
        "remaining_identity_count": sum(r["status"] in {"uncertain", "unmatched"} for r in rows)
        - len(eligible),
        "quantity_checks_remaining": sum(r["needs_quantity"] for r in eligible),
        "score_bands": [
            {"min_score": score, "count": sum(r["score"] + 1e-9 >= score for r in separated)}
            for score in (0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60)
        ],
    }


def preview_batch(store: Store, recording_id: str, policy: BatchPolicy) -> dict:
    references = {r["id"]: r for r in store.references()}
    with closing(store.connect()) as db, db:
        db.execute("BEGIN")
        return _preview(db, recording_id, policy, references)


def accept_batch(store: Store, recording_id: str, policy: BatchPolicy, preview_token: str) -> dict:
    references = {r["id"]: r for r in store.references()}
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        preview = _preview(db, recording_id, policy, references)
        if not secrets.compare_digest(preview["preview_token"], preview_token):
            raise ValueError("Batch preview changed. Preview qualifying matches again")
        if not preview["entries"]:
            raise ValueError("No identities qualify for these thresholds")
        batch_id = str(uuid.uuid4())
        for row in preview["entries"]:
            db.execute(
                "UPDATE owned SET monster_id=proposed_id,status='confirmed' WHERE id=?",
                (row["inventory_id"],),
            )
            detail = {
                **row,
                "policy": policy.model_dump(),
                "batch_id": batch_id,
                "preview_token": preview_token,
            }
            db.execute(
                "INSERT INTO review_event(inventory_id,action,detail,created_at) "
                "VALUES (?,'batch_accept',?,?)",
                (row["inventory_id"], json.dumps(detail), now()),
            )
    return {
        "accepted_count": preview["eligible_count"],
        "batch_id": batch_id,
        "remaining_identity_count": preview["remaining_identity_count"],
        "quantity_checks_remaining": preview["quantity_checks_remaining"],
    }
