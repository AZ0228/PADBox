"""Reproducible import orchestration. Human decisions are never overwritten."""

import json
import logging
from contextlib import closing
from pathlib import Path

import cv2

from padbox.database.store import Store, now
from padbox.inventory.processor import FrameProcessor
from padbox.inventory.service import statistics
from padbox.inventory.snapshots import auto_accept_strong, register_scan
from padbox.models import Layout
from padbox.recognition.matcher import MIN_CANDIDATE_SCORE, Matcher
from padbox.video.reader import fingerprint, frames, metadata

LOG = logging.getLogger(__name__)
PIPELINE_VERSION = "3"


def import_media(
    store: Store,
    path: Path,
    layout: Layout | None = None,
    interval: float = 0.3,
    additions: bool = False,
    snapshot: bool = True,
) -> dict:
    path = path.expanduser().resolve(strict=True)
    layout = layout or Layout()
    recording_id = fingerprint(path)
    config = {
        "layout": layout.model_dump(mode="json"),
        "interval": interval,
        "additions": additions,
        "pipeline": PIPELINE_VERSION,
    }
    if snapshot:
        config["snapshot"] = True
    config_json = json.dumps(config, sort_keys=True)
    with closing(store.connect()) as db, db:
        existing = db.execute("SELECT * FROM recording WHERE id=?", (recording_id,)).fetchone()
        if existing:
            if existing["config"] != config_json:
                observations = db.execute(
                    "SELECT COUNT(*) FROM observation WHERE recording_id=?", (recording_id,)
                ).fetchone()[0]
                if observations:
                    raise ValueError(
                        "This recording already has another layout/sampling configuration. "
                        "Use a separate --home to recalibrate without losing reviews."
                    )
                db.execute(
                    "UPDATE recording SET config=?,status='running' WHERE id=?",
                    (config_json, recording_id),
                )
            if existing["status"] == "complete":
                return {
                    "recording_id": recording_id,
                    "reused": True,
                    "statistics": statistics(store, recording_id),
                }
        else:
            db.execute(
                "INSERT INTO recording VALUES (?,?,?,?,?,?)",
                (
                    recording_id,
                    str(path),
                    json.dumps(metadata(path)),
                    config_json,
                    "running",
                    now(),
                ),
            )
        if snapshot:
            register_scan(db, recording_id)
    directory = store.home / "recordings" / recording_id
    directory.mkdir(parents=True, exist_ok=True)
    matcher = Matcher(store.references())
    processor = FrameProcessor(store, recording_id, layout, matcher, additions)
    counts = processor.counts
    try:
        for frame in frames(path, interval):
            processor.process(frame)
        if counts["observations"] == 0:
            with closing(store.connect()) as db:
                have = db.execute(
                    "SELECT COUNT(*) FROM observation WHERE recording_id=?", (recording_id,)
                ).fetchone()[0]
            if not have:
                raise ValueError(
                    "No complete grid cells detected. Inspect debug frames and "
                    "set an ROI or calibrated layout and retry."
                )
        with closing(store.connect()) as db, db:
            db.execute("UPDATE recording SET status='complete' WHERE id=?", (recording_id,))
    except Exception:
        with closing(store.connect()) as db, db:
            db.execute("UPDATE recording SET status='failed' WHERE id=?", (recording_id,))
        raise
    summary = {
        "recording_id": recording_id,
        "reused": False,
        **counts,
        "statistics": statistics(store, recording_id),
        "artifacts": str(directory),
        "warning": "Visible slots only; skipped content and stacked quantities require review.",
        "next_step": f"padbox scans diff {recording_id}" if snapshot else "Review entries",
    }
    (directory / "summary.json").write_text(json.dumps(summary, indent=2))
    LOG.info("Import complete: %s", summary)
    return summary


def rematch(store: Store) -> int:
    """Refresh proposals after adding references, preserving every reviewed decision."""
    matcher = Matcher(store.references())
    changed = 0
    with closing(store.connect()) as db, db:
        rows = db.execute("""SELECT v.*,o.status FROM observation v JOIN owned o
         ON o.id=v.matched_inventory_id WHERE o.status IN ('uncertain','unmatched')
         AND NOT EXISTS(SELECT 1 FROM review_event r WHERE r.inventory_id=o.id
                        AND r.action<>'map_join')
         AND NOT EXISTS(SELECT 1 FROM box_scan s WHERE s.recording_id=o.recording_id
                        AND s.status='applied')
         ORDER BY v.timestamp""").fetchall()
        best_by_copy = {}
        for row in rows:
            image = cv2.imread(row["crop"])
            if image is None:
                LOG.warning("Observation crop unavailable: %s", row["crop"])
                continue
            match = matcher.match(image)
            db.execute(
                "UPDATE observation SET candidates=?,match_reason=? WHERE id=?",
                (json.dumps([c.model_dump() for c in match.candidates]), match.reason, row["id"]),
            )
            best = match.candidates[0] if match.candidates else None
            if best and best.score > best_by_copy.get(row["matched_inventory_id"], (-1,))[0]:
                best_by_copy[row["matched_inventory_id"]] = (best.score, best, match)
        for owned_id, (score, candidate, match) in best_by_copy.items():
            db.execute(
                """UPDATE owned SET proposed_id=?,confidence=?,high_confidence=?,
             status=? WHERE id=?""",
                (
                    candidate.monster_id if score >= MIN_CANDIDATE_SCORE else None,
                    score,
                    int(match.high_confidence),
                    "uncertain" if score >= MIN_CANDIDATE_SCORE else "unmatched",
                    owned_id,
                ),
            )
            changed += 1
    with closing(store.connect()) as db:
        scans = db.execute("SELECT recording_id FROM box_scan WHERE status='draft'").fetchall()
    for scan in scans:
        auto_accept_strong(store, scan[0])
    return changed
