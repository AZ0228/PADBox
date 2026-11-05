from contextlib import closing
from pathlib import Path

import pytest

from padbox.database.store import Store
from padbox.demo import Demo
from padbox.inventory.pipeline import import_media
from padbox.sources.adapters import CuratedSource


@pytest.fixture
def reviewable_draft(store, fixtures):
    """Exact file fixtures auto-accept; leave one valid identity for UI review."""
    sid = import_media(store, fixtures["image"])["recording_id"]
    with closing(store.connect()) as db, db:
        item = db.execute(
            "SELECT id FROM owned WHERE recording_id=? "
            "AND status='confirmed' AND high_confidence=1 ORDER BY id LIMIT 1",
            (sid,),
        ).fetchone()[0]
        db.execute(
            "UPDATE owned SET status='uncertain',monster_id=NULL,high_confidence=0 WHERE id=?",
            (item,),
        )
        db.execute("DELETE FROM review_event WHERE inventory_id=?", (item,))
    return sid


@pytest.fixture
def fixtures(tmp_path: Path) -> dict[str, Path]:
    return Demo(tmp_path / "fixtures").generate()


@pytest.fixture
def store(tmp_path: Path, fixtures: dict[str, Path]) -> Store:
    database = Store(tmp_path / "home")
    database.load_monsters(CuratedSource(fixtures["references"]).load())
    return database
