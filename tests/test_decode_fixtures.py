"""Typed effects for the pinned-snapshot regression cards.

Expectations are the fixture's own skill records and the decoder's parameter
models. Description text is not an input.
"""

import json

from conftest import load_regression_cards, regression_skill_index

from padbox.mechanics.decode import (
    DECODER_VERSION,
    AbsorbVoidParameters,
    AwakeningTriggerParameters,
    CreateOrbsParameters,
    DelayParameters,
    EvolvingParameters,
    LinkedOrbParameters,
    OrbConvertParameters,
    RecordedParameters,
    TransformParameters,
    decode_awakening,
    decode_skill,
    read_skill,
)
from padbox.mechanics.explain import roles_from


def _records() -> dict[int, dict]:
    return {int(record["monster_id"]): record for record in load_regression_cards()}


def _index() -> dict[int, dict]:
    return regression_skill_index(load_regression_cards())


def _root(record: dict, role: str) -> dict:
    skill_id = record["card"]["activeSkillId" if role == "active" else "leaderSkillId"]
    return next(skill for skill in record["skills"] if skill["id"] == skill_id)


def test_syntax_ignores_description_text():
    skill = {"id": 18, "type": 18, "params": [4], "description": "this must not be parsed"}
    assert read_skill(skill).model_dump() == {
        "skill_id": 18,
        "source_type": 18,
        "raw_parameters": [4],
    }
    changed = dict(skill, description="a different sentence")
    assert [effect.model_dump() for effect in decode_skill(skill, {})] == [
        effect.model_dump() for effect in decode_skill(changed, {})
    ]


def test_fixture_effects_carry_provenance_and_typed_parameters():
    index = _index()
    records = _records()
    unsupported = []
    verified = []
    for record in records.values():
        for skill in record["skills"]:
            if skill["type"] in {116, 138, 232}:
                continue
            for effect in decode_skill(skill, index):
                assert effect.provenance is not None
                assert effect.provenance.decoder_version == DECODER_VERSION
                assert effect.provenance.source.endswith(f"type:{effect.source_type}")
                assert effect.parameters is not None
                if effect.kind == "unsupported":
                    assert effect.evidence == "unknown"
                    assert effect.provenance.verification_status == "unsupported"
                    assert effect.provenance.evidence_level == "unsupported"
                    unsupported.append(effect)
                elif effect.evidence == "verified":
                    assert effect.provenance.verification_status == "verified"
                    assert effect.provenance.evidence_level == "verified"
                    assert effect.provenance.unknown_parameters == []
                    assert not isinstance(effect.parameters, RecordedParameters)
                    verified.append(effect)
    assert unsupported
    assert verified
    assert all(effect.provenance.unknown_parameters for effect in unsupported)


def test_roze_transform_and_leader_clauses_stay_separate():
    index = _index()
    records = _records()
    active = decode_skill(_root(records[13966], "active"), index)
    transform = next(effect for effect in active if effect.kind == "transform")
    assert isinstance(transform.parameters, TransformParameters)
    assert transform.parameters.monster_id == records[13966]["card"]["henshinTo"][0]
    assert transform.provenance.verification_status == "verified"

    leader = decode_skill(_root(records[13967], "leader"), index)
    shield = next(effect for effect in leader if effect.kind == "linked_orb_leader")
    cross = next(effect for effect in leader if effect.kind == "awakening_trigger")
    assert isinstance(shield.parameters, LinkedOrbParameters)
    assert isinstance(cross.parameters, AwakeningTriggerParameters)
    assert shield.parameters.attributes == shield.details["attributes"]
    assert shield.parameters.minimum_linked == shield.details["minimum_linked"]
    assert shield.parameters.shield_percent == shield.details["shield_percent"]
    assert "light" in shield.parameters.attributes
    assert cross.parameters.trigger == "cross_attack"
    assert cross.parameters.fixed_damage == cross.details["fixed_damage"]
    assert shield.provenance.unknown_parameters == []
    assert "minimum_linked" in shield.provenance.supported_parameters
    assert "trigger" in cross.provenance.supported_parameters


def test_saphira_transform_stays_with_its_orb_creation():
    index = _index()
    record = _records()[7095]
    effects = decode_skill(_root(record, "active"), index)
    transform = next(effect for effect in effects if effect.kind == "transform")
    created = next(effect for effect in effects if effect.kind == "create_orbs")
    assert isinstance(transform.parameters, TransformParameters)
    assert transform.parameters.monster_id == record["card"]["henshinTo"][0]
    assert isinstance(created.parameters, CreateOrbsParameters)
    assert created.parameters.count == created.details["count"]
    assert created.parameters.colors == created.details["attributes"]
    assert created.parameters.count > 0
    assert created.parameters.colors


def test_apollo_conversion_keeps_source_colors_and_skyfall_duration():
    index = _index()
    effects = decode_skill(_root(_records()[7844], "active"), index)
    converted = next(effect for effect in effects if effect.kind == "orb_convert")
    skyfall = next(effect for effect in effects if effect.kind == "skyfall")
    assert isinstance(converted.parameters, OrbConvertParameters)
    assert converted.parameters.source_colors == converted.details["from"]
    assert converted.parameters.destination == converted.details["to"]
    assert len(converted.parameters.destination) == 1
    assert skyfall.parameters.orbs == skyfall.details["orbs"]
    assert skyfall.parameters.duration.start_turn == skyfall.details["start_turn"]
    assert skyfall.parameters.duration.end_turn == skyfall.details["end_turn"]
    assert skyfall.parameters.rate == skyfall.details["rate"]


def test_paladin_absorb_kinds_stay_one_effect():
    index = _index()
    effects = decode_skill(_root(_records()[7949], "active"), index)
    voids = [effect for effect in effects if effect.kind == "absorb_void"]
    assert len(voids) == 1
    assert isinstance(voids[0].parameters, AbsorbVoidParameters)
    assert voids[0].parameters.kinds == voids[0].details["kinds"]
    assert set(voids[0].parameters.kinds) >= {"attribute", "damage"}
    assert voids[0].parameters.duration.turns == voids[0].details["turns"]


def test_gaia_evolving_stages_stay_grouped():
    index = _index()
    active = _root(_records()[8336], "active")
    effects = decode_skill(active, index)
    assert [effect.kind for effect in effects] == ["evolving_skill"]
    evolving = effects[0]
    assert isinstance(evolving.parameters, EvolvingParameters)
    assert evolving.parameters.loops is None
    assert evolving.provenance.evidence_level == "syntactic"
    assert evolving.provenance.verification_status == "unverified"
    assert "loops" in evolving.provenance.unknown_parameters
    assert "stages" in evolving.provenance.supported_parameters
    assert [stage.skill_id for stage in evolving.parameters.stages] == active["params"]
    flat = [effect.kind for stage in evolving.parameters.stages for effect in stage.effects]
    assert len(flat) > 1
    assert "evolving_skill" not in flat
    assert roles_from(effects)["unknown_effects"] == 1
    first = evolving.parameters.stages[0]
    assert isinstance(first.effects[0].parameters, DelayParameters)
    assert first.effects[0].provenance.verification_status == "verified"
    assert roles_from(first.effects)["unknown_effects"] == 0


def test_fixture_awakenings_are_grouped_source_encoded_effects():
    awakenings = _records()[13966]["card"]["awakenings"]
    assert 21 in awakenings
    boost = decode_awakening(21)
    assert boost.kind == "awakening"
    assert boost.parameters.effects["skill_boost"] == 1
    assert boost.provenance.evidence_level == "source_encoded"
    assert boost.provenance.verification_status == "unverified"
    assert boost.provenance.source == "awakening:21"
    missing = decode_awakening(999999)
    assert missing.kind == "unsupported"
    assert missing.provenance.verification_status == "unsupported"


def test_description_on_a_fixture_skill_does_not_change_the_effect():
    index = _index()
    skill = _root(_records()[13966], "active")
    changed = dict(skill, description="ignore this text")
    assert json.dumps(
        [effect.model_dump() for effect in decode_skill(skill, index)], sort_keys=True
    ) == json.dumps(
        [effect.model_dump() for effect in decode_skill(changed, index)], sort_keys=True
    )
