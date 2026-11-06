"""Leader clauses from the pinned-snapshot regression cards.

A linked reduction and a cross bonus are separate verdicts. Description text
is not an input.
"""

from pathlib import Path

from conftest import load_regression_cards, regression_skill_index

from padbox.mechanics.clauses import (
    ClauseCondition,
    ClauseEffect,
    LeaderClause,
    MatchObservation,
    evaluate_clauses,
    leader_clauses,
)
from padbox.mechanics.decode import decode_skill

GAPS = Path(__file__).resolve().parents[1] / "docs" / "known-mechanics-gaps.md"


def _world():
    records_list = load_regression_cards()
    records = {int(record["monster_id"]): record for record in records_list}
    return records, regression_skill_index(records_list)


def _leader(records: dict, skills: dict, monster_id: int):
    record = records[monster_id]
    skill_id = record["card"]["leaderSkillId"]
    skill = next(item for item in record["skills"] if item["id"] == skill_id)
    return decode_skill(skill, skills)


def _clause(
    clause_id: str,
    condition: ClauseCondition,
    effect: ClauseEffect,
) -> LeaderClause:
    return LeaderClause(
        clause_id=clause_id,
        condition=condition,
        effects=[effect],
        skill_id=None,
        source_type=0,
        evidence="verified",
        summary=clause_id,
    )


def _statuses(clauses, observation: MatchObservation) -> dict[str, str]:
    return {item.clause_id: item.status for item in evaluate_clauses(clauses, observation)}


def test_roze_reduction_and_cross_offense_evaluate_separately():
    records, skills = _world()
    clauses = leader_clauses(_leader(records, skills, 13967))
    assert [clause.condition.kind for clause in clauses] == ["unconditional", "linked", "cross"]
    move_time, reduction, cross = clauses

    assert move_time.effects[0].seconds == 12
    assert reduction.skill_id == 64138
    assert reduction.condition.attributes == ["light"]
    assert reduction.condition.minimum == 5
    assert reduction.condition.combination == "single"
    assert [effect.kind for effect in reduction.effects] == ["damage_reduction"]
    assert reduction.effects[0].percent == 83
    assert cross.skill_id == 64139
    assert cross.condition.attributes == []
    assert cross.condition.trigger == "cross_attack"
    assert [effect.kind for effect in cross.effects] == [
        "multiplier",
        "added_combos",
        "fixed_damage",
    ]
    assert cross.effects[0].multiplier_hundredths == 20000
    assert cross.effects[1].combos == 4
    assert cross.effects[2].damage == 17_000_000

    linked_only = _statuses(clauses, MatchObservation(linked={"light": 5}))
    assert linked_only[reduction.clause_id] == "met"
    assert linked_only[cross.clause_id] == "unmet"
    assert linked_only[move_time.clause_id] == "met"

    longer_link = _statuses(clauses, MatchObservation(linked={"light": 9}))
    assert longer_link[reduction.clause_id] == "met"
    assert longer_link[cross.clause_id] == "unmet"

    cross_only = _statuses(clauses, MatchObservation(cross=True, linked={"light": 4}))
    assert cross_only[reduction.clause_id] == "unmet"
    assert cross_only[cross.clause_id] == "met"

    both = _statuses(clauses, MatchObservation(cross=True, linked={"light": 5}))
    assert both[reduction.clause_id] == "met"
    assert both[cross.clause_id] == "met"


def test_gaia_attack_and_reduction_stay_apart_from_unknown_clauses():
    records, skills = _world()
    clauses = leader_clauses(_leader(records, skills, 8336))
    linked = [clause for clause in clauses if clause.condition.kind == "linked"]
    unknown = [clause for clause in clauses if clause.evidence == "unknown"]
    assert [clause.effects[0].kind for clause in linked] == ["multiplier", "damage_reduction"]
    assert linked[0].effects[0].multiplier_hundredths == 3800
    assert linked[1].effects[0].percent == 50
    assert linked[0].condition.minimum == linked[1].condition.minimum == 9
    assert {clause.source_type for clause in unknown} == {185, 200}
    assert {clause.skill_id for clause in unknown} == {26764, 26770}

    verdicts = _statuses(clauses, MatchObservation(linked={"light": 9}, cross=True))
    assert verdicts[linked[0].clause_id] == "met"
    assert verdicts[linked[1].clause_id] == "met"
    assert all(verdicts[clause.clause_id] == "unknown" for clause in unknown)


def test_a_multi_attribute_link_is_not_given_a_combination_rule():
    records, skills = _world()
    clauses = leader_clauses(_leader(records, skills, 7096))
    linked = next(clause for clause in clauses if clause.condition.kind == "linked")
    assert linked.skill_id == 21122
    assert linked.condition.attributes == ["light", "dark"]
    assert linked.condition.minimum == 6
    assert linked.condition.combination == "unknown"
    filled = MatchObservation(linked={"light": 6, "dark": 6})
    assert _statuses([linked], filled)[linked.clause_id] == "unknown"
    assert any(clause.evidence == "unknown" for clause in clauses)


def test_rank_exp_is_not_an_activation_clause():
    records, skills = _world()
    effects = _leader(records, skills, 13966)
    assert [effect.kind for effect in effects] == ["rank_exp"]
    assert leader_clauses(effects) == []


def _atk(hundredths: int) -> ClauseEffect:
    return ClauseEffect(kind="multiplier", stat="atk", multiplier_hundredths=hundredths)


def test_each_condition_reads_only_its_own_observation():
    clauses = [
        _clause(
            "linked",
            ClauseCondition(
                kind="linked", attributes=["light"], minimum=5, combination="single"
            ),
            ClauseEffect(kind="damage_reduction", percent=83),
        ),
        _clause("cross", ClauseCondition(kind="cross", trigger="cross_attack"), _atk(20000)),
        _clause("l_shape", ClauseCondition(kind="l_shape"), _atk(100)),
        _clause(
            "row",
            ClauseCondition(kind="row", attributes=["light"], minimum=1),
            _atk(100),
        ),
        _clause(
            "combo",
            ClauseCondition(kind="combo", minimum=8),
            ClauseEffect(kind="added_combos", combos=2),
        ),
        _clause(
            "hp",
            ClauseCondition(kind="hp", minimum=50, comparison="at_most"),
            _atk(200),
        ),
        _clause("colors", ClauseCondition(kind="multi_color", minimum=4), _atk(100)),
        _clause("attribute", ClauseCondition(kind="attribute", attributes=["light"]), _atk(200)),
        _clause("card_type", ClauseCondition(kind="card_type", card_types=["God"]), _atk(200)),
    ]
    only_cross = _statuses(clauses, MatchObservation(cross=True))
    assert only_cross["cross"] == "met"
    assert only_cross["linked"] == "unmet"
    assert only_cross["l_shape"] == "unmet"
    assert only_cross["row"] == "unmet"
    assert only_cross["combo"] == "unknown"
    assert only_cross["hp"] == "unknown"
    assert only_cross["colors"] == "unknown"
    assert only_cross["attribute"] == "unmet"
    assert only_cross["card_type"] == "unmet"

    filled = _statuses(
        clauses,
        MatchObservation(
            linked={"light": 5},
            l_shape=True,
            rows={"light": 1},
            combos=8,
            hp_percent=40,
            color_count=4,
            attributes=["light"],
            card_types=["God"],
        ),
    )
    assert filled["cross"] == "unmet"
    others = [clause for clause in clauses if clause.clause_id != "cross"]
    assert all(filled[clause.clause_id] == "met" for clause in others)


def test_description_text_does_not_change_leader_clauses():
    records, skills = _world()
    card = records[13967]["card"]
    skill = next(item for item in records[13967]["skills"] if item["id"] == card["leaderSkillId"])
    rewritten = {skill_id: dict(item, description="rewritten") for skill_id, item in skills.items()}
    original = [clause.model_dump() for clause in leader_clauses(decode_skill(skill, skills))]
    changed = [clause.model_dump() for clause in leader_clauses(decode_skill(skill, rewritten))]
    assert changed == original


def test_gaps_doc_names_every_unsupported_fixture_effect():
    records, skills = _world()
    text = GAPS.read_text(encoding="utf-8")
    unsupported = []
    for record in records.values():
        card = record["card"]
        for key in ("activeSkillId", "leaderSkillId"):
            skill = next(item for item in record["skills"] if item["id"] == card[key])
            decoded = _flat(decode_skill(skill, skills))
            unsupported.extend(effect for effect in decoded if effect.kind == "unsupported")
    assert unsupported
    for effect in unsupported:
        assert f"| {effect.skill_id} |" in text
        assert f"| {effect.source_type} |" in text
    assert "26756" in text
    assert "loops" in text
    assert "21122" in text


def _flat(effects):
    found = []
    for effect in effects:
        if effect.kind == "evolving_skill":
            for stage in effect.parameters.stages:
                found.extend(_flat(stage.effects))
            continue
        found.append(effect)
    return found
