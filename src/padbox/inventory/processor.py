"""Incremental frame ingestion shared by file imports and live capture."""

import json
import logging
import uuid
from contextlib import closing
from typing import Any

import cv2
import numpy as np

from padbox.database.store import Store, now
from padbox.detection.grid import annotate, detect
from padbox.inventory.box_map import BoxMap
from padbox.inventory.snapshots import auto_accept_strong, is_scan
from padbox.inventory.tracking import Alignment, align
from padbox.models import Layout
from padbox.recognition.matcher import MIN_CANDIDATE_SCORE, Matcher, appearance_similarity
from padbox.video.reader import Frame

LOG = logging.getLogger(__name__)


class FrameProcessor:
    def __init__(
        self,
        store: Store,
        recording_id: str,
        layout: Layout,
        matcher: Matcher,
        additions: bool = False,
        resumed: bool = False,
    ):
        self.store = store
        self.recording_id = recording_id
        self.layout = layout
        self.matcher = matcher
        self.additions = additions
        self.snapshot_mode = is_scan(store, recording_id)
        self.directory = store.home / "recordings" / recording_id
        self.directory.mkdir(parents=True, exist_ok=True)
        with closing(store.connect()) as db:
            self.previous_collection = db.execute(
                """SELECT COUNT(*) FROM owned WHERE (recording_id IS NULL OR recording_id<>?)
                   AND status NOT IN ('rejected','merged')""",
                (recording_id,),
            ).fetchone()[0]
        self.previous_cells, self.previous_ids = [], []
        self.previous_thumb = None
        self.previous_image = None
        self.scroll_offset = 0.0
        self.history: dict = {}
        self.box_map = BoxMap(store, recording_id)
        self.continuity_broken = False
        self.counts = {
            "sampled_frames": 0,
            "redundant_frames": 0,
            "no_grid_frames": 0,
            "observations": 0,
            "new_entries": 0,
            "ambiguous_entries": 0,
        }
        self.continuity_broken = resumed
        self.last_result: dict[str, Any] = {
            "cells": [],
            "status": "waiting",
            "message": "Waiting for first frame",
        }

    def break_continuity(self) -> None:
        self.previous_cells, self.previous_ids = [], []
        self.previous_thumb = self.previous_image = None
        self.history = {}
        self.scroll_offset = 0.0
        self.continuity_broken = True
        # Reload only durable positions: a failed frame may have mutated the in-memory
        # map before its SQLite transaction rolled back.
        self.box_map = BoxMap(self.store, self.recording_id)

    def _result(self, cells: list[dict], status: str, message: str) -> dict:
        self.last_result = {
            "cells": cells,
            "status": status,
            "message": message,
            "counts": dict(self.counts),
        }
        return self.last_result

    def reconcile_map(self) -> int:
        """Resume can repair saved fragments without capturing their overlap again."""
        self.box_map.refresh()
        try:
            with closing(self.store.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                aliases, joined = self.box_map.reconcile(db)
        except Exception:
            self.break_continuity()
            raise
        if joined or aliases:
            self.previous_ids = [aliases.get(key, key) for key in self.previous_ids]
            self.scroll_offset = self.box_map.state["offset"]
            self.history = self.box_map.history()
            for cell in self.last_result.get("cells", []):
                key = cell["inventory_id"]
                cell["inventory_id"] = aliases.get(key, key)
            auto_accept_strong(self.store, self.recording_id)
        return joined

    def process(self, frame: Frame) -> dict:
        self.counts["sampled_frames"] += 1
        thumb = cv2.resize(frame.image, (96, 160)).astype(np.float32)
        # Very conservative threshold; exact repeated views provide no new evidence.
        if (
            self.previous_thumb is not None
            and float(np.mean(np.abs(thumb - self.previous_thumb))) < 0.12
        ):
            self.counts["redundant_frames"] += 1
            return self._result(
                self.last_result.get("cells", []),
                "no_grid" if self.last_result.get("status") == "no_grid" else "ok",
                self.last_result["message"]
                if self.last_result.get("status") == "no_grid"
                else "Unchanged view; no additional observations saved",
            )
        self.previous_thumb = thumb
        cells = detect(frame.image, self.layout)
        cv2.imwrite(
            str(self.directory / f"frame-{frame.index:07}-grid.jpg"), annotate(frame.image, cells)
        )
        if not cells:
            LOG.warning("No grid at %.3fs; see debug frame %s", frame.timestamp, frame.index)
            self.counts["no_grid_frames"] += 1
            self.previous_cells, self.previous_ids = [], []
            self.previous_image = None
            self.continuity_broken = True
            self.history = {}
            self.scroll_offset = 0.0
            with closing(self.store.connect()) as db, db:
                if self.box_map.state["viewport"]:
                    self.box_map.state["last_viewport"] = self.box_map.state["viewport"]
                self.box_map.state.update(viewport=None, position="no_grid", direction="unknown")
                self.box_map.save(db)
            return self._result([], "no_grid", "No box grid detected; check crop/layout")
        self.box_map.refresh()
        alignment = align(self.previous_cells, cells, self.previous_image, frame.image)
        relocated_links: dict[int, str] = {}
        relocated = False
        if self.continuity_broken or not alignment.certain or not self.previous_cells:
            location = self.box_map.locate(cells)
            if location:
                self.box_map.state["active_segment"] = location.segment
                self.scroll_offset = location.offset
                self.history = self.box_map.history()
                relocated_links = location.links
                relocated = True
                self.continuity_broken = False
                alignment = Alignment({}, True, location.reason)
            else:
                rows = sorted({cell.y for cell in cells})
                pitch = (
                    self.layout.step_y * frame.image.shape[0]
                    if self.layout.step_y
                    else float(np.median(np.diff(rows)))
                    if len(rows) > 1
                    else float(cells[0].height) * 1.15
                )
                had_map = bool(self.box_map.slots)
                self.box_map.start_segment(pitch, uncertain=had_map or self.continuity_broken)
                self.continuity_broken = had_map or self.continuity_broken
                self.history = {}
                self.scroll_offset = 0.0
                alignment = Alignment(
                    {},
                    not self.continuity_broken,
                    "unplaced map section; review copy counts"
                    if self.continuity_broken
                    else "first visible grid",
                )
        elif alignment.certain and alignment.links:
            self.scroll_offset += float(
                np.median(
                    [cells[i].y - self.previous_cells[j].y for i, j in alignment.links.items()]
                )
            )
        # Continuing within an unplaced fragment must also look for a bridge back
        # to another section. Otherwise a fragment can never lose its uncertainty.
        join_location = None
        if alignment.certain and self.previous_cells and self.box_map.uncertain:
            join_location = self.box_map.locate(
                cells, exclude_segments={self.box_map.state["active_segment"]}
            )
        current_ids = []
        display_cells = []
        new_before = self.counts["new_entries"]
        with closing(self.store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if join_location:
                merged = self.box_map.join(db, join_location, self.scroll_offset)
                if merged is not None:
                    self.previous_ids = [merged.get(key, key) for key in self.previous_ids]
                    self.scroll_offset = join_location.offset
                    self.history = self.box_map.history()
                    relocated_links = join_location.links
                    self.continuity_broken = False
                    relocated = True
                    alignment.reason = "Joined map sections; " + join_location.reason
            ambiguous = (
                not alignment.certain
                or self.continuity_broken
                or self.box_map.uncertain
                or (
                    bool(self.previous_collection) and not self.additions and not self.snapshot_mode
                )
            )
            if ambiguous:
                LOG.warning("Slot ambiguity at %.3fs: %s", frame.timestamp, alignment.reason)
            for i, cell in enumerate(cells):
                observation_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{self.recording_id}:{frame.index}:{cell.rect}")
                )
                saved = db.execute(
                    "SELECT matched_inventory_id FROM observation WHERE id=?", (observation_id,)
                ).fetchone()
                if saved:
                    current_ids.append(saved[0])
                    continue
                self.counts["observations"] += 1
                crop = self.directory / f"{observation_id}.png"
                if not cv2.imwrite(str(crop), cell.image):
                    raise OSError(f"Failed writing crop {crop}")
                match = self.matcher.match(cell.image)
                best = match.candidates[0] if match.candidates else None
                plausible = best is not None and best.score >= MIN_CANDIDATE_SCORE
                global_y = cell.y - self.scroll_offset
                past = [
                    (key, prior)
                    for key, (prior, position) in self.history.items()
                    if prior.column == cell.column and abs(position - global_y) < cell.height * 0.16
                ]
                reappeared = [
                    key
                    for key, prior in past
                    if appearance_similarity(prior.image, cell.image) >= 0.945
                ]
                if len(past) == 1 and alignment.certain and not self.continuity_broken:
                    # Row consensus has already established this translation. An
                    # animated overlay on one returning card cannot create a new
                    # inventory copy at its uniquely known map position.
                    reappeared = [past[0][0]]
                cell_ambiguous = ambiguous or bool(past and len(reappeared) != 1)
                if i in relocated_links:
                    inventory_id = relocated_links[i]
                elif i in alignment.links:
                    inventory_id = self.previous_ids[alignment.links[i]]
                elif len(reappeared) == 1:
                    inventory_id = reappeared[0]
                else:
                    inventory_id = str(uuid.uuid5(uuid.NAMESPACE_OID, observation_id))
                    self.counts["new_entries"] += 1
                    self.counts["ambiguous_entries"] += int(cell_ambiguous)
                    db.execute(
                        """INSERT INTO owned(id,proposed_id,status,tracking,confidence,
                     high_confidence,recording_id,created_at) VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            inventory_id,
                            best.monster_id if plausible else None,
                            "uncertain" if plausible else "unmatched",
                            "ambiguous" if cell_ambiguous else "clear",
                            best.score if best else 0,
                            int(match.high_confidence),
                            self.recording_id,
                            now(),
                        ),
                    )
                if best:
                    # Better unobscured evidence may arrive later; human decisions win.
                    db.execute(
                        """UPDATE owned SET proposed_id=?,confidence=?,
                         high_confidence=?,status=?
                         WHERE id=? AND confidence<? AND status IN ('uncertain','unmatched')
                         AND NOT EXISTS (SELECT 1 FROM review_event r
                                         WHERE r.inventory_id=owned.id AND r.action<>'map_join')
                        """,
                        (
                            best.monster_id if plausible else None,
                            best.score,
                            int(match.high_confidence),
                            "uncertain" if plausible else "unmatched",
                            inventory_id,
                            best.score,
                        ),
                    )
                reason = alignment.reason
                if past and len(reappeared) != 1:
                    reason += "; conflicting historical slot appearances"
                if self.previous_collection and not self.additions and not self.snapshot_mode:
                    reason += "; possible overlap with earlier collection"
                if self.continuity_broken:
                    reason += "; visual continuity interrupted"
                if self.box_map.uncertain:
                    reason += "; unjoined map section"
                db.execute(
                    "INSERT INTO observation VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        observation_id,
                        self.recording_id,
                        frame.timestamp,
                        frame.index,
                        json.dumps(cell.rect),
                        str(crop),
                        json.dumps([c.model_dump() for c in match.candidates]),
                        inventory_id,
                        match.reason,
                        reason,
                    ),
                )
                self.box_map.record(db, inventory_id, cell, global_y, observation_id)
                current_ids.append(inventory_id)
            # Reconcile the complete saved map whenever new positions arrive.
            # Old overlaps may be far outside the current viewport.
            joined = 0
            if self.counts["new_entries"] > new_before and len(self.box_map.state["segments"]) > 1:
                self.box_map.state["offset"] = self.scroll_offset
                aliases, joined = self.box_map.reconcile(db)
                if joined or aliases:
                    current_ids = [aliases.get(key, key) for key in current_ids]
                    self.previous_ids = [aliases.get(key, key) for key in self.previous_ids]
                    self.scroll_offset = self.box_map.state["offset"]
                    self.history = self.box_map.history()
                    relocated = True
                    if not self.box_map.uncertain:
                        self.continuity_broken = False
                        ambiguous = False
                        alignment = Alignment(
                            {},
                            True,
                            f"Joined {joined} saved map sections"
                            if joined
                            else "Reconciled repeated map positions",
                        )
            self.box_map.checkpoint(
                db,
                self.scroll_offset,
                self.layout.roi[1] * frame.image.shape[0],
                self.layout.roi[3] * frame.image.shape[0],
                self.counts["new_entries"] - new_before,
                relocated,
            )
        auto_accept_strong(self.store, self.recording_id)
        for cell, inventory_id in zip(cells, current_ids, strict=True):
            self.history[inventory_id] = (cell, cell.y - self.scroll_offset)
        self.previous_cells, self.previous_ids = cells, current_ids
        self.previous_image = frame.image
        was_broken = self.continuity_broken
        self.continuity_broken = False
        display_cells = [
            {"inventory_id": inventory_id, "rect": cell.rect}
            for cell, inventory_id in zip(cells, current_ids, strict=True)
        ]
        warnings = []
        if not alignment.certain or was_broken:
            warnings.append(alignment.reason)
        if ambiguous:
            warnings.append("Slot identity or overlap with an earlier collection needs review")
        return self._result(
            display_cells,
            "tracking_warning" if not alignment.certain or was_broken else "ok",
            "; ".join(warnings) or alignment.reason,
        )
