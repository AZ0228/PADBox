"""Persistent per-scan spatial map and conservative multi-row relocalization."""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass

import cv2
import numpy as np

from padbox.database.store import Store, now
from padbox.detection.grid import Cell
from padbox.inventory.consensus import ANCHOR_SCORE, LOCATION_MARGIN, context_agrees, row_consensus
from padbox.recognition.matcher import appearance_similarity, features


def empty_state() -> dict:
    return {
        "segments": [],
        "active_segment": None,
        "offset": 0.0,
        "viewport": None,
        "direction": "unknown",
        "position": "waiting",
    }


def _payload(state: dict, slots: list[dict]) -> dict:
    segments = []
    for segment in state["segments"]:
        group = [s for s in slots if s["segment"] == segment["id"]]
        if not group:
            continue
        lower = min(s["world_y"] for s in group)
        upper = max(s["world_y"] + s["rect"][3] for s in group)
        viewport = state["viewport"]
        if viewport and viewport["segment"] == segment["id"]:
            lower, upper = min(lower, viewport["top"]), max(upper, viewport["bottom"])
        pitch = segment["row_pitch"]
        segments.append(
            {
                **segment,
                "min_y": lower,
                "max_y": upper,
                "rows": len({round(s["world_y"] / pitch) for s in group}),
            }
        )
    return {
        "version": 1,
        "segments": segments,
        "slots": slots,
        "viewport": state["viewport"],
        "direction": state["direction"],
        "position": state["position"],
        "known_slots": len(slots),
    }


def read_map(store: Store, recording_id: str) -> dict:
    """Read UI state without loading recognition templates into memory."""
    with closing(store.connect()) as db:
        row = db.execute(
            "SELECT document FROM scan_map_state WHERE recording_id=?", (recording_id,)
        ).fetchone()
        slots = [
            {
                "inventory_id": r["inventory_id"],
                "segment": r["segment"],
                "column": r["column_index"],
                "world_y": r["world_y"],
                "rect": json.loads(r["rect"]),
            }
            for r in db.execute(
                "SELECT m.* FROM scan_map_slot m JOIN owned o ON o.id=m.inventory_id "
                "WHERE m.recording_id=? AND o.status NOT IN ('merged','rejected') "
                "ORDER BY m.segment,m.world_y,m.column_index",
                (recording_id,),
            )
        ]
    return _payload(json.loads(row[0]) if row else empty_state(), slots)


@dataclass
class MapSlot:
    inventory_id: str
    segment: int
    cell: Cell
    world_y: float
    hash: np.ndarray
    latest_image: np.ndarray | None = None
    latest_hash: np.ndarray | None = None

    def similarity(self, image: np.ndarray) -> float:
        score = appearance_similarity(image, self.cell.image)
        if self.latest_image is not None:
            score = max(score, appearance_similarity(image, self.latest_image))
        return score


@dataclass
class MapLocation:
    segment: int
    offset: float
    links: dict[int, str]
    reason: str


class BoxMap:
    def __init__(self, store: Store, recording_id: str):
        self.store, self.recording_id = store, recording_id
        self.slots: dict[str, MapSlot] = {}
        with closing(store.connect()) as db:
            state = db.execute(
                "SELECT document FROM scan_map_state WHERE recording_id=?", (recording_id,)
            ).fetchone()
            self.state = json.loads(state[0]) if state else empty_state()
            rows = db.execute(
                "SELECT m.*,v.crop,(SELECT first.crop FROM observation first "
                "WHERE first.matched_inventory_id=m.inventory_id ORDER BY first.rowid LIMIT 1) "
                "AS first_crop FROM scan_map_slot m JOIN observation v "
                "ON v.id=m.observation_id JOIN owned o ON o.id=m.inventory_id "
                "WHERE m.recording_id=? AND o.status NOT IN ('merged','rejected')",
                (recording_id,),
            ).fetchall()
        for row in rows:
            image = cv2.imread(row["crop"])
            first_image = cv2.imread(row["first_crop"]) if row["first_crop"] else None
            if image is None and first_image is None:
                continue  # A missing template cannot provide relocation evidence.
            first_image = first_image if first_image is not None else image
            image = image if image is not None else first_image
            x, y, w, h = json.loads(row["rect"])
            cell = Cell(
                x,
                y,
                w,
                h,
                row["column_index"],
                cv2.resize(first_image, (64, 64), interpolation=cv2.INTER_AREA),
            )
            latest = cv2.resize(image, (64, 64), interpolation=cv2.INTER_AREA)
            self.slots[row["inventory_id"]] = MapSlot(
                row["inventory_id"],
                row["segment"],
                cell,
                row["world_y"],
                features(cell.image).hash,
                latest,
                features(latest).hash,
            )

    def refresh(self) -> None:
        with closing(self.store.connect()) as db:
            active = {
                r[0]
                for r in db.execute(
                    "SELECT id FROM owned WHERE recording_id=? "
                    "AND status NOT IN ('merged','rejected')",
                    (self.recording_id,),
                )
            }
        self.slots = {key: slot for key, slot in self.slots.items() if key in active}

    def start_segment(self, row_pitch: float, uncertain: bool) -> None:
        segment = max((s["id"] for s in self.state["segments"]), default=-1) + 1
        self.state["segments"].append(
            {"id": segment, "row_pitch": row_pitch, "uncertain": uncertain}
        )
        self.state.update(
            active_segment=segment,
            offset=0.0,
            direction="unknown",
            viewport=None,
            position="unlocated" if uncertain else "new",
        )

    def history(self) -> dict[str, tuple[Cell, float]]:
        return {
            key: (slot.cell, slot.world_y)
            for key, slot in self.slots.items()
            if slot.segment == self.state["active_segment"]
        }

    @property
    def uncertain(self) -> bool:
        return next(
            (
                s["uncertain"]
                for s in self.state["segments"]
                if s["id"] == self.state["active_segment"]
            ),
            True,
        )

    def locate(
        self,
        current: list[Cell],
        exclude_segments: set[int] | None = None,
        alternatives: dict[int, np.ndarray] | None = None,
    ) -> MapLocation | None:
        """Direction orders hypotheses; artwork/geometry must establish a unique place.

        One distinctive ordered row can suffice. Alternative observed appearances
        support saved-fragment reconciliation despite changing overlays.
        """
        eligible = [s for s in self.slots.values() if s.segment not in (exclude_segments or set())]
        if len(current) < 4 or len(eligible) < 4:
            return None
        size = float(np.median([c.width for c in current]))
        cache: dict[tuple[int, str], float] = {}
        hypotheses: dict[tuple[int, int], float] = {}

        def similarity(i: int, slot: MapSlot) -> float:
            key = (i, slot.inventory_id)
            if key not in cache:
                score = slot.similarity(current[i].image)
                if alternatives and i in alternatives:
                    score = max(score, slot.similarity(alternatives[i]))
                cache[key] = score
            return cache[key]

        for i, cell in enumerate(current):
            candidates = [
                s
                for s in eligible
                if s.cell.column == cell.column and abs(s.cell.x - cell.x) < size * 0.15
            ]
            if not candidates:
                continue
            hashes = np.array([s.hash for s in candidates])
            query_hash = features(cell.image).hash
            latest_hashes = np.array(
                [s.latest_hash if s.latest_hash is not None else s.hash for s in candidates]
            )
            scores = np.maximum(
                np.mean(hashes == query_hash, axis=1), np.mean(latest_hashes == query_hash, axis=1)
            )
            if alternatives and i in alternatives:
                alternative_hash = features(alternatives[i]).hash
                scores = np.maximum(
                    scores,
                    np.maximum(
                        np.mean(hashes == alternative_hash, axis=1),
                        np.mean(latest_hashes == alternative_hash, axis=1),
                    ),
                )
            # Retrieval only proposes offsets. Every overlapping position is then verified.
            for index in np.argsort(-scores, kind="stable")[:12]:
                if scores[index] < 0.70:
                    continue
                slot = candidates[index]
                if similarity(i, slot) >= ANCHOR_SCORE:
                    offset = cell.y - slot.world_y
                    hypotheses[slot.segment, round(offset / (size * 0.10))] = offset
        possibilities = []
        seen = set()
        for (segment, _), offset in hypotheses.items():
            links: dict[int, str] = {}
            pairs = []
            for i, cell in enumerate(current):
                candidates = [
                    s
                    for s in eligible
                    if s.segment == segment
                    and s.cell.column == cell.column
                    and abs(s.cell.x - cell.x) < size * 0.15
                    and abs(s.world_y + offset - cell.y) < size * 0.13
                ]
                if len(candidates) != 1:
                    continue
                slot = candidates[0]
                score = similarity(i, slot)
                links[i] = slot.inventory_id
                pairs.append((cell, score))
            signature = tuple(sorted(links.items()))
            if signature in seen:
                continue
            seen.add(signature)
            evidence = row_consensus(pairs)
            if evidence is None:
                continue
            offset = float(
                np.median([current[i].y - self.slots[key].world_y for i, key in links.items()])
            )
            possibilities.append((evidence.score, evidence.anchors, segment, offset, links))
        # Never let direction break an appearance tie between repeated map sections.
        direction = self.state["direction"]

        def direction_cost(candidate: tuple) -> float:
            delta = self.state["offset"] - candidate[3]
            return -delta if direction == "down" else delta if direction == "up" else abs(delta)

        possibilities.sort(key=lambda p: (-p[0], -p[1], direction_cost(p)))
        if not possibilities or (
            len(possibilities) > 1 and possibilities[0][0] - possibilities[1][0] < LOCATION_MARGIN
        ):
            return None
        score, anchors, segment, offset, links = possibilities[0]
        return MapLocation(
            segment,
            offset,
            links,
            f"map relocalization: {anchors} anchors in ordered grid overlap, {score:.3f}",
        )

    @staticmethod
    def _merge_allowed(db: sqlite3.Connection, source: str, target: str) -> bool:
        rows = [
            db.execute("SELECT * FROM owned WHERE id=?", (key,)).fetchone()
            for key in (source, target)
        ]
        if any(r is None or r["status"] in {"merged", "rejected"} for r in rows):
            return False
        if rows[0]["recording_id"] != rows[1]["recording_id"]:
            return False
        reviewed = db.execute(
            "SELECT 1 FROM review_event WHERE inventory_id=? "
            "AND action NOT IN ('auto_accept','map_join') LIMIT 1",
            (source,),
        ).fetchone()
        if reviewed or (
            rows[0]["monster_id"] is not None
            and rows[1]["monster_id"] is not None
            and rows[0]["monster_id"] != rows[1]["monster_id"]
        ):
            return False
        simultaneous = db.execute(
            "WITH RECURSIVE a(id) AS (SELECT ? UNION ALL SELECT o.id FROM owned o "
            "JOIN a ON o.merged_into=a.id), b(id) AS (SELECT ? UNION ALL "
            "SELECT o.id FROM owned o JOIN b ON o.merged_into=b.id) "
            "SELECT 1 FROM observation x JOIN observation y "
            "ON x.recording_id=y.recording_id AND x.frame_index=y.frame_index "
            "WHERE x.matched_inventory_id IN (SELECT id FROM a) "
            "AND y.matched_inventory_id IN (SELECT id FROM b) LIMIT 1",
            (source, target),
        ).fetchone()
        return not simultaneous

    def _deduplicate_positions(self, db: sqlite3.Connection) -> dict[str, str]:
        """Repair repeated rows that the old tracker inserted at identical positions."""
        priorities = {
            r["id"]: r["first_seen"] or 0
            for r in db.execute(
                "SELECT o.id,"
                "(SELECT MIN(rowid) FROM observation v WHERE v.matched_inventory_id=o.id) "
                "AS first_seen "
                "FROM owned o WHERE o.recording_id=?",
                (self.recording_id,),
            )
        }
        aliases: dict[str, str] = {}
        for segment in self.state["segments"]:
            rows: list[list[MapSlot]] = []
            for slot in sorted(
                (s for s in self.slots.values() if s.segment == segment["id"]),
                key=lambda s: s.world_y,
            ):
                if not rows or abs(rows[-1][0].world_y - slot.world_y) > slot.cell.height * 0.13:
                    rows.append([])
                rows[-1].append(slot)
            for row in rows:
                pairs = []
                singletons = []
                for column in sorted({s.cell.column for s in row}):
                    group = sorted(
                        (s for s in row if s.cell.column == column),
                        key=lambda s: priorities[s.inventory_id],
                    )
                    target = group[0]
                    if len(group) == 1:
                        singletons.append(target)
                    for source in group[1:]:
                        if abs(source.cell.x - target.cell.x) > source.cell.width * 0.15:
                            continue
                        score = target.similarity(source.cell.image)
                        if source.latest_image is not None:
                            score = max(score, target.similarity(source.latest_image))
                        pairs.append((source, target, score))
                evidence = [(s.cell, score) for s, _, score in pairs]
                if pairs and row_consensus(evidence) is None:
                    # A single duplicated card can be resolved by stable neighbors
                    # that were tracked alongside *both* appearances in the same
                    # screen row. Include merged descendants when reading evidence.
                    for neighbor in singletons:
                        for source, target, _ in pairs:
                            if all(
                                self._observed_row_neighbors(
                                    db,
                                    slot.inventory_id,
                                    neighbor.inventory_id,
                                    slot.cell.height * 0.13,
                                )
                                for slot in (source, target)
                            ):
                                evidence.append((neighbor.cell, 1.0))
                                break
                if row_consensus(evidence) is None:
                    continue
                for source, target, _ in pairs:
                    if not self._merge_allowed(db, source.inventory_id, target.inventory_id):
                        continue
                    db.execute(
                        "UPDATE owned SET status='merged',merged_into=? WHERE id=?",
                        (target.inventory_id, source.inventory_id),
                    )
                    db.execute(
                        "DELETE FROM scan_map_slot WHERE inventory_id=?", (source.inventory_id,)
                    )
                    db.execute(
                        "INSERT INTO review_event(inventory_id,action,detail,created_at) "
                        "VALUES (?,?,?,?)",
                        (
                            source.inventory_id,
                            "map_join",
                            json.dumps(
                                {
                                    "target_id": target.inventory_id,
                                    "source_segment": segment["id"],
                                    "target_segment": segment["id"],
                                    "evidence": "ordered row at the same map positions",
                                }
                            ),
                            now(),
                        ),
                    )
                    aliases[source.inventory_id] = target.inventory_id
                    del self.slots[source.inventory_id]
        return aliases

    @staticmethod
    def _observed_row_neighbors(
        db: sqlite3.Connection, inventory_id: str, neighbor: str, tolerance: float
    ) -> bool:
        return (
            db.execute(
                "WITH RECURSIVE a(id) AS (SELECT ? UNION ALL SELECT o.id FROM owned o "
                "JOIN a ON o.merged_into=a.id), b(id) AS (SELECT ? UNION ALL "
                "SELECT o.id FROM owned o JOIN b ON o.merged_into=b.id) "
                "SELECT 1 FROM observation x JOIN observation y "
                "ON x.recording_id=y.recording_id AND x.frame_index=y.frame_index "
                "WHERE x.matched_inventory_id IN (SELECT id FROM a) "
                "AND y.matched_inventory_id IN (SELECT id FROM b) "
                "AND ABS(json_extract(x.rect,'$[1]')-json_extract(y.rect,'$[1]'))<? LIMIT 1",
                (inventory_id, neighbor, tolerance),
            ).fetchone()
            is not None
        )

    def join(
        self, db: sqlite3.Connection, location: MapLocation, source_offset: float
    ) -> dict[str, str] | None:
        """Join an unplaced section through a uniquely located, continuous viewport.

        Validate the *entire* overlap before mutating anything. Corresponding slots
        merge by position and artwork, never by monster ID. Reviewed source copies,
        conflicting artwork and slots ever seen together block the whole join.
        Evidence/decisions remain attached to their original IDs for auditability.
        """
        source = self.state["active_segment"]
        if source == location.segment or not self.uncertain:
            return None
        segments = {s["id"]: s for s in self.state["segments"]}
        pitch = segments[source]["row_pitch"]
        if abs(pitch - segments[location.segment]["row_pitch"]) > pitch * 0.08:
            return None
        shift = source_offset - location.offset
        source_slots = [s for s in self.slots.values() if s.segment == source]
        target_slots = [s for s in self.slots.values() if s.segment == location.segment]
        links: dict[str, str] = {}
        overlap_scores = []
        for slot in source_slots:
            candidates = [
                t
                for t in target_slots
                if t.cell.column == slot.cell.column
                and abs(t.cell.x - slot.cell.x) < slot.cell.width * 0.15
                and abs(t.world_y - (slot.world_y + shift)) < slot.cell.height * 0.13
            ]
            if not candidates:
                continue
            ranked = []
            for candidate in candidates:
                score = candidate.similarity(slot.cell.image)
                if slot.latest_image is not None:
                    score = max(score, candidate.similarity(slot.latest_image))
                ranked.append((score, candidate))
            ranked.sort(key=lambda pair: -pair[0])
            if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < LOCATION_MARGIN:
                return None
            score, target = ranked[0]
            overlap_scores.append(score)
            if target.inventory_id in links.values():
                return None
            if not self._merge_allowed(db, slot.inventory_id, target.inventory_id):
                return None
            links[slot.inventory_id] = target.inventory_id
        # The current viewport is the bridge: some target anchors may be rows
        # entering the viewport for the first time in the source section.
        if len(location.links) < 4 or not context_agrees(overlap_scores):
            return None
        # A join can only mutate a live draft; an applied snapshot is immutable.
        scan = db.execute(
            "SELECT status FROM box_scan WHERE recording_id=?", (self.recording_id,)
        ).fetchone()
        if not scan or scan[0] != "draft":
            return None
        target_uncertain = segments[location.segment]["uncertain"]
        for slot in source_slots:
            target_id = links.get(slot.inventory_id)
            if target_id:
                db.execute(
                    "UPDATE owned SET status='merged',merged_into=? WHERE id=?",
                    (target_id, slot.inventory_id),
                )
                db.execute("DELETE FROM scan_map_slot WHERE inventory_id=?", (slot.inventory_id,))
                del self.slots[slot.inventory_id]
            else:
                slot.segment = location.segment
                slot.world_y += shift
                db.execute(
                    "UPDATE scan_map_slot SET segment=?,world_y=? WHERE inventory_id=?",
                    (slot.segment, slot.world_y, slot.inventory_id),
                )
                if not target_uncertain:
                    # Only section-placement uncertainty is resolved by this join.
                    first = db.execute(
                        "SELECT tracking_reason FROM observation WHERE matched_inventory_id=? "
                        "ORDER BY rowid LIMIT 1",
                        (slot.inventory_id,),
                    ).fetchone()
                    manual = db.execute(
                        "SELECT 1 FROM review_event WHERE inventory_id=? "
                        "AND action NOT IN ('auto_accept','map_join') LIMIT 1",
                        (slot.inventory_id,),
                    ).fetchone()
                    reason = first[0] if first else ""
                    if (
                        not manual
                        and reason
                        and not any(
                            conflict in reason
                            for conflict in (
                                "conflicting historical",
                                "overlap with earlier collection",
                            )
                        )
                        and reason.startswith(
                            (
                                "unplaced map section",
                                "unique vertical offset",
                                "forward/backward optical flow",
                                "map relocalization",
                                "Joined",
                            )
                        )
                    ):
                        db.execute(
                            "UPDATE owned SET tracking='clear' WHERE id=?", (slot.inventory_id,)
                        )
            db.execute(
                "INSERT INTO review_event(inventory_id,action,detail,created_at) VALUES (?,?,?,?)",
                (
                    slot.inventory_id,
                    "map_join",
                    json.dumps(
                        {
                            "source_segment": source,
                            "target_segment": location.segment,
                            "target_id": target_id,
                            "world_shift": shift,
                            "evidence": location.reason,
                        }
                    ),
                    now(),
                ),
            )
        self.state["segments"] = [s for s in self.state["segments"] if s["id"] != source]
        for key in ("viewport", "last_viewport"):
            viewport = self.state.get(key)
            if viewport and viewport["segment"] == source:
                self.state[key] = {
                    "segment": location.segment,
                    "top": viewport["top"] + shift,
                    "bottom": viewport["bottom"] + shift,
                }
        self.state.update(active_segment=location.segment, offset=location.offset)
        return links

    def reconcile(self, db: sqlite3.Connection) -> tuple[dict[str, str], int]:
        """Stitch saved sections even when their overlapping rows are off screen.

        Prefer the anchored map, then older fragments. Each pair must have a unique
        ordered placement within that target and pass full-overlap join checks.
        Overlapping fragments describing the same place are consolidated in turn;
        they no longer veto each other as competing relocalization destinations.
        """
        scan = db.execute(
            "SELECT status FROM box_scan WHERE recording_id=?", (self.recording_id,)
        ).fetchone()
        if not scan or scan[0] != "draft":
            return {}, 0
        aliases = self._deduplicate_positions(db)
        joined = 0
        progress = True
        while progress:
            progress = False
            for source in list(self.state["segments"]):
                if not source["uncertain"]:
                    continue
                source_slots = sorted(
                    (s for s in self.slots.values() if s.segment == source["id"]),
                    key=lambda s: (s.world_y, s.cell.column),
                )
                if len(source_slots) < 4:
                    continue
                query = [
                    Cell(
                        s.cell.x,
                        round(s.world_y),
                        s.cell.width,
                        s.cell.height,
                        s.cell.column,
                        s.cell.image,
                    )
                    for s in source_slots
                ]
                alternatives = {
                    i: s.latest_image
                    for i, s in enumerate(source_slots)
                    if s.latest_image is not None
                }
                targets = sorted(
                    (
                        t
                        for t in self.state["segments"]
                        if t["id"] != source["id"]
                        and (not t["uncertain"] or t["id"] < source["id"])
                    ),
                    key=lambda t: (t["uncertain"], t["id"]),
                )
                for target in targets:
                    excluded = {s["id"] for s in self.state["segments"] if s["id"] != target["id"]}
                    location = self.locate(query, excluded, alternatives)
                    if location is None:
                        continue
                    active, offset = self.state["active_segment"], self.state["offset"]
                    self.state["active_segment"] = source["id"]
                    merged = self.join(db, location, source_offset=0.0)
                    if merged is None:
                        self.state["active_segment"] = active
                        continue
                    for key, value in list(aliases.items()):
                        aliases[key] = merged.get(value, value)
                    aliases.update(merged)
                    # join() shifted saved viewports; preserve the physical capture's
                    # offset, which need not be the source query's virtual offset.
                    self.state.update(
                        active_segment=location.segment if active == source["id"] else active,
                        offset=offset + location.offset if active == source["id"] else offset,
                    )
                    joined += 1
                    progress = True
                    break
                if progress:
                    break  # Rebuild candidates after every committed-in-transaction join.
        if joined or aliases:
            self.state["position"] = "relocalized" if not self.uncertain else "unlocated"
            self.save(db)
        return aliases, joined

    def record(
        self,
        db: sqlite3.Connection,
        inventory_id: str,
        cell: Cell,
        world_y: float,
        observation_id: str,
    ) -> None:
        # Applied/merged/rejected evidence never becomes an active map tile.
        row = db.execute("SELECT status FROM owned WHERE id=?", (inventory_id,)).fetchone()
        if row[0] in {"merged", "rejected"}:
            return
        segment = self.state["active_segment"]
        image = cv2.resize(cell.image, (64, 64), interpolation=cv2.INTER_AREA)
        previous = self.slots.get(inventory_id)
        first = previous.cell.image if previous else image
        template = Cell(cell.x, cell.y, cell.width, cell.height, cell.column, first)
        self.slots[inventory_id] = MapSlot(
            inventory_id,
            segment,
            template,
            world_y,
            features(first).hash,
            image,
            features(image).hash,
        )
        db.execute(
            "INSERT INTO scan_map_slot VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(inventory_id) DO UPDATE SET segment=excluded.segment, "
            "world_y=excluded.world_y,rect=excluded.rect,observation_id=excluded.observation_id",
            (
                inventory_id,
                self.recording_id,
                segment,
                cell.column,
                world_y,
                json.dumps(cell.rect),
                observation_id,
            ),
        )

    def checkpoint(
        self,
        db: sqlite3.Connection,
        offset: float,
        top: float,
        bottom: float,
        new_entries: int,
        relocated: bool,
    ) -> None:
        previous = self.state["viewport"] or self.state.get("last_viewport")
        viewport = {
            "segment": self.state["active_segment"],
            "top": top - offset,
            "bottom": bottom - offset,
        }
        if previous and previous["segment"] == viewport["segment"]:
            delta = viewport["top"] - previous["top"]
            self.state["direction"] = "down" if delta > 2 else "up" if delta < -2 else "stationary"
        self.state.update(
            offset=offset,
            viewport=viewport,
            position="relocalized"
            if relocated
            else "unlocated"
            if self.uncertain
            else "extended"
            if new_entries
            else "revisited",
        )
        self.save(db)

    def save(self, db: sqlite3.Connection) -> None:
        db.execute(
            "INSERT INTO scan_map_state VALUES (?,?) ON CONFLICT(recording_id) "
            "DO UPDATE SET document=excluded.document",
            (self.recording_id, json.dumps(self.state)),
        )
