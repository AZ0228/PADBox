"""Map coordinates, direction, durable relocalization and ambiguous repeated blocks."""

from contextlib import closing

import cv2

from padbox.inventory.box_map import read_map


def jpeg(image):
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return encoded.tobytes()


def images(fixtures):
    return [
        cv2.imread(str(fixtures["references"].parent / "icons" / f"{i}.png")) for i in range(1, 16)
    ]


def test_map_migration_from_v2_keeps_evidence(store, fixtures):
    # Exercise actual v2 migration on a separate copy of the fixture DB.
    with closing(store.connect()) as db, db:
        db.executescript(
            "DROP TABLE scan_map_slot; DROP TABLE scan_map_state; PRAGMA user_version=2;"
        )
    from padbox.database.store import Store

    reopened = Store(store.home)
    assert reopened.references()
    assert read_map(reopened, "none")["known_slots"] == 0
    with closing(reopened.connect()) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
