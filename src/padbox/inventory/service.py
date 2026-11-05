"""User decisions and per-copy accounting, shared by CLI and review UI."""

import json
import sqlite3
import uuid
from contextlib import closing

from padbox.database.store import Store, now
from padbox.inventory.snapshots import current_box, require_editable


def entries(
    store: Store, recording_id: str | None = None, *, inventory_id: str | None = None
) -> list[dict]:
    with closing(store.connect()) as db:
        filters = [
            (key, value)
            for key, value in (("recording_id", recording_id), ("id", inventory_id))
            if value is not None
        ]
        clause = " WHERE " + " AND ".join(f"{key}=?" for key, _ in filters) if filters else ""
        evidence_clause = (
            " WHERE "
            + " AND ".join(
                f"{'matched_inventory_id' if key == 'id' else key}=?" for key, _ in filters
            )
            if filters
            else ""
        )
        params = tuple(value for _, value in filters)
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM owned" + clause + " ORDER BY created_at,id", params
            )
        ]
        observations: dict[str, list[dict]] = {}
        for observation in db.execute(
            "SELECT * FROM observation" + evidence_clause + " ORDER BY timestamp,id", params
        ):
            observations.setdefault(observation["matched_inventory_id"], []).append(
                dict(observation)
            )
    for row in rows:
        evidence = observations.get(row["id"], [])
        representative = evidence[0] if evidence else {}
        # Keep displayed crop/alternatives consistent with the scored proposal after rematching.
        for observation in evidence:
            candidates = json.loads(observation["candidates"] or "[]")
            if (
                candidates
                and candidates[0]["monster_id"] == row["proposed_id"]
                and abs(candidates[0]["score"] - row["confidence"]) < 1e-6
            ):
                representative = observation
                break
        row.update(
            {
                "crop": representative.get("crop"),
                "source_timestamp": representative.get("timestamp"),
                "source_frame": representative.get("frame_index"),
                "candidates": representative.get("candidates"),
                "tracking_reason": evidence[0]["tracking_reason"] if evidence else None,
            }
        )
    return rows


def _event(db: sqlite3.Connection, inventory_id: str, action: str, detail: dict) -> None:
    db.execute(
        "INSERT INTO review_event(inventory_id,action,detail,created_at) VALUES (?,?,?,?)",
        (inventory_id, action, json.dumps(detail), now()),
    )


def accept(store: Store, inventory_id: str, monster_id: int, distinct: bool = False) -> None:
    store.require_monster(monster_id)
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        require_editable(db, inventory_id)
        row = db.execute("SELECT * FROM owned WHERE id=?", (inventory_id,)).fetchone()
        if not row or row["status"] == "merged":
            raise ValueError("Entry missing or merged")
        if row["tracking"] == "ambiguous" and not distinct:
            raise ValueError("Resolve slot ambiguity: confirm a separate copy or merge first")
        db.execute(
            "UPDATE owned SET quantity=CASE WHEN COALESCE(monster_id,proposed_id)=? "
            "THEN quantity ELSE 1 END, "
            "quantity_verified=CASE WHEN COALESCE(monster_id,proposed_id)=? "
            "THEN quantity_verified ELSE 0 END, "
            "monster_id=?,status='confirmed',tracking='clear' WHERE id=?",
            (monster_id, monster_id, monster_id, inventory_id),
        )
        _event(db, inventory_id, "accept", {"monster_id": monster_id, "distinct": distinct})


def reject(store: Store, inventory_id: str) -> None:
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        require_editable(db, inventory_id)
        row = db.execute("SELECT status FROM owned WHERE id=?", (inventory_id,)).fetchone()
        if not row or row["status"] == "merged":
            raise ValueError("Entry missing or merged")
        db.execute("UPDATE owned SET status='rejected' WHERE id=?", (inventory_id,))
        _event(db, inventory_id, "reject", {})


def merge(store: Store, inventory_id: str, target_id: str) -> None:
    if inventory_id == target_id:
        raise ValueError("Cannot merge an entry into itself")
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        require_editable(db, inventory_id)
        require_editable(db, target_id)
        source = db.execute("SELECT * FROM owned WHERE id=?", (inventory_id,)).fetchone()
        target = db.execute("SELECT * FROM owned WHERE id=?", (target_id,)).fetchone()
        if (
            not source
            or not target
            or any(r["status"] in {"merged", "rejected"} for r in [source, target])
        ):
            raise ValueError("Both merge entries must be active")
        if source["recording_id"] != target["recording_id"]:
            scoped = db.execute(
                "SELECT 1 FROM box_scan WHERE recording_id IN (?,?)",
                (source["recording_id"], target["recording_id"]),
            ).fetchone()
            if scoped:
                raise ValueError("Copies can only be merged within the same snapshot scan")
        simultaneous = db.execute(
            """WITH RECURSIVE
             source_group(id) AS (SELECT ? UNION ALL
                SELECT o.id FROM owned o JOIN source_group g ON o.merged_into=g.id),
             target_group(id) AS (SELECT ? UNION ALL
                SELECT o.id FROM owned o JOIN target_group g ON o.merged_into=g.id)
             SELECT 1 FROM observation a JOIN observation b
             ON a.recording_id=b.recording_id AND a.frame_index=b.frame_index
             WHERE a.matched_inventory_id IN (SELECT id FROM source_group)
             AND b.matched_inventory_id IN (SELECT id FROM target_group) LIMIT 1""",
            (inventory_id, target_id),
        ).fetchone()
        if simultaneous:
            raise ValueError("Entries visible in the same frame must remain separate copies")
        db.execute(
            "UPDATE owned SET status='merged',merged_into=? WHERE id=?", (target_id, inventory_id)
        )
        _event(db, inventory_id, "merge", {"target_id": target_id})


def add(
    store: Store, monster_id: int, quantity: int = 1, recording_id: str | None = None
) -> list[str]:
    store.require_monster(monster_id)
    if quantity < 1 or quantity > 10000:
        raise ValueError("Quantity must be between 1 and 10000")
    result = []
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if recording_id:
            scan = db.execute(
                "SELECT status FROM box_scan WHERE recording_id=?", (recording_id,)
            ).fetchone()
            if not scan or scan["status"] != "draft":
                raise ValueError("Choose an editable full-box draft")
        elif db.execute("SELECT 1 FROM box_state").fetchone():
            raise ValueError("Add copies to a draft with --scan; then preview and apply it")
        for _ in range(quantity):
            inventory_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO owned(id,monster_id,status,tracking,recording_id,created_at)
             VALUES (?,?,'confirmed','clear',?,?)""",
                (inventory_id, monster_id, recording_id, now()),
            )
            _event(db, inventory_id, "manual_add", {"monster_id": monster_id})
            result.append(inventory_id)
    return result


def set_quantity(store: Store, monster_id: int, quantity: int) -> None:
    """Adjust confirmed count only; uncertain entries retain their independent evidence."""
    if quantity < 0 or quantity > 10000:
        raise ValueError("Quantity must be between 0 and 10000")
    store.require_monster(monster_id)
    with closing(store.connect()) as db:
        if db.execute("SELECT 1 FROM box_state").fetchone():
            raise ValueError("Adjust draft entry quantities with 'padbox entry-quantity'")
    confirmed = [
        e
        for e in entries(store)
        if e["monster_id"] == monster_id and e["status"] == "confirmed" and e["tracking"] == "clear"
    ]
    if quantity >= len(confirmed):
        if quantity > len(confirmed):
            add(store, monster_id, quantity - len(confirmed))
    else:
        with closing(store.connect()) as db, db:
            # Keep observation evidence, audit removals rather than deleting rows.
            for entry in confirmed[quantity:]:
                db.execute("UPDATE owned SET status='rejected' WHERE id=?", (entry["id"],))
                _event(db, entry["id"], "quantity_decrease", {"monster_id": monster_id})


def set_entry_quantity(store: Store, inventory_id: str, quantity: int) -> None:
    """Set the visible stack count explicitly without merging physical slots."""
    if quantity < 1 or quantity > 10000:
        raise ValueError("Quantity must be between 1 and 10000")
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        require_editable(db, inventory_id)
        row = db.execute("SELECT * FROM owned WHERE id=?", (inventory_id,)).fetchone()
        if not row or row["status"] in {"merged", "rejected"}:
            raise ValueError("Entry missing or inactive")
        mid = row["monster_id"] or row["proposed_id"]
        reference = next((r for r in store.references(str(mid)) if r["id"] == mid), None)
        if quantity != 1 and not (reference and json.loads(reference["raw"]).get("stackable")):
            raise ValueError(
                "Only stackable items have xN quantities; add separate slots for copies"
            )
        db.execute(
            "UPDATE owned SET quantity=?,quantity_verified=1 WHERE id=?", (quantity, inventory_id)
        )
        _event(db, inventory_id, "quantity", {"quantity": quantity})


def assume_pending_stack_quantities(store: Store, recording_id: str) -> int:
    """Explicitly count each unresolved material stack as one in this draft."""
    stackable = {r["id"] for r in store.references() if json.loads(r["raw"]).get("stackable")}
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        scan = db.execute(
            "SELECT s.status,r.status AS capture_status FROM box_scan s "
            "JOIN recording r ON r.id=s.recording_id WHERE s.recording_id=?",
            (recording_id,),
        ).fetchone()
        if not scan or scan["status"] != "draft":
            raise ValueError("Choose an editable snapshot draft; applied history is read-only")
        if scan["capture_status"] not in {"paused", "complete"}:
            raise ValueError("Pause or finish capture before setting stack quantities")
        pending = [
            r
            for r in db.execute(
                "SELECT * FROM owned WHERE recording_id=? AND quantity_verified=0 "
                "AND status NOT IN ('merged','rejected') ORDER BY id",
                (recording_id,),
            )
            if (r["monster_id"] or r["proposed_id"]) in stackable
        ]
        batch_id = str(uuid.uuid4())
        for row in pending:
            db.execute("UPDATE owned SET quantity=1,quantity_verified=1 WHERE id=?", (row["id"],))
            _event(
                db,
                row["id"],
                "quantity",
                {
                    "quantity": 1,
                    "previous_quantity": row["quantity"],
                    "method": "assume_one",
                    "batch_id": batch_id,
                },
            )
        return len(pending)


def statistics(store: Store, recording_id: str | None = None) -> dict[str, int]:
    current = current_box(store) if recording_id is None else None
    if current is not None:
        total = sum(r["quantity"] for r in current["monsters"])
        slots = current.get("capture", {}).get("slots", total)
        return {
            "detected_inventory_entries": slots,
            "active_entries": slots,
            "confirmed_entries": slots,
            "uncertain_entries": 0,
            "unmatched_entries": 0,
            "unique_monster_ids": len(current["monsters"]),
            "total_owned_copies": total,
            "unresolved_tracking": 0,
        }
    rows = entries(store, recording_id)
    active = [e for e in rows if e["status"] not in {"merged", "rejected"}]
    confirmed = [e for e in active if e["status"] == "confirmed" and e["tracking"] == "clear"]
    return {
        "detected_inventory_entries": sum(e["recording_id"] is not None for e in rows),
        "active_entries": len(active),
        "confirmed_entries": len(confirmed),
        "uncertain_entries": sum(
            e["status"] == "uncertain" or e["tracking"] == "ambiguous" for e in active
        ),
        "unmatched_entries": sum(e["status"] == "unmatched" for e in active),
        "unique_monster_ids": len({e["monster_id"] for e in confirmed}),
        "total_owned_copies": sum(e["quantity"] for e in confirmed),
        "unresolved_tracking": sum(e["tracking"] == "ambiguous" for e in active),
    }
