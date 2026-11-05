"""Verified skill layouts. Unknown opcodes stay unknown."""

import json
from pathlib import Path

from padbox.database.store import Store
from padbox.mechanics.awakenings import describe
from padbox.mechanics.decode import decode_skill
from padbox.mechanics.explain import explain_monster, kit_members, roles_from
from padbox.models import Monster, Provenance, Relationship, Skill


def _skill(skill_id: int, kind: int, params: list[int], **extra: object) -> dict:
    return {"id": skill_id, "type": kind, "params": params, "name": "", "description": "", **extra}


def _index(*skills: dict) -> dict[int, dict]:
    return {skill["id"]: skill for skill in skills}


def _kinds(effects: list) -> list[str]:
    return [effect.kind for effect in effects]


def test_roze_base_active_is_cleric_haste_and_transform():
    parent = _skill(1, 116, [2, 3, 4, 5], initialCooldown=28, maxLevel=5)
    index = _index(
        parent,
        _skill(2, 196, [9999]),
        _skill(3, 117, [9999, 0, 0, 0, 9999]),
        _skill(4, 146, [2, 2]),
        _skill(5, 202, [13967]),
    )
    effects = decode_skill(parent, index)
    assert _kinds(effects) == [
        "unmatchable_clear",
        "bind_clear",
        "awoken_bind_clear",
        "charge_other_skills",
        "transform",
    ]
    assert effects[0].details == {"full": True}
    assert effects[3].details == {"turns": 2}
    assert effects[4].details == {"monster_id": 13967}
    roles = roles_from(effects)
    assert roles["full_bind_clear"] and roles["full_awoken_bind_clear"]
    assert roles["full_unmatchable_clear"]
    assert roles["charge_other_skills_turns"] == 2
    assert roles["transform_into"] == [13967]
    assert roles["unknown_effects"] == 0


def test_fortress_mode_covers_void_pierce_board_and_awoken_clear():
    parent = _skill(10, 116, [11, 12, 13, 14, 15])
    index = _index(
        parent,
        _skill(11, 196, [9999]),
        _skill(12, 117, [0, 0, 0, 0, 9999]),
        _skill(13, 191, [4]),
        _skill(14, 244, [4, 3]),
        _skill(15, 230, [4, 15, 200]),
    )
    effects = decode_skill(parent, index)
    roles = roles_from(effects)
    assert roles["full_bind_clear"] is False
    assert roles["full_awoken_bind_clear"] and roles["full_unmatchable_clear"]
    assert roles["void_pierce_turns"] == 4
    assert roles["boards"] == [{"columns": 6, "rows": 5, "turns": 4}]
    assert roles["all_allies_atk"] == [{"turns": 4, "multiplier_hundredths": 200}]
    assert roles["unknown_effects"] == 0


def test_assist_form_voids_attribute_and_damage_absorption():
    parent = _skill(20, 116, [21, 22, 23, 24])
    index = _index(
        parent,
        _skill(21, 196, [9999]),
        _skill(22, 117, [0, 0, 0, 0, 9999]),
        _skill(23, 173, [2, 1, 0, 1]),
        _skill(24, 244, [2, 1]),
    )
    roles = roles_from(decode_skill(parent, index))
    assert roles["attribute_absorb_turns"] == 2
    assert roles["damage_absorb_turns"] == 2
    assert roles["combo_absorb_turns"] is None
    assert roles["boards"] == [{"columns": 7, "rows": 6, "turns": 2}]


def test_roze_leader_separates_linked_shield_from_cross_attack():
    parent = _skill(30, 138, [31, 32, 33])
    index = _index(
        parent,
        _skill(31, 178, [12]),
        _skill(32, 182, [8, 5, 100, 83]),
        _skill(33, 271, [8, 20000, 0, 4, 17000000]),
    )
    effects = decode_skill(parent, index)
    assert effects[0].summary == "Move time is 12 seconds"
    assert effects[1].details["attributes"] == ["light"]
    assert effects[1].details["shield_percent"] == 83
    assert effects[1].details["minimum_linked"] == 5
    assert "1x ATK" not in effects[1].summary
    assert effects[2].details["trigger"] == "cross_attack"
    assert effects[2].details["atk_multiplier_hundredths"] == 20000
    assert effects[2].details["added_combos"] == 4
    assert effects[2].details["fixed_damage"] == 17_000_000


def test_partial_recovery_and_absorption_shapes():
    heal = decode_skill(_skill(1, 117, [2, 500]), {})
    assert heal[0].kind == "bind_clear" and heal[0].details == {"full": False, "turns": 2}
    assert heal[1].summary == "Recovers 5x RCV as HP"
    awoken = decode_skill(_skill(2, 117, [0, 0, 0, 30, 3]), {})
    assert [effect.kind for effect in awoken] == ["heal_percent", "awoken_bind_clear"]
    combo = decode_skill(_skill(3, 173, [1, 0, 1]), {})
    assert combo[0].details["kinds"] == ["combo"]
    damage = decode_skill(_skill(4, 173, [2, 0, 0, 1]), {})
    assert damage[0].details["kinds"] == ["damage"]
    both = decode_skill(_skill(5, 173, [4, 1, 1]), {})
    assert both[0].details["kinds"] == ["attribute", "combo"]


def test_unknown_layouts_are_not_promoted():
    missing = decode_skill(_skill(1, 116, [99]), {})
    assert missing[0].evidence == "unknown"
    assert missing[0].details["component_id"] == 99
    assert decode_skill(_skill(2, 999, [1]), {})[0].evidence == "unknown"
    assert decode_skill(_skill(3, 146, [1, 3]), {})[0].evidence == "unknown"
    assert decode_skill(_skill(4, 173, [2, 0, 0, 2]), {})[0].evidence == "unknown"
    assert decode_skill(_skill(5, 230, [4, 1, 200]), {})[0].evidence == "unknown"
    assert decode_skill(_skill(6, 244, [1, 9]), {})[0].evidence == "unknown"
    mixed = decode_skill(_skill(7, 271, [8 + 4096, 500, 0, 0, 1]), {})
    assert mixed[0].evidence == "unknown"
    assert decode_skill(_skill(8, 71, [0, 6, -1]), {})[0].evidence == "unknown"


def test_simple_attacks_board_change_and_move_time():
    mass = decode_skill(_skill(1, 0, [0, 1000]), {})[0]
    assert mass.summary == "10x ATK fire mass attack"
    buff = decode_skill(_skill(2, 50, [4, 3, 200]), {})[0]
    assert buff.summary == "2x ATK for light for 4 turns"
    board = decode_skill(_skill(3, 71, [0, 1, 2, 3, 4, -1]), {})[0]
    assert board.details["orbs"] == ["fire", "water", "wood", "light", "dark"]
    bonus = decode_skill(_skill(4, 15, [200]), {})[0]
    assert bonus.details == {"seconds": 2}
    small = decode_skill(_skill(5, 244, [1, 2]), {})[0]
    assert small.details == {"turns": 1, "columns": 5, "rows": 4}
    rank = decode_skill(_skill(6, 148, [150]), {})[0]
    assert rank.summary == "1.5x Rank EXP when entering as leader"


def test_skill_cycles_stop():
    first = _skill(1, 116, [2])
    second = _skill(2, 116, [1])
    effects = decode_skill(first, _index(first, second))
    assert effects[-1].evidence == "unknown"
    assert "cycle" in effects[-1].summary


def test_kit_includes_transform_base_and_its_assist():
    edges = [(13966, 13967, "transform"), (13966, 13968, "assist_evolution"), (1, 2, "evolution")]
    assert kit_members(13967, edges) == [
        (13967, "self"),
        (13966, "transform_base"),
        (13968, "assist_evolution"),
    ]
    assert kit_members(13966, edges) == [
        (13966, "self"),
        (13967, "transform"),
        (13968, "assist_evolution"),
    ]


def test_create_orbs_uses_the_attribute_bitmask():
    single = decode_skill(_skill(1, 141, [6, 8, 8]), {})
    assert single[0].kind == "create_orbs"
    assert single[0].details == {"count": 6, "attributes": ["light"]}
    mixed = decode_skill(_skill(2, 141, [4, 21, 21]), {})
    assert mixed[0].details["attributes"] == ["fire", "wood", "dark"]
    assert decode_skill(_skill(3, 141, [4, 1, 2]), {})[0].evidence == "unknown"
    heal = decode_skill(_skill(4, 141, [15, 48]), {})
    assert heal[0].details == {"count": 15, "attributes": ["dark", "heal"]}
    replaced = decode_skill(_skill(5, 141, [6, 32, 48]), {})
    assert replaced[0].kind == "orb_replace"
    assert replaced[0].details == {"count": 6, "excluded": ["dark"], "to": ["heal"]}
    assert decode_skill(_skill(6, 141, [6, 128, 32]), {})[0].evidence == "unknown"


def test_single_destination_conversion_and_recovery_buff():
    converted = decode_skill(_skill(1, 154, [992, 1]), {})
    assert converted[0].kind == "orb_convert"
    assert converted[0].details == {
        "from": ["heal", "jammer", "poison", "lethal poison", "bomb"],
        "to": ["fire"],
    }
    assert decode_skill(_skill(2, 154, [3, 160]), {})[0].evidence == "unknown"
    assert decode_skill(_skill(3, 172, []), {})[0].summary == "Removes locks"
    recovery = decode_skill(_skill(4, 50, [3, 5, 200]), {})
    assert recovery[0].kind == "rcv_buff"
    assert recovery[0].details == {"turns": 3, "multiplier_hundredths": 200}
    leader = decode_skill(_skill(5, 230, [4, 2, 150]), {})
    assert leader[0].kind == "team_atk"
    assert leader[0].details["target"] == "the leader"
    assert decode_skill(_skill(6, 230, [4, 1, 150]), {})[0].evidence == "unknown"
    assert decode_skill(_skill(7, 230, [4, 15, 200]), {})[0].kind == "all_allies_atk"


def test_repeated_layouts_for_shields_orbs_and_move_time():
    shield = decode_skill(_skill(1, 3, [6, 35]), {})[0]
    assert shield.kind == "damage_shield"
    assert shield.summary == "Reduces damage by 35% for 6 turns"
    assert decode_skill(_skill(2, 3, [1, 100]), {})[0].summary == "Voids damage for 1 turn"
    assert decode_skill(_skill(3, 3, [5, 50, 50000]), {})[0].evidence == "unknown"
    assert decode_skill(_skill(4, 6, [15]), {})[0].summary == "Reduces enemies' remaining HP by 15%"
    delayed = decode_skill(_skill(5, 18, [2]), {})[0]
    assert delayed.summary == "Delays enemies' next move for 2 turns"
    assert decode_skill(_skill(6, 18, [2, 2]), {})[0].details == {"turns": 2}
    assert decode_skill(_skill(7, 18, [2, 3]), {})[0].evidence == "unknown"
    defense = decode_skill(_skill(8, 19, [5, 100]), {})[0]
    assert defense.summary == "Voids enemies' defense for 5 turns"
    combos = decode_skill(_skill(9, 160, [3, 1]), {})[0]
    assert combos.summary == "Adds 1 combo for 3 turns"
    flat = decode_skill(_skill(10, 132, [1, 295]), {})[0]
    assert flat.summary == "Orb move time +29.5 seconds for 1 turn"
    assert flat.details["seconds"] == 29.5
    slower = decode_skill(_skill(11, 132, [1, -10]), {})[0]
    assert slower.summary == "Orb move time -1 second for 1 turn"
    scaled = decode_skill(_skill(12, 132, [1, 0, 150]), {})[0]
    assert scaled.summary == "1.5x orb move time for 1 turn"
    assert decode_skill(_skill(13, 132, [1, 1, 150]), {})[0].evidence == "unknown"
    paired = decode_skill(_skill(14, 90, [1, 5, 3, 200]), {})
    assert [effect.kind for effect in paired] == ["attribute_atk", "rcv_buff"]
    assert paired[0].details["attribute"] == "light"
    changed = decode_skill(_skill(15, 20, [4, 1, 2]), {})[0]
    assert changed.kind == "orb_change"
    assert changed.details["changes"] == [
        {"from": "dark", "to": "water"},
        {"from": "wood", "to": "fire"},
    ]
    both = decode_skill(_skill(16, 20, [5, 0, 1]), {})[0]
    assert both.summary == "Changes heal, water to fire"
    assert decode_skill(_skill(17, 20, [1, 9]), {})[0].evidence == "unknown"
    assert decode_skill(_skill(18, 188, [40]), {})[0].evidence == "unknown"


def test_caps_lines_and_typing_follow_the_repeated_parameters():
    cap = decode_skill(_skill(1, 258, [2, 200, 1]), {})[0]
    assert cap.summary == "Sets damage cap to 20 billion for this ally for 2 turns"
    allies = decode_skill(_skill(2, 258, [1, 99, 15]), {})[0]
    assert allies.summary == "Sets damage cap to 9.9 billion for all allies for 1 turn"
    assert decode_skill(_skill(3, 258, [1, 300, 7]), {})[0].evidence == "unknown"
    self_cap = decode_skill(_skill(4, 241, [20, 99]), {})[0]
    assert self_cap.details["target"] == "this ally"
    assert decode_skill(_skill(5, 140, [63, 6]), {})[0].summary == "Enhances all orbs"
    light = decode_skill(_skill(6, 140, [40, 6]), {})[0]
    assert light.summary == "Enhances light and heal orbs"
    assert decode_skill(_skill(7, 140, [0, 6]), {})[0].evidence == "unknown"
    typing = decode_skill(_skill(8, 88, [1, 5, 300]), {})[0]
    assert typing.summary == "3x ATK for God for 1 turn"
    regen = decode_skill(_skill(9, 179, [2, 0, 100]), {})[0]
    assert regen.summary == "Fully recovers HP for 2 turns"
    assert decode_skill(_skill(10, 179, [3, 0, 30, 0, 9999]), {})[0].evidence == "unknown"
    assert decode_skill(_skill(11, 10, []), {})[0].summary == "Replaces all orbs"
    assert decode_skill(_skill(12, 161, [25]), {})[0].details == {"percent": 25}
    column = decode_skill(_skill(13, 127, [33, 34]), {})[0]
    assert column.summary == "Changes the outermost columns to water and heal at random"
    row = decode_skill(_skill(14, 128, [16, 1]), {})[0]
    assert row.summary == "Changes the bottom row to fire"
    assert row.details["to"] == ["fire"]
    assert decode_skill(_skill(15, 128, [33, 1]), {})[0].evidence == "unknown"


def test_lock_clear_skyfall_and_the_other_repeated_clauses():
    assert decode_skill(_skill(1, 172, [1]), {})[0].summary == "Removes locks"
    assert decode_skill(_skill(2, 184, [1]), {})[0].summary == "No skyfall combos for 1 turn"
    assert decode_skill(_skill(3, 237, [6, 150]), {})[0].summary == "1.5x maximum HP for 6 turns"
    assert decode_skill(_skill(4, 93, []), {})[0].kind == "leader_swap"
    assert decode_skill(_skill(5, 259, [100]), {})[0].summary == "Destroys 1 shield"
    assert decode_skill(_skill(6, 259, [50]), {})[0].summary == "Reduces enemy shield by 50%"
    roulette = decode_skill(_skill(7, 207, [2, 50, 0, 0, 0, 0, 0, 1]), {})[0]
    assert roulette.summary == "Creates 1 rapid roulette orb for 2 turns"
    assert decode_skill(_skill(8, 142, [2]), {})[0].summary == (
        "This ally's attribute changes to fire for 2 turns"
    )
    assert decode_skill(_skill(9, 142, [1, 4]), {})[0].details["attribute"] == "dark"
    assert decode_skill(_skill(10, 9, [5]), {})[0].summary == "Changes heal to fire"
    assert decode_skill(_skill(11, 195, []), {})[0].summary == "Reduces HP to 1"
    assert decode_skill(_skill(12, 195, [80]), {})[0].summary == "Reduces HP by 20%"
    assert decode_skill(_skill(13, 190, [63, 3]), {})[0].summary == "Creates 3 combo orbs"
    assert decode_skill(_skill(14, 190, [2, 42]), {})[0].summary == "Water orbs become combo orbs"
    skyfall = decode_skill(_skill(15, 126, [8, 7, 7, 15]), {})[0]
    assert skyfall.summary == "Light orbs appear more often for 7 turns"
    only = decode_skill(_skill(16, 126, [8, 1, 1, 100]), {})[0]
    assert only.summary == "Only light orbs appear for 1 turn"
    assert decode_skill(_skill(17, 37, [3, 4000]), {})[0].summary == (
        "Inflicts 40x light ATK on 1 enemy"
    )
    assert decode_skill(_skill(18, 21, [1, 2, 100]), {})[0].summary == (
        "Voids wood damage for 1 turn"
    )
    assert decode_skill(_skill(19, 11, [0, 250]), {})[0].summary == "2.5x ATK for fire"


def test_explain_names_awakenings_from_this_snapshot(tmp_path: Path):
    store = Store(tmp_path)
    provenance = Provenance(source="test", version="1")
    parent = _skill(64124, 116, [64125], initialCooldown=28, maxLevel=5, name="Resolve")
    store.load_monsters(
        [
            Monster(
                monster_id=13966,
                name="Base",
                servers=["NA"],
                provenance=provenance,
                awakenings=[49, 52],
                super_awakenings=[21],
                relationships=[Relationship(target_id=13967, kind="transform")],
                active_skill=Skill(
                    skill_id=64124,
                    name="Resolve",
                    base_cooldown=28,
                    minimum_cooldown=24,
                    raw=parent,
                ),
                raw={"canAssist": False, "is8Latent": True, "syncAwakening": 138},
            ),
            Monster(monster_id=13967, name="Combat", servers=["NA"], provenance=provenance),
        ]
    )
    report = explain_monster(store, 13966, skill_index={64125: _skill(64125, 202, [13967])})
    form = report["forms"][0]
    assert form["active"]["minimum_cooldown"] == 24
    assert form["roles"]["transform_into"] == [13967]
    assert form["roles"]["unknown_effects"] == 0
    assert form["awakenings"] == [describe(49), describe(52)]
    assert form["super_awakenings"] == [describe(21)]
    assert form["sync_awakening"]["name"] == "Assist Resonance"
    assert form["eight_latent_slots"] is True
    assert form["sync_awakening"]["evidence"] == "unknown"
    assert [item["monster_id"] for item in report["forms"]] == [13966, 13967]
    cache = tmp_path / "cache"
    cache.mkdir()
    blob = cache / "skills"
    blob.write_text(json.dumps([_skill(7, 191, [1])]), encoding="utf-8")
    (cache / "skills.json").write_text(
        json.dumps(
            {
                "url": "https://example.invalid/1/monsters-info/skill_en.json",
                "fetched_at": "2026-11-04T00:00:00+00:00",
            }
        )
    )
    loaded = explain_monster(store, 13967)
    assert loaded["skill_index"] == "cached"
