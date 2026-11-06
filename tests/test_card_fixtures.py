"""Regression cards extracted from the pinned PADDashFormation NA snapshot.

Names and skill IDs below were read from that snapshot (commit
6d8b0698208aebef17e4f4fefe6b3c616f8d0ed8, card file dated 2026-10-30).
They are not wiki text. #8336 is Ultraman Gaia (V2); #8736 is a different card
and is not part of this extract.
"""

import json
from contextlib import closing
from pathlib import Path

from padbox.mechanics.explain import explain_monster
from padbox.sources.adapters import PINNED_COMMIT

CARDS = Path(__file__).resolve().parent / "fixtures" / "cards"


def _records() -> dict[int, dict]:
    records = {}
    for path in CARDS.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        records[int(record["monster_id"])] = record
    return records

# monster_id, name, (active id, type, name), (leader id, type, name)
EXPECTED = {
    13966: (
        "Rozé",
        (64124, 116, "This...is my resolve."),
        (64128, 148, "Keen Intellect and Excellent Communication Skills"),
    ),
    13967: (
        "Rozé & Zi-Artemis",
        (64131, 116, "Fortress Mode"),
        (64136, 138, "Subjugation of Enemy Territory with Geass"),
    ),
    8336: (
        "Ultraman Gaia (V2)",
        (26756, 232, "Gaia (V2) Lifting"),
        (26763, 138, "Will of the Earth"),
    ),
    7844: (
        "Super Reincarnated Apollo",
        (3924, 116, "Sunlight Harmony"),
        (24300, 138, "True Spirit of the Solar Deity"),
    ),
    10756: (
        "Love Deity of the Divine Transcendence, Venus",
        (41360, 116, "Beautiful Tenderness"),
        (41361, 138, "Prime of the Love Deity"),
    ),
    7949: (
        "Paladin",
        (24544, 116, "Sacrifice"),
        (24548, 138, "3rd Class: Paladin"),
    ),
    7095: (
        "Saintly Wise Queen, Saphira",
        (21111, 116, "Cryptic Riddle"),
        (21114, 138, "Intense Challenge"),
    ),
    7096: (
        "Inquisitive Saintly Wise Queen, Saphira",
        (21117, 116, "Bright Solution"),
        (21120, 138, "Intense Research"),
    ),
    4834: (
        "Chosen War Goddess, Valkyrie - Ciel",
        (11780, 116, "Sacred Feather"),
        (11783, 138, "Innocent Soul"),
    ),
    4206: (
        "Sweet Dianthus, Kushinadahime",
        (9891, 116, "Loving Perception"),
        (9894, 138, "Please come here"),
    ),
    3939: (
        "Sword Barrier Dragon Caller, Rathios",
        (9065, 116, "Solblade Guard"),
        (9068, 138, "Cataphract's Draco Summoning Seal"),
    ),
    3821: (
        "Pixel Lightning",
        (8523, 116, "You survive."),
        (8526, 138, "Thunderfall"),
    ),
    7712: (
        "Leona",
        (23724, 116, "We just need to hold out a little longer!"),
        (23728, 138, "Fullheal"),
    ),
}


def _skills(record: dict) -> dict[int, dict]:
    return {int(skill["id"]): skill for skill in record["skills"]}


def test_regression_cards_match_the_pinned_snapshot():
    records = _records()
    assert set(records) == set(EXPECTED)
    assert 8736 not in records
    for monster_id, (name, active, leader) in EXPECTED.items():
        record = records[monster_id]
        card = record["card"]
        skills = _skills(record)
        assert record["name"] == name
        assert record["snapshot_commit"] == PINNED_COMMIT
        assert record["provenance"]["source"] == "PADDashFormation/NA"
        assert record["provenance"]["version"] == PINNED_COMMIT
        assert record["provenance"]["updated_at"].startswith("2026-10-30")
        assert record["skill_index"]["file"] == "monsters-info/skill_en.json"
        assert len(record["skills"]) < 40
        for skill_id, kind, skill_name in (active, leader):
            skill = skills[skill_id]
            assert card["activeSkillId" if skill_id == active[0] else "leaderSkillId"] == skill_id
            assert skill["type"] == kind
            assert skill["name"] == skill_name
            raw_fields = {
                "id",
                "name",
                "description",
                "type",
                "params",
                "initialCooldown",
                "maxLevel",
            }
            assert raw_fields <= set(skill)
    gaia = _skills(records[8336])[26756]
    assert gaia["params"] == [26757, 26760]
    assert _skills(records[8336])[26757]["type"] == 18
    assert _skills(records[8336])[26760]["name"] == "Gaia Slash"
    assert records[13966]["card"]["henshinTo"] == [13967]
    assert records[7095]["card"]["henshinTo"] == [7096]
    # The brief's Ordeal Venus is this ID: the snapshot recipe starts with the
    # untracked special-material sentinel, and the card name is the one above.
    assert records[10756]["card"]["evoMaterials"][0] == 65535


def test_regression_store_preserves_skills_and_transforms(regression_store):
    records = _records()
    with closing(regression_store.connect("reference")) as db:
        for monster_id, record in records.items():
            row = db.execute(
                "SELECT name, provenance FROM monster WHERE id=?", (monster_id,)
            ).fetchone()
            assert row["name"] == record["name"]
            assert json.loads(row["provenance"])["version"] == PINNED_COMMIT
            for skill in record["skills"]:
                stored = db.execute("SELECT raw FROM skill WHERE id=?", (skill["id"],)).fetchone()
                assert json.loads(stored["raw"]) == skill
        stage = db.execute(
            "SELECT role FROM monster_skill WHERE monster_id=8336 AND skill_id=26757"
        ).fetchone()
        assert stage is None
        active = db.execute(
            "SELECT skill_id FROM monster_skill WHERE monster_id=8336 AND role='active'"
        ).fetchone()
        assert active["skill_id"] == 26756
        for source, target in ((13966, 13967), (7095, 7096)):
            edge = db.execute(
                "SELECT target_id, kind FROM relationship WHERE source_id=?", (source,)
            ).fetchone()
            assert edge["target_id"] == target
            assert edge["kind"] == "transform"
        assert (
            db.execute("SELECT COUNT(*) FROM relationship WHERE target_id=8736").fetchone()[0] == 0
        )

    roze = {form["monster_id"]: form for form in explain_monster(regression_store, 13966)["forms"]}
    assert roze[13966]["roles"]["transform_into"] == [13967]
    assert roze[13966]["leader"]["effects"][0]["kind"] == "rank_exp"
    combat = [effect["kind"] for effect in roze[13967]["leader"]["effects"]]
    assert combat == ["move_time", "linked_orb_leader", "awakening_trigger"]
    linked = roze[13967]["leader"]["effects"][1]["details"]
    assert linked["attributes"] == ["light"]
    assert linked["minimum_linked"] == 5
    assert roze[13967]["leader"]["effects"][2]["details"]["trigger"] == "cross_attack"

    saphira = {
        form["monster_id"]: form for form in explain_monster(regression_store, 7095)["forms"]
    }
    assert saphira[7095]["roles"]["transform_into"] == [7096]
    assert 7096 in saphira

    gaia = explain_monster(regression_store, 8336)
    assert gaia["skill_index"] == "cached"
    assert gaia["forms"][0]["name"] == "Ultraman Gaia (V2)"
    assert gaia["forms"][0]["active"]["skill_id"] == 26756
