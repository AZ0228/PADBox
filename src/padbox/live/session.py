"""Durable live sessions; one frame in flight, bounded input, explicit uncertainty."""

import json
import math
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from padbox.database.store import Store, now
from padbox.inventory.box_map import read_map
from padbox.inventory.processor import FrameProcessor
from padbox.inventory.service import accept, entries, merge, reject, set_entry_quantity
from padbox.inventory.snapshots import register_scan
from padbox.models import Layout
from padbox.recognition.matcher import Matcher
from padbox.video.reader import Frame

MAX_JPEG_BYTES = 4_000_000
MAX_PIXELS = 10_000_000


def decode_frame(payload: bytes) -> np.ndarray:
    """Validate JPEG dimensions before allocating the decoded bitmap."""
    if not payload or len(payload) > MAX_JPEG_BYTES or not payload.startswith(b"\xff\xd8"):
        raise ValueError("Expected a JPEG frame under 4 MB")
    position = 2
    dimensions = None
    while position + 4 <= len(payload):
        if payload[position] != 0xFF:
            break
        marker = payload[position + 1]
        position += 2
        if marker == 0xFF:
            position -= 1
            continue
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        size = int.from_bytes(payload[position : position + 2], "big")
        if size < 2 or position + size > len(payload):
            break
        if marker in {0xC0, 0xC1, 0xC2} and size >= 8:
            height = int.from_bytes(payload[position + 3 : position + 5], "big")
            width = int.from_bytes(payload[position + 5 : position + 7], "big")
            dimensions = (width, height)
            break
        position += size
    if dimensions is None:
        raise ValueError("Invalid or unsupported JPEG frame")
    width, height = dimensions
    if min(width, height) < 64 or max(width, height) > 4096 or width * height > MAX_PIXELS:
        raise ValueError("Frame must be at least 64px and at most 4096px / 10 megapixels")
    image = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (height, width):
        raise ValueError("Cannot decode the captured frame")
    return image


class LiveSessions:
    def __init__(self, store: Store, default_layout: Layout | None = None):
        self.store = store
        self.default_layout = default_layout or Layout()
        self.lock = threading.RLock()
        self.matcher: Matcher | None = None
        self.processors: dict[str, FrameProcessor] = {}
        self.results: dict[str, dict] = {}
        self.received_at: dict[str, float] = {}
        references = store.references()
        self.reference_names = {r["id"]: r["name"] for r in references}
        self.stackable = {r["id"] for r in references if json.loads(r["raw"]).get("stackable")}
        # Running capture cannot survive a server restart; retain evidence and require resume.
        self.shutdown()

    def _session(self, session_id: str) -> dict:
        with closing(self.store.connect()) as db:
            row = db.execute("SELECT * FROM recording WHERE id=?", (session_id,)).fetchone()
        if row is None or json.loads(row["metadata"]).get("kind") != "live":
            raise ValueError("Live session not found")
        return dict(row)

    def _metadata(self, session_id: str, metadata: dict, status: str | None = None) -> None:
        with closing(self.store.connect()) as db, db:
            if status:
                db.execute(
                    "UPDATE recording SET metadata=?,status=? WHERE id=?",
                    (json.dumps(metadata), status, session_id),
                )
            else:
                db.execute(
                    "UPDATE recording SET metadata=? WHERE id=?", (json.dumps(metadata), session_id)
                )

    def create(
        self,
        layout: Layout,
        additions: bool = False,
        name: str = "Live box scan",
        capture_crop: tuple[float, float, float, float] = (0, 0, 1, 1),
        snapshot: bool = True,
    ) -> dict:
        x1, y1, x2, y2 = capture_crop
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("Capture crop must be a normalized rectangle")
        with self.lock:
            session_id = str(uuid.uuid4())
            metadata = {
                "kind": "live",
                "name": name[:100],
                "last_sequence": -1,
                "last_timestamp": -1,
                "dimensions": None,
                "counts": {},
            }
            config = {
                "layout": layout.model_dump(mode="json"),
                "additions": additions,
                "pipeline": "live-1",
                "capture_crop": list(capture_crop),
            }
            if snapshot:
                config["snapshot"] = True
            with closing(self.store.connect()) as db, db:
                db.execute(
                    "INSERT INTO recording VALUES (?,?,?,?,?,?)",
                    (
                        session_id,
                        "live://browser-window",
                        json.dumps(metadata),
                        json.dumps(config),
                        "paused",
                        now(),
                    ),
                )
                if snapshot:
                    register_scan(db, session_id)
            return self.snapshot(session_id)

    def control(self, session_id: str, action: str, discontinuity: bool = False) -> dict:
        with self.lock:
            row = self._session(session_id)
            metadata = json.loads(row["metadata"])
            if action == "resume":
                if row["status"] == "complete":
                    raise ValueError("Finished session is read-only; start a new scan")
                if self.matcher is None:
                    self.matcher = Matcher(self.store.references())
                if session_id not in self.processors:
                    config = json.loads(row["config"])
                    self.processors[session_id] = FrameProcessor(
                        self.store,
                        session_id,
                        Layout.model_validate(config["layout"]),
                        self.matcher,
                        config["additions"],
                        resumed=metadata["last_sequence"] >= 0,
                    )
                    self.processors[session_id].counts.update(metadata.get("counts", {}))
                if discontinuity:
                    self.processors[session_id].break_continuity()
                self.processors[session_id].reconcile_map()
                # Only one session captures at once, even when two browser tabs are open.
                with closing(self.store.connect()) as db, db:
                    db.execute(
                        "UPDATE recording SET status='paused' WHERE id<>? AND status='running' "
                        "AND json_extract(metadata,'$.kind')='live'",
                        (session_id,),
                    )
                self._metadata(session_id, metadata, "running")
            elif action in {"pause", "finish"}:
                if row["status"] == "complete" and action == "pause":
                    return self.snapshot(session_id)
                self._metadata(session_id, metadata, "complete" if action == "finish" else "paused")
                if action == "finish":
                    self.processors.pop(session_id, None)
                    self.received_at.pop(session_id, None)
            else:
                raise ValueError("Unknown session action")
            return self.snapshot(session_id)

    def submit(self, session_id: str, sequence: int, timestamp: float, payload: bytes) -> dict:
        if sequence < 0 or not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("Invalid frame sequence or timestamp")
        with self.lock:
            row = self._session(session_id)
            metadata = json.loads(row["metadata"])
            # Network retry of a committed frame must never create a new copy.
            if sequence <= metadata["last_sequence"]:
                return self.snapshot(session_id)
            if row["status"] != "running" or session_id not in self.processors:
                raise ValueError("Session is paused; resume it before sending frames")
            if timestamp <= metadata["last_timestamp"]:
                raise ValueError("Frame timestamps must increase")
            image = decode_frame(payload)
            processor = self.processors[session_id]
            shape = list(image.shape[:2])
            if metadata["dimensions"] and shape != metadata["dimensions"]:
                processor.break_continuity()
                self._metadata(session_id, metadata, "paused")
                raise ValueError(
                    "Capture size changed. Restore the original crop/size or start a new "
                    "session; ingestion has been paused"
                )
            warnings = []
            if (
                session_id in self.received_at
                and time.monotonic() - self.received_at[session_id] > 5
            ):
                # Wall time includes browser rendering, image requests and upload delays.
                # Elapsed time alone says nothing about whether the box actually moved.
                # Keep the prior frame and let visual overlap/map evidence decide.
                warnings.append("Capture delayed; checking visual overlap with the previous view")
            if sequence != metadata["last_sequence"] + 1:
                processor.break_continuity()
                warnings.append("Dropped frame sequence: verify overlapping rows")
            started = time.monotonic()
            counts_before = dict(processor.counts)
            try:
                result = processor.process(Frame(sequence, timestamp, image))
            except Exception:
                processor.counts = counts_before
                processor.break_continuity()
                self._metadata(session_id, metadata, "paused")
                raise
            self.received_at[session_id] = time.monotonic()
            elapsed = self.received_at[session_id] - started
            result = {**result, "processing_seconds": round(elapsed, 3), "warnings": warnings}
            if elapsed > 1:
                warnings.append(
                    "Processing took over one second; scroll slowly and pause on each row"
                )
            # Evidence is already durable in SQLite. Checkpoint even blank/redundant frames.
            metadata.update(
                {
                    "last_sequence": sequence,
                    "last_timestamp": timestamp,
                    "dimensions": shape,
                    "counts": dict(processor.counts),
                }
            )
            self._metadata(session_id, metadata)
            self.results[session_id] = result
            return self.snapshot(session_id)

    def list(self) -> list[dict]:
        with closing(self.store.connect()) as db:
            rows = db.execute(
                "SELECT id FROM recording WHERE json_extract(metadata,'$.kind')='live' "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [self.snapshot(row["id"], include_entries=False) for row in rows]

    def _entry(self, row: dict) -> dict[str, Any]:
        mid = row["monster_id"] or row["proposed_id"]
        candidates = json.loads(row["candidates"] or "[]")
        return {
            "id": row["id"],
            "monster_id": row["monster_id"],
            "proposed_id": row["proposed_id"],
            "name": self.reference_names.get(mid, "Unmatched"),
            "status": row["status"],
            "tracking": row["tracking"],
            "confidence": row["confidence"],
            "high_confidence": bool(row["high_confidence"]),
            "stackable": mid in self.stackable,
            "quantity": row["quantity"],
            "quantity_verified": bool(row["quantity_verified"]),
            "timestamp": row["source_timestamp"],
            "tracking_reason": row["tracking_reason"],
            "crop_url": f"/api/crops/{row['id']}",
            "candidates": [
                {
                    "monster_id": c["monster_id"],
                    "name": c["name"],
                    "score": c["score"],
                    "icon_url": f"/api/icons/{c['monster_id']}",
                }
                for c in candidates
            ],
        }

    def snapshot(self, session_id: str, include_entries: bool = True) -> dict:
        with self.lock:
            row = self._session(session_id)
            metadata = json.loads(row["metadata"])
            rows = entries(self.store, session_id)
            active = [r for r in rows if r["status"] not in {"merged", "rejected"}]
            confirmed = [r for r in active if r["status"] == "confirmed"]
            with closing(self.store.connect()) as db:
                scan = db.execute(
                    "SELECT status FROM box_scan WHERE recording_id=?", (session_id,)
                ).fetchone()
                auto_ids = {
                    r[0]
                    for r in db.execute(
                        "SELECT inventory_id FROM review_event e WHERE action='auto_accept' "
                        "AND NOT EXISTS (SELECT 1 FROM review_event later "
                        "WHERE later.inventory_id=e.inventory_id AND later.id>e.id "
                        "AND later.action IN ('accept','batch_accept','reject'))"
                    )
                }
            result = self.results.get(session_id, {})
            box_map = read_map(self.store, session_id)
            if not include_entries:
                box_map = {key: value for key, value in box_map.items() if key != "slots"}
            return {
                "id": session_id,
                "name": metadata["name"],
                "status": row["status"],
                "snapshot_status": scan["status"] if scan else "legacy",
                "config": json.loads(row["config"]),
                "last_sequence": metadata["last_sequence"],
                "last_timestamp": metadata["last_timestamp"],
                "dimensions": metadata["dimensions"],
                "counts": metadata["counts"],
                "active_entries": len(active),
                "confirmed_entries": len(confirmed),
                "uncertain_entries": sum(r["status"] == "uncertain" for r in active),
                "unmatched_entries": sum(r["status"] == "unmatched" for r in active),
                "ambiguous_entries": sum(r["tracking"] == "ambiguous" for r in active),
                "entries": [{**self._entry(r), "auto_accepted": r["id"] in auto_ids} for r in rows]
                if include_entries
                else [],
                "result": result,
                "box_map": box_map,
            }

    def review(
        self,
        session_id: str,
        inventory_id: str,
        action: str,
        monster_id: int | None = None,
        distinct: bool = False,
        target_id: str = "",
        quantity: int | None = None,
    ) -> dict:
        with self.lock:
            row = next(
                (r for r in entries(self.store, session_id) if r["id"] == inventory_id), None
            )
            if row is None or row["recording_id"] != session_id:
                raise ValueError("Inventory entry is not in this session")
            if action == "accept" and monster_id is not None:
                accept(self.store, inventory_id, monster_id, distinct)
            elif action == "reject":
                reject(self.store, inventory_id)
            elif action == "merge":
                merge(self.store, inventory_id, target_id)
                # Do not append new observations to a merged-away track.
                if session_id in self.processors:
                    self.processors[session_id].break_continuity()
            elif action == "quantity" and quantity is not None:
                set_entry_quantity(self.store, inventory_id, quantity)
            else:
                raise ValueError("Unknown review action or missing identity")
            return self.snapshot(session_id)

    def image_path(self, kind: str, value: str) -> Path:
        if kind == "icons":
            self.store.require_monster(int(value))
            reference = next(r for r in self.store.references(value) if r["id"] == int(value))
            path = reference.get("icon")
        else:
            # Crop requests are frequent during capture. Reading every recording's
            # evidence for each thumbnail made large scans stall for seconds.
            row = next(iter(entries(self.store, inventory_id=value)), None)
            path = row.get("crop") if row else None
        if not path or not Path(path).is_file():
            raise ValueError("Image not available locally")
        return Path(path)

    def shutdown(self) -> None:
        with self.lock, closing(self.store.connect()) as db, db:
            db.execute(
                "UPDATE recording SET status='paused' WHERE status='running' "
                "AND json_extract(metadata,'$.kind')='live'"
            )
