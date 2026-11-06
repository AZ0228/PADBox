"""Lineup explanations from the pinned regression cards.

Each card's removal is a second evaluation. The tests assert the trade-offs
that evaluation reports. They do not treat either lineup as the winner.
Helpers are named on every team. A friend's helper is not assumed.
"""

import json
from contextlib import closing
from pathlib import Path

import pytest
from pydantic import ValidationError

from padbox.evaluator import compare_teams, evaluate_team
from padbox.evaluator.explain import CardExplanation, ComponentChange, RemovalResult
from padbox.mechanics.result import CheckResult
from padbox.mechanics.skill_state import cooldown_spec

CARDS = Path(__file__).resolve().parent / "fixtures" / "cards"

ROZE = 13967
GAIA = 8336
APOLLO = 7844
VENUS = 10756
PALADIN = 7949
CIEL = 4834
KUSHI = 4206
RATHIOS = 3939
PIXEL = 3821
LEONA = 7712
SAPHIRA = 7095
SAPHIRA_BATTLE = 7096


def _records() -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(CARDS.glob("*.json"))]


def _skill_index() -> dict[int, dict]:
    index: dict[int, dict] = {}
    for record in _records():
        for skill in record["skills"]:
            index[int(skill["id"])] = skill
    return index


def _box(store, monsters: list[int]) -> None:
    document = json.dumps(
        {
            "schema_version": 1,
            "server": "NA",
            "monsters": [
                {"monster_id": monster_id, "quantity": 1, "confirmed": True}
                for monster_id in monsters
            ],
            "unresolved": [],
            "complete": True,
        }
    )
    with closing(store.connect()) as db, db:
        db.execute("INSERT INTO box_state VALUES (1, 1, ?, NULL)", (document,))


def _fast() -> dict:
    return {"name": "general", "seed": 0, "turns": 2, "simulations": 2}


def _sub(owned: int, form: int | None = None, assist: dict | None = None) -> dict:
    row = {"owned_id": owned, "form_id": owned if form is None else form}
    if assist is not None:
        row["assist"] = assist
    return row


def _minimum(monster_id: int) -> int:
    record = next(item for item in _records() if item["monster_id"] == monster_id)
    skill_id = record["card"]["activeSkillId"]
    skill = next(item for item in record["skills"] if item["id"] == skill_id)
    minimum = cooldown_spec(skill).minimum
    assert isinstance(minimum, int)
    return minimum


def _card(report, slot: str) -> CardExplanation:
    return next(card for card in report.cards if card.slot == slot)


def _change(card: CardExplanation, name: str) -> ComponentChange:
    return next(item for item in card.removal.changes if item.component == name)


def _diff(comparison, name: str):
    return next(item for item in comparison.differences if item.name == name)


def _no_alternative(cards: list[CardExplanation]) -> None:
    for card in cards:
        assert card.alternatives_considered == []
        assert card.ranks_higher_because is None
        assert "ranks higher" not in card.role
        assert "ranks higher" not in card.removal.detail


@pytest.fixture
def lineup_store(regression_store):
    _box(regression_store, [int(record["monster_id"]) for record in _records()])
    return regression_store


def test_lineups_report_tradeoffs_without_selecting_a_winner(lineup_store):
    """Rozé's subs against the earlier five-card lineup. Leona is the helper."""
    index = _skill_index()
    roze = {
        "leader_id": ROZE,
        "helper_id": ROZE,
        "subs": [_sub(GAIA), _sub(APOLLO), _sub(VENUS), _sub(PALADIN)],
    }
    earlier = {
        "leader_id": ROZE,
        "helper_id": LEONA,
        "subs": [_sub(CIEL), _sub(KUSHI), _sub(RATHIOS), _sub(PIXEL)],
    }
    comparison = compare_teams(
        roze,
        earlier,
        _fast(),
        store=lineup_store,
        skill_index=index,
        left_label="roze",
        right_label="earlier",
    )
    assert comparison.selected_team is None
    assert comparison.left_score is None
    assert comparison.right_score is None
    assert "neither team is selected" in comparison.note
    assert "No card was replaced by an alternative" in comparison.note
    _no_alternative(comparison.left_cards + comparison.right_cards)

    boost = _diff(comparison, "skill_boost_total")
    required = _diff(comparison, "skill_boost_required")
    assert boost.left != boost.right
    assert boost.left < required.left
    assert boost.right < required.right
    assert _diff(comparison, "transform").left == CheckResult.FAIL.value
    assert _diff(comparison, "transform").right == CheckResult.FAIL.value

    paladin = next(card for card in comparison.left_cards if card.monster_id == PALADIN)
    kushi = next(card for card in comparison.right_cards if card.monster_id == KUSHI)
    gaia = next(card for card in comparison.left_cards if card.monster_id == GAIA)
    assert "absorb_void:attribute" in paladin.unique_contribution
    assert "absorb_void:damage" in paladin.unique_contribution
    assert "absorb_void:attribute" in kushi.unique_contribution
    assert "evolving_skill" in gaia.unresolved_effects
    assert "Gaia" in str(_diff(comparison, "unresolved_cards").left)
    leader = next(card for card in comparison.left_cards if card.slot == "leader")
    assert any(item.startswith("leader clause:") for item in leader.unique_contribution)
    assert leader.removal.skill_boost_after is None
    assert "No other leader was evaluated" in leader.removal.detail
    assert _change(leader, "activation_consistency").change == "became_unresolved"


def test_insufficient_skill_boosts_remain_short_without_saphira(lineup_store):
    index = _skill_index()
    report = evaluate_team(
        {
            "leader_id": ROZE,
            "helper_id": LEONA,
            "subs": [
                _sub(SAPHIRA, SAPHIRA_BATTLE),
                _sub(RATHIOS),
                _sub(PIXEL),
                _sub(GAIA),
            ],
        },
        _fast(),
        store=lineup_store,
        skill_index=index,
    )
    assert report.transform_feasible is CheckResult.FAIL
    boost = report.validation["skill_boost"]
    assert boost["total"] < boost["required"]
    saphira = _card(report, "sub 1")
    assert saphira.monster_id == SAPHIRA_BATTLE
    assert saphira.removal.skill_boost_required_before > saphira.removal.skill_boost_required_after
    assert saphira.removal.transform_before == CheckResult.FAIL.value
    assert saphira.removal.transform_after == CheckResult.FAIL.value
    assert saphira.removal.skill_boost_after < saphira.removal.skill_boost_required_after
    _no_alternative(report.cards)


def test_overlapping_clerics_keep_the_shared_clear(lineup_store):
    index = _skill_index()
    report = evaluate_team(
        {
            "leader_id": PIXEL,
            "helper_id": RATHIOS,
            "subs": [_sub(CIEL), _sub(LEONA), _sub(APOLLO), _sub(PALADIN)],
        },
        _fast(),
        store=lineup_store,
        skill_index=index,
    )
    ciel = _card(report, "sub 1")
    leona = _card(report, "sub 2")
    shared = {"awoken_bind_clear", "unmatchable_clear"}
    assert shared.isdisjoint(ciel.unique_contribution)
    assert shared.isdisjoint(leona.unique_contribution)
    assert "rcv_buff" in leona.unique_contribution
    assert "bind_clear" in ciel.unique_contribution
    assert shared.isdisjoint(leona.removal.lost_kinds)
    assert "rcv_buff" in leona.removal.lost_kinds
    _no_alternative(report.cards)


def test_a_longer_assist_cooldown_hides_the_host_active(lineup_store):
    index = _skill_index()
    report = evaluate_team(
        {
            "leader_id": PIXEL,
            "helper_id": PALADIN,
            "subs": [
                _sub(APOLLO, assist={"owned_id": KUSHI, "form_id": KUSHI}),
                _sub(CIEL),
                _sub(LEONA),
                _sub(RATHIOS),
            ],
        },
        _fast(),
        store=lineup_store,
        skill_index=index,
    )
    structure = next(item for item in report.constraints if item.name == "team_structure")
    assert structure.result is CheckResult.FAIL
    assert "not an assist equip" in structure.detail
    assist = _card(report, "sub 1 assist")
    assert assist.monster_id == KUSHI
    assert assist.replaced_host_cooldown == _minimum(APOLLO)
    assert assist.active_cooldown == _minimum(KUSHI)
    assert assist.active_cooldown > assist.replaced_host_cooldown
    assert str(assist.replaced_host_cooldown) in assist.removal.detail
    assert str(assist.active_cooldown) in assist.removal.detail
    assert "attribute_atk" in assist.removal.gained_kinds
    assert "skyfall" in assist.removal.gained_kinds
    _no_alternative(report.cards)


def test_incomplete_decoding_stays_unresolved_on_gaia(lineup_store):
    index = _skill_index()
    report = evaluate_team(
        {
            "leader_id": PIXEL,
            "helper_id": LEONA,
            "subs": [_sub(GAIA), _sub(CIEL), _sub(RATHIOS), _sub(PALADIN)],
        },
        _fast(),
        store=lineup_store,
        skill_index=index,
    )
    gaia = _card(report, "sub 1")
    assert gaia.name == "Ultraman Gaia (V2)"
    assert gaia.unresolved_effects == ["evolving_skill"]
    assert "evolving_skill" not in gaia.unique_contribution
    assert "Unresolved active effects: evolving_skill." in gaia.role
    assert gaia.alternatives_considered == []
    assert gaia.ranks_higher_because is None
    row = next(item for item in report.validation["unknowns"] if item["form_id"] == GAIA)
    assert row["form_name"] == gaia.name
    assert row["effects"]
    assert any(row["where"] in line and "unresolved" in line for line in report.unknowns)


def test_a_ranking_reason_requires_an_evaluated_alternative():
    removal = RemovalResult(
        feasibility=CheckResult.FAIL,
        team_complete=False,
        changes=[],
        lost_kinds=[],
        gained_kinds=[],
        skill_boost_before=None,
        skill_boost_after=None,
        skill_boost_required_before=None,
        skill_boost_required_after=None,
        transform_before=None,
        transform_after=None,
        detail="Re-evaluated without this card.",
    )
    with pytest.raises(ValidationError):
        CardExplanation(
            slot="sub 1",
            monster_id=1,
            owned_id=1,
            name="Card",
            role="Primary active role: bind clear.",
            unique_contribution=[],
            unresolved_effects=[],
            active_cooldown=1,
            removal=removal,
            ranks_higher_because="It ranks higher than another card.",
        )
