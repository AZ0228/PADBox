import json
from contextlib import closing

import pytest

from padbox.database.store import Store
from padbox.models import Monster, Provenance, Relationship, Skill
from padbox.sources.adapters import CuratedSource


def test_curated_missing_icons_fail_without_partial_mutation(store, tmp_path):
    before = len(store.references())
    manifest = tmp_path / "bad.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "monster_id": 55,
                    "name": "Missing",
                    "icon": "absent.png",
                    "provenance": {"source": "test", "version": "1"},
                }
            ]
        )
    )
    with pytest.raises(ValueError, match="missing or unreadable"):
        store.load_monsters(CuratedSource(manifest).load())
    assert len(store.references()) == before


def test_reference_inventory_separation_and_transform(store):
    provenance = Provenance(source="explicit test facts", version="1")
    store.load_monsters(
        [
            Monster(
                monster_id=13966,
                name="Base fixture",
                servers=["NA"],
                provenance=provenance,
                relationships=[Relationship(target_id=13967, kind="transform")],
                awakenings=[21, 21],
                super_awakenings=[56],
                active_skill=Skill(
                    skill_id=42,
                    name="Transform",
                    base_cooldown=28,
                    minimum_cooldown=24,
                    evidence="source_encoded",
                ),
            ),
            Monster(monster_id=13967, name="Combat fixture", servers=["NA"], provenance=provenance),
        ]
    )
    with closing(store.connect("reference")) as db:
        edge = db.execute("SELECT * FROM relationship WHERE source_id=13966").fetchone()
        assert edge["target_id"] == 13967
        assert edge["kind"] == "transform"
        assert (
            db.execute("SELECT COUNT(*) FROM monster_awakening WHERE monster_id=13966").fetchone()[
                0
            ]
            == 3
        )
        assert db.execute("SELECT minimum_cooldown FROM skill WHERE id=42").fetchone()[0] == 24
    with closing(store.connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM owned").fetchone()[0] == 0
    assert store.home.joinpath("reference.sqlite").exists()
    assert store.home.joinpath("inventory.sqlite").exists()


def test_schema_rejects_future_version(tmp_path):
    store = Store(tmp_path / "home")
    with closing(store.connect()) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="Unsupported"):
        Store(store.home)


def test_cached_icon_retained_only_for_same_source_version(store):
    old = store.references()[0]
    prov = json.loads(old["provenance"])
    store.load_monsters(
        [
            Monster(
                monster_id=old["id"],
                name="Same version",
                servers=["NA"],
                provenance=Provenance(**prov),
            )
        ]
    )
    assert store.references(str(old["id"]))[0]["icon"] == old["icon"]
    prov["version"] = "new-source-version"
    store.load_monsters(
        [
            Monster(
                monster_id=old["id"],
                name="New version",
                servers=["NA"],
                provenance=Provenance(**prov),
            )
        ]
    )
    assert store.references(str(old["id"]))[0]["icon"] is None
