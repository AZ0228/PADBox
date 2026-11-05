import hashlib
import json
from contextlib import closing

import pytest

from padbox.database.store import Store
from padbox.models import Monster, Provenance, Relationship, Skill
from padbox.sources.adapters import CuratedSource, FormationSource, SnapshotCache, sheet_location


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


def test_cache_integrity_and_offline_use(tmp_path, monkeypatch):
    cache = SnapshotCache(tmp_path)
    url = "https://example.invalid/reference.json"
    key = hashlib.sha256(url.encode()).hexdigest()
    path = tmp_path / key
    path.write_bytes(b"[]")
    path.with_suffix(".json").write_text(json.dumps({"sha256": hashlib.sha256(b"[]").hexdigest()}))
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: pytest.fail("Network used"))
    assert cache.fetch(url) == path
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        cache.fetch(url)


def test_sheet_numbering_no_double_id_conversion():
    assert sheet_location(1) == (1, 0, 0)
    assert sheet_location(100) == (1, 918, 918)
    assert sheet_location(101) == (2, 0, 0)
    assert sheet_location(13966) == (140, 510, 612)
    assert sheet_location(13967) == (140, 612, 612)


def test_formation_adapter_fake_snapshot_and_explicit_transform(tmp_path, monkeypatch):
    cards = [
        {
            "id": 13966,
            "name": "Base",
            "enabled": True,
            "attrs": [3],
            "types": [1],
            "henshinTo": [13967],
            "activeSkillId": 42,
        },
        {
            "id": 13967,
            "name": "Combat",
            "enabled": True,
            "attrs": [3, 1, 3],
            "henshinFrom": [13966],
        },
        {"id": 13968, "name": "Assist", "enabled": True, "evoBaseId": 13966, "awakenings": [49]},
        {"id": 19999, "name": "Unavailable", "enabled": False},
    ]
    skills = [
        {
            "id": 42,
            "name": "Change",
            "initialCooldown": 28,
            "maxLevel": 5,
            "type": 202,
            "params": [13967],
        }
    ]

    def local(path):
        data = (
            cards
            if path.endswith("mon_en.json")
            else skills
            if path.endswith("skill_en.json")
            else [{"code": "en", "updateTime": 1793361638631}]
        )
        file = tmp_path / path.split("/")[-1]
        file.write_text(json.dumps(data))
        return data, file

    source = FormationSource(tmp_path / "cache", tmp_path / "icons")
    monkeypatch.setattr(source, "_json", local)
    result = source.load()
    assert len(result) == 3
    base = result[0]
    assert {e.kind for e in base.relationships} == {"transform", "assist_evolution"}
    assert base.active_skill.minimum_cooldown == 24
    assert base.active_skill.effects == []
    assert base.active_skill.raw["params"] == [13967]


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
