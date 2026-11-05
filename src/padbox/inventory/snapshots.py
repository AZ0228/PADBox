"""Isolated full-box drafts, reviewed diffs and atomic collection replacement."""

import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing

from padbox.database.store import Store, now
from padbox.recognition.matcher import HIGH_SCORE, MIN_MARGIN

REPEATED_SCORE = 0.85
REPEATED_FRAMES = 3


def _document(rows: list[dict]) -> dict:
    counts: Counter[int] = Counter()
    for row in rows:
        if row["status"] == "confirmed" and row["tracking"] == "clear":
            counts[row["monster_id"]] += row["quantity"]
    return {
        "schema_version": 1,
        "server": "NA",
        "monsters": [
            {"monster_id": mid, "quantity": quantity, "confirmed": True}
            for mid, quantity in sorted(counts.items())
        ],
        "unresolved": [],
        "complete": True,
    }


def _initialize(db: sqlite3.Connection) -> None:
    if db.execute("SELECT 1 FROM box_state WHERE id=1").fetchone():
        return
    # Retain only verified legacy ownership as the initial baseline. Old scan
    # proposals remain evidence, never become ownership through migration.
    rows = [dict(r) for r in db.execute("SELECT * FROM owned")]
    db.execute("INSERT INTO box_state VALUES (1,0,?,NULL)", (json.dumps(_document(rows)),))


def register_scan(db: sqlite3.Connection, recording_id: str) -> None:
    """Called in the recording creation transaction, before observations exist."""
    _initialize(db)
    db.execute(
        "INSERT OR IGNORE INTO box_scan(recording_id,created_at) VALUES (?,?)",
        (recording_id, now()),
    )


def is_scan(store: Store, recording_id: str) -> bool:
    with closing(store.connect()) as db:
        return (
            db.execute("SELECT 1 FROM box_scan WHERE recording_id=?", (recording_id,)).fetchone()
            is not None
        )


def require_editable(db: sqlite3.Connection, inventory_id: str) -> None:
    row = db.execute(
        "SELECT s.status FROM box_scan s JOIN owned o ON o.recording_id=s.recording_id "
        "WHERE o.id=?",
        (inventory_id,),
    ).fetchone()
    if row and row["status"] != "draft":
        raise ValueError("Applied scans are read-only; start a new full-box scan")


def current_box(store: Store) -> dict | None:
    with closing(store.connect()) as db:
        row = db.execute("SELECT * FROM box_state WHERE id=1").fetchone()
    if not row:
        return None
    return {
        **json.loads(row["document"]),
        "revision": row["revision"],
        "source_scan": row["source_scan"],
    }


def scan_list(store: Store) -> list[dict]:
    with closing(store.connect()) as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT s.*,r.path,r.created_at AS captured_at,r.status AS capture_status "
                "FROM box_scan s JOIN recording r ON r.id=s.recording_id "
                "ORDER BY r.created_at DESC"
            )
        ]


def auto_accept_strong(store: Store, recording_id: str) -> int:
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        scan = db.execute(
            "SELECT status FROM box_scan WHERE recording_id=?", (recording_id,)
        ).fetchone()
        if not scan or scan["status"] != "draft":
            return 0
        rows = db.execute(
            "SELECT * FROM owned WHERE recording_id=? AND status='uncertain' "
            "AND tracking='clear' AND confidence>=? AND proposed_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM review_event e WHERE e.inventory_id=owned.id "
            "AND e.action<>'map_join')",
            (recording_id, REPEATED_SCORE),
        ).fetchall()
        accepted = 0
        for row in rows:
            strong = bool(row["high_confidence"]) and row["confidence"] >= HIGH_SCORE
            evidence_frames = []
            if not strong:
                # A lower single-frame threshold alone would silently increase false
                # acceptance. Require separated, agreeing results in recent frames.
                evidence = db.execute(
                    "SELECT frame_index,candidates FROM observation WHERE matched_inventory_id=? "
                    "ORDER BY rowid DESC LIMIT ?",
                    (row["id"], REPEATED_FRAMES),
                ).fetchall()
                for observation in evidence:
                    candidates = json.loads(observation["candidates"] or "[]")
                    if (
                        len(candidates) < 3
                        or candidates[0]["monster_id"] != row["proposed_id"]
                        or candidates[0]["score"] < REPEATED_SCORE
                        or candidates[0]["score"] - candidates[1]["score"] < MIN_MARGIN
                    ):
                        break
                    evidence_frames.append(observation["frame_index"])
                if len(set(evidence_frames)) < REPEATED_FRAMES:
                    continue
                with closing(store.connect("reference")) as refs:
                    monster = refs.execute(
                        "SELECT raw FROM monster WHERE id=?",
                        (row["proposed_id"],),
                    ).fetchone()
                if not monster or json.loads(monster[0]).get("stackable"):
                    continue
            db.execute(
                "UPDATE owned SET monster_id=proposed_id,status='confirmed',high_confidence=1 "
                "WHERE id=?",
                (row["id"],),
            )
            db.execute(
                "INSERT INTO review_event(inventory_id,action,detail,created_at) VALUES (?,?,?,?)",
                (
                    row["id"],
                    "auto_accept",
                    json.dumps(
                        {
                            "monster_id": row["proposed_id"],
                            "score": row["confidence"],
                            "rule": "single_strong" if strong else "repeated_separated",
                            "evidence_frames": evidence_frames,
                        }
                    ),
                    now(),
                ),
            )
            accepted += 1
        return accepted


def _preview(store: Store, db: sqlite3.Connection, recording_id: str) -> dict:
    scan = db.execute(
        "SELECT s.*,r.status AS capture_status FROM box_scan s "
        "JOIN recording r ON r.id=s.recording_id WHERE s.recording_id=?",
        (recording_id,),
    ).fetchone()
    if not scan:
        raise ValueError("Not a full-box snapshot; start a new scan")
    state = db.execute("SELECT * FROM box_state WHERE id=1").fetchone()
    rows = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM owned WHERE recording_id=? ORDER BY id",
            (recording_id,),
        )
    ]
    active = [r for r in rows if r["status"] not in {"merged", "rejected"}]
    references = {r["id"]: r for r in store.references()}
    unresolved = []
    for row in active:
        problems = []
        if row["status"] != "confirmed" or not row["monster_id"]:
            problems.append("identity")
        if row["tracking"] != "clear":
            problems.append("copy tracking")
        ref = references.get(row["monster_id"])
        if row["monster_id"] and not ref:
            problems.append("missing NA reference")
        if ref and json.loads(ref["raw"]).get("stackable") and not row["quantity_verified"]:
            problems.append("stack quantity")
        if ref and not json.loads(ref["raw"]).get("stackable") and row["quantity"] != 1:
            problems.append("non-stackable quantity")
        if problems:
            unresolved.append({"inventory_id": row["id"], "reasons": problems})
    before = json.loads(state["document"])
    after = _document(active)
    old = {r["monster_id"]: r["quantity"] for r in before["monsters"]}
    new = {r["monster_id"]: r["quantity"] for r in after["monsters"]}
    changes = [
        {
            "monster_id": mid,
            "name": references.get(mid, {}).get("name", "Unknown"),
            "before": old.get(mid, 0),
            "after": new.get(mid, 0),
            "delta": new.get(mid, 0) - old.get(mid, 0),
        }
        for mid in sorted(old.keys() | new.keys())
        if old.get(mid, 0) != new.get(mid, 0)
    ]
    # Bind approval to the current box AND all draft evidence, including quantities,
    # rejected tracks, and capture state. Edits or another applied scan invalidate it.
    digest = hashlib.sha256(
        json.dumps([dict(state), dict(scan), rows], sort_keys=True).encode()
    ).hexdigest()
    blockers = []
    if scan["status"] != "draft":
        blockers.append("This scan has already been applied")
    if scan["capture_status"] != "complete":
        blockers.append("Finish capture before applying")
    if not active:
        blockers.append("The scan contains no active entries")
    if unresolved:
        blockers.append(f"Resolve {len(unresolved)} entries before applying")
    return {
        "scan_id": recording_id,
        "scan_status": scan["status"],
        "base_revision": state["revision"],
        "preview_token": digest,
        "before_total": sum(old.values()),
        "after_total": sum(new.values()),
        "active_slots": len(active),
        "changes": changes,
        "added_copies": sum(max(0, c["delta"]) for c in changes),
        "removed_copies": sum(max(0, -c["delta"]) for c in changes),
        "unresolved": unresolved,
        "blockers": blockers,
        "can_apply": not blockers,
        "collection": after,
    }


def preview_scan(store: Store, recording_id: str) -> dict:
    with closing(store.connect()) as db:
        db.execute("BEGIN")
        return _preview(store, db, recording_id)


def apply_scan(
    store: Store,
    recording_id: str,
    preview_token: str,
    full_box_verified: bool,
    expected_slots: int | None = None,
) -> dict:
    if not full_box_verified:
        raise ValueError("Confirm that the complete unfiltered box and quantities were captured")
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        preview = _preview(store, db, recording_id)
        if preview_token != preview["preview_token"]:
            raise ValueError(
                "The scan or current box changed. Preview the diff again before applying"
            )
        if preview["blockers"]:
            raise ValueError("; ".join(preview["blockers"]))
        if expected_slots is not None and expected_slots != preview["active_slots"]:
            raise ValueError("Expected box slot count does not match the detected/reviewed slots")
        before = db.execute("SELECT document FROM box_state WHERE id=1").fetchone()[0]
        document = {
            **preview["collection"],
            "capture": {
                "full_box_verified": True,
                "expected_slots": expected_slots,
                "slots": preview["active_slots"],
            },
        }
        after = json.dumps(document)
        revision = preview["base_revision"] + 1
        db.execute(
            "UPDATE box_state SET revision=?,document=?,source_scan=? WHERE id=1",
            (revision, after, recording_id),
        )
        db.execute(
            "UPDATE box_scan SET status='applied',applied_at=? WHERE recording_id=?",
            (now(), recording_id),
        )
        db.execute(
            "INSERT INTO box_history VALUES (?,?,?,?,?,?)",
            (revision, recording_id, before, after, json.dumps(preview["changes"]), now()),
        )
    return {"revision": revision, "source_scan": recording_id, **document}


def history(store: Store) -> list[dict]:
    with closing(store.connect()) as db:
        return [
            {**dict(r), "diff": json.loads(r["diff"])}
            for r in db.execute(
                "SELECT revision,recording_id,diff,applied_at FROM box_history "
                "ORDER BY revision DESC"
            )
        ]
