"""Greedy matching on a 6×5 board.

Orb counts and colors come from the decoded skill. A line of five and a plus
are different matches, and each turn's clause verdict is a policy approximation.
"""

from conftest import load_regression_cards, regression_skill_index

from padbox.evaluator.sim import (
    BOARD_COLUMNS,
    BOARD_ROWS,
    MemberInput,
    PolicyTarget,
    empty_board,
    open_battle,
    step,
)
from padbox.mechanics.decode import decode_skill


def _raw(type_: int, skill_id: int, params: list[int], initial: int, levels: int) -> dict:
    return {
        "id": skill_id,
        "name": f"skill {skill_id}",
        "description": "this text must not be read",
        "type": type_,
        "params": params,
        "initialCooldown": initial,
        "maxLevel": levels,
    }


def _card(monster_id: int, active_id: int, leader_id: int = 0) -> dict:
    return {
        "id": monster_id,
        "name": f"Monster {monster_id}",
        "attrs": [3],
        "types": [1],
        "hp": {"max": 1000},
        "atk": {"max": 100},
        "rcv": {"max": 50},
        "activeSkillId": active_id,
        "leaderSkillId": leader_id,
        "awakenings": [],
    }


def _open(skill: dict, skills: dict | None = None, *, leader_id: int = 0, seed: int = 0):
    index = {skill["id"]: skill}
    if skills:
        index.update(skills)
    card = _card(1, skill["id"], leader_id)
    return open_battle(
        [MemberInput(role="leader", card=card, awakenings=[])],
        index,
        seed=seed,
    )


def _set(state, column: int, row: int, color: str, *, locked: bool = False) -> None:
    cell = state.board.cells[row * state.board.columns + column]
    cell.color = color
    cell.locked = locked


def _paint(state, color: str) -> None:
    for row in range(state.board.rows):
        for column in range(state.board.columns):
            _set(state, column, row, color)


def _checker(state) -> None:
    for row in range(state.board.rows):
        for column in range(state.board.columns):
            color = "fire" if (row + column) % 2 == 0 else "water"
            _set(state, column, row, color)


def _at(state, column: int, row: int):
    return state.board.cells[row * state.board.columns + column]


def _roze():
    records = {int(record["monster_id"]): record for record in load_regression_cards()}
    card = records[13967]["card"]
    skills = regression_skill_index(list(records.values()))
    return open_battle(
        [MemberInput(role="leader", card=card, awakenings=[])],
        skills,
    )


def _clause(state, kind: str):
    return next(clause for clause in state.leader_clauses if clause.condition.kind == kind)


def _status(state, kind: str) -> str:
    clause = _clause(state, kind)
    verdict = next(item for item in state.leader_verdicts if item.clause_id == clause.clause_id)
    return verdict.status


def _light_cells(column_rows: list[tuple[int, int]], state) -> None:
    _checker(state)
    for column, row in column_rows:
        _set(state, column, row, "light")


def _plus() -> list[tuple[int, int]]:
    return [(2, 1), (1, 1), (3, 1), (2, 0), (2, 2)]


def _line() -> list[tuple[int, int]]:
    return [(column, 4) for column in range(5)]


def test_line_of_five_light_is_linked_and_not_a_cross():
    state = _roze()
    linked = _clause(state, "linked")
    assert linked.condition.attributes == ["light"]
    assert linked.condition.minimum == 5
    _light_cells(_line(), state)
    step(state)
    report = state.turn_reports[0]
    assert report.basis == "policy_approximation"
    assert _status(state, "linked") == "met"
    assert _status(state, "cross") == "unmet"
    light = next(match for match in report.matches if match.color == "light")
    assert light.shape == "group"
    assert light.size == 5
    assert report.skyfall_count == 5
    assert state.leader_verdicts == report.leader_verdicts
    assert state.helper_verdicts == []


def test_light_plus_is_a_cross_and_not_a_linked_match():
    state = _roze()
    _light_cells(_plus(), state)
    step(state)
    report = state.turn_reports[0]
    assert _status(state, "cross") == "met"
    assert _status(state, "linked") == "unmet"
    cross = next(match for match in report.matches if match.shape == "cross")
    assert cross.color == "light"
    assert cross.size == 5
    assert not any(match.color == "light" and match.shape != "cross" for match in report.matches)
    assert report.skyfall_count == 5


def test_plus_and_line_satisfy_both_clauses():
    state = _roze()
    _light_cells(_plus() + _line(), state)
    step(state)
    report = state.turn_reports[0]
    assert _status(state, "cross") == "met"
    assert _status(state, "linked") == "met"
    cross = next(match for match in report.matches if match.shape == "cross")
    linked = next(
        match
        for match in report.matches
        if match.color == "light" and match.shape != "cross"
    )
    plus_cells = {row * BOARD_COLUMNS + column for column, row in _plus()}
    line_cells = {row * BOARD_COLUMNS + column for column, row in _line()}
    assert set(cross.cells) == plus_cells
    assert set(linked.cells) == line_cells
    assert report.skyfall_count == 10


def test_full_light_board_is_one_linked_match():
    state = _roze()
    _paint(state, "light")
    step(state)
    report = state.turn_reports[0]
    assert _status(state, "linked") == "met"
    assert _status(state, "cross") == "unmet"
    assert [(match.color, match.size, match.shape) for match in report.matches] == [
        ("light", BOARD_COLUMNS * BOARD_ROWS, "group")
    ]
    assert report.skyfall_count == BOARD_COLUMNS * BOARD_ROWS


def test_hearts_stay_unless_recovery_is_needed():
    hearts = [(column, 4) for column in range(3)]

    kept = _roze()
    _light_cells(_plus(), kept)
    for column, row in hearts:
        _set(kept, column, row, "heal")
    step(kept)
    kept_report = kept.turn_reports[0]
    assert _status(kept, "cross") == "met"
    assert kept_report.heal_orbs_matched == 0
    assert kept_report.heal_orbs_kept == 3
    assert [_at(kept, column, row).color for column, row in hearts] == ["heal", "heal", "heal"]

    used = _roze()
    _light_cells(_plus(), used)
    for column, row in hearts:
        _set(used, column, row, "heal")
    step(used, PolicyTarget(recovery=True))
    used_report = used.turn_reports[0]
    assert used_report.heal_orbs_matched == 3
    assert used_report.heal_orbs_kept == 0
    assert used_report.skyfall_count == 8


def test_locked_orb_breaks_a_line():
    state = _roze()
    _light_cells(_line(), state)
    _set(state, 2, 4, "light", locked=True)
    step(state)
    assert _status(state, "linked") == "unmet"
    assert _status(state, "cross") == "unmet"
    assert _at(state, 2, 4).locked is True
    assert _at(state, 2, 4).color == "light"
    assert not any(match.color == "light" for match in state.turn_reports[0].matches)


def test_saphira_create_places_each_decoded_color():
    records = {int(record["monster_id"]): record for record in load_regression_cards()}
    skill = next(item for item in records[7095]["skills"] if item["id"] == 21112)
    effect = decode_skill(skill, {skill["id"]: skill})[0]
    assert effect.kind == "create_orbs"
    state = _open(skill)
    _paint(state, "wood")
    state.board.unmatchable_turns = 1
    step(state, PolicyTarget(mechanics=["create_orbs"]))
    for color in effect.details["attributes"]:
        found = sum(cell.color == color for cell in state.board.cells)
        assert found == effect.details["count"]
    assert not any("placed" in note for note in state.unknowns)


def test_gaia_replace_uses_count_and_excluded_color():
    records = {int(record["monster_id"]): record for record in load_regression_cards()}
    skill = next(item for item in records[8336]["skills"] if item["id"] == 26762)
    effect = decode_skill(skill, {skill["id"]: skill})[0]
    assert effect.kind == "orb_replace"
    assert effect.details["excluded"] == ["heal"]
    assert effect.details["to"] == ["light"]
    state = _open(skill)
    _paint(state, "fire")
    _set(state, 0, 0, "heal")
    _set(state, 1, 0, "fire", locked=True)
    state.board.unmatchable_turns = 1
    step(state, PolicyTarget(mechanics=["orb_replace"]))
    lights = sum(cell.color == "light" for cell in state.board.cells)
    assert lights == effect.details["count"]
    assert _at(state, 0, 0).color == "heal"
    assert _at(state, 1, 0).color == "fire"
    assert _at(state, 1, 0).locked is True


def test_apollo_convert_uses_source_colors():
    records = {int(record["monster_id"]): record for record in load_regression_cards()}
    original = next(item for item in records[7844]["skills"] if item["id"] == 3925)
    effect = decode_skill(original, {original["id"]: original})[0]
    assert effect.kind == "orb_convert"
    sources = set(effect.details["from"])
    destination = effect.details["to"][0]
    skill = dict(original)
    skill["initialCooldown"] = 1
    skill["maxLevel"] = 1
    state = _open(skill)
    _paint(state, "water")
    for column in range(4):
        _set(state, column, 0, "dark")
    for column in range(3):
        _set(state, column, 1, "heal")
    _set(state, 5, 0, "dark", locked=True)
    convertible = sum(
        1
        for cell in state.board.cells
        if not cell.locked and cell.color in sources
    )
    state.board.unmatchable_turns = 1
    step(state, PolicyTarget(mechanics=["orb_convert"]))
    assert sum(cell.color == destination for cell in state.board.cells) == convertible
    assert _at(state, 5, 0).color == "dark"
    assert _at(state, 5, 0).locked is True
    assert _at(state, 0, 2).color == "water"


def test_column_changes_follow_the_decoder_lines():
    cases = (
        ([1, 8], "the leftmost column", [0]),
        ([3, 8], "the 2 leftmost columns", [0, 1]),
        ([5, 8], "the leftmost column and the 3rd column from the left", [0, 2]),
    )
    for params, phrase, columns in cases:
        skill = _raw(127, 1, params, 1, 1)
        effect = decode_skill(skill, {1: skill})[0]
        assert effect.kind == "column_change"
        assert effect.details["lines"][0]["where"] == phrase
        assert effect.details["lines"][0]["orbs"] == ["light"]
        state = _open(skill)
        _paint(state, "dark")
        state.board.unmatchable_turns = 1
        step(state, PolicyTarget(mechanics=["column_change"]))
        for column in range(BOARD_COLUMNS):
            for row in range(BOARD_ROWS):
                expected = "light" if column in columns else "dark"
                assert _at(state, column, row).color == expected


def test_top_row_matches_as_a_row_and_not_as_a_cross():
    trigger = _raw(271, 2, [256, 200], 0, 0)
    leader = _raw(138, 3, [2], 0, 0)
    active = _raw(128, 1, [1, 8], 1, 1)
    effect = decode_skill(active, {1: active})[0]
    assert effect.details["lines"][0]["where"] == "the top row"
    state = _open(active, {2: trigger, 3: leader}, leader_id=3)
    assert state.leader_clauses[0].condition.kind == "row"
    assert state.leader_clauses[0].condition.attributes == ["light"]
    _checker(state)
    step(state, PolicyTarget(mechanics=["row_change"]))
    report = state.turn_reports[0]
    row = next(match for match in report.matches if match.color == "light")
    assert row.shape == "row"
    assert row.cells == list(range(BOARD_COLUMNS))
    assert _status(state, "row") == "met"
    assert report.basis == "policy_approximation"


def test_full_board_pattern_is_row_major_from_the_top_left():
    params = [0] * BOARD_COLUMNS + [1] * (BOARD_COLUMNS * (BOARD_ROWS - 1))
    skill = _raw(71, 1, params, 1, 1)
    effect = decode_skill(skill, {1: skill})[0]
    assert effect.kind == "board_change"
    assert effect.details["orbs"][:BOARD_COLUMNS] == ["fire"] * BOARD_COLUMNS
    state = _open(skill)
    _paint(state, "dark")
    _set(state, 0, 0, "dark", locked=True)
    state.board.unmatchable_turns = 1
    step(state, PolicyTarget(mechanics=["board_change"]))
    assert [cell.color for cell in state.board.cells[:BOARD_COLUMNS]] == ["fire"] * BOARD_COLUMNS
    assert all(cell.color == "water" for cell in state.board.cells[BOARD_COLUMNS:])
    assert all(cell.locked is False for cell in state.board.cells)


def test_only_skyfall_fills_matched_holes_without_a_cascade():
    skill = _raw(126, 1, [8, 1, 1, 100], 1, 1)
    effect = decode_skill(skill, {1: skill})[0]
    assert effect.details["mode"] == "only"
    assert effect.details["orbs"] == ["light"]
    state = _open(skill)
    _checker(state)
    for column in range(3):
        _set(state, column, 0, "light")
    held = _at(state, 3, 0).color
    step(state, PolicyTarget(mechanics=["skyfall"]))
    report = state.turn_reports[0]
    assert report.skyfall_count == 3
    assert [_at(state, column, 0).color for column in range(3)] == ["light", "light", "light"]
    assert _at(state, 3, 0).color == held


def test_increased_skyfall_has_no_modeled_weight():
    records = {int(record["monster_id"]): record for record in load_regression_cards()}
    original = next(item for item in records[7844]["skills"] if item["id"] == 3926)
    assert original["params"] == [8, 8, 8, 15]
    skill = dict(original)
    skill["initialCooldown"] = 1
    skill["maxLevel"] = 1
    effect = decode_skill(skill, {skill["id"]: skill})[0]
    assert effect.details["mode"] == "increased"
    state = _open(skill)
    _checker(state)
    for column in range(3):
        _set(state, column, 0, "wood")
    step(state, PolicyTarget(mechanics=["skyfall"]))
    assert state.turn_reports[0].skyfall_count == 3
    assert any("no modeled weight" in note for note in state.unknowns)


def test_same_seed_replays_the_matched_board():
    skill = _raw(18, 1, [1], 99, 1)

    def colors(seed: int) -> tuple:
        state = _open(skill, seed=seed)
        step(state)
        assert state.turn_reports[0].basis == "policy_approximation"
        return tuple(cell.color for cell in state.board.cells)

    assert colors(0) == colors(0)
    assert colors(0) != colors(1)


def test_each_turn_keeps_its_own_verdicts():
    state = _roze()
    _light_cells(_line(), state)
    step(state)
    first = state.turn_reports[0]
    assert _status(state, "linked") == "met"
    step(state)
    assert len(state.turn_reports) == 2
    assert state.turn_reports[0] == first
    assert state.leader_verdicts == state.turn_reports[1].leader_verdicts
    assert all(report.basis == "policy_approximation" for report in state.turn_reports)


def test_unsupported_board_leaves_board_clauses_unknown():
    state = _roze()
    wide = empty_board(7, 6)
    for cell in wide.cells:
        cell.color = "light"
    state.board = wide
    step(state)
    report = state.turn_reports[0]
    assert report.board_observed is False
    assert report.matches == []
    assert _status(state, "linked") == "unknown"
    assert _status(state, "cross") == "unknown"
    assert _status(state, "unconditional") == "met"


def test_unmatchable_board_misses_linked_and_cross():
    state = _roze()
    _paint(state, "light")
    state.board.unmatchable_turns = 1
    step(state)
    assert state.board.unmatchable_turns == 0
    assert state.turn_reports[0].board_observed is True
    assert state.turn_reports[0].matches == []
    assert _status(state, "linked") == "unmet"
    assert _status(state, "cross") == "unmet"
    assert _status(state, "unconditional") == "met"


def test_gravity_drops_orbs_and_a_lock_blocks_them():
    skill = _raw(18, 1, [1], 99, 1)
    falling = _open(skill)
    _checker(falling)
    _set(falling, 0, 0, "dark")
    _set(falling, 0, 1, "dark")
    for row in range(2, 5):
        _set(falling, 0, row, "light")
    step(falling)
    assert [_at(falling, 0, row).color for row in range(5)][3:] == ["dark", "dark"]
    assert falling.turn_reports[0].skyfall_count == 3

    blocked = _open(skill)
    _checker(blocked)
    _set(blocked, 0, 0, "dark")
    _set(blocked, 0, 1, "fire", locked=True)
    for row in range(2, 5):
        _set(blocked, 0, row, "light")
    step(blocked)
    assert _at(blocked, 0, 0).color == "dark"
    assert _at(blocked, 0, 1).locked is True
    assert _at(blocked, 0, 1).color == "fire"


def test_l_shape_counts_as_linked_and_not_as_a_cross():
    linked = _raw(182, 2, [8, 5, 150, 0], 0, 0)
    attack = _raw(271, 3, [4, 200], 0, 0)
    leader = _raw(138, 4, [2, 3], 0, 0)
    active = _raw(18, 1, [1], 99, 1)
    state = _open(active, {2: linked, 3: attack, 4: leader}, leader_id=4)
    assert [clause.condition.kind for clause in state.leader_clauses] == ["linked", "l_shape"]
    _light_cells([(0, 0), (1, 0), (2, 0), (0, 1), (0, 2)], state)
    step(state)
    report = state.turn_reports[0]
    shape = next(match for match in report.matches if match.color == "light")
    assert shape.shape == "l_shape"
    assert shape.size == 5
    assert _status(state, "l_shape") == "met"
    assert _status(state, "linked") == "met"
    assert not any(match.shape == "cross" for match in report.matches)


def test_combo_orbs_leave_their_source_color():
    skill = _raw(190, 1, [8, 42], 1, 1)
    effect = decode_skill(skill, {1: skill})[0]
    assert effect.kind == "combo_convert"
    assert effect.details["from"] == "light"
    state = _open(skill)
    _paint(state, "wood")
    for column in range(4):
        _set(state, column, 0, "light")
    state.board.unmatchable_turns = 1
    step(state, PolicyTarget(mechanics=["combo_convert"]))
    assert sum(cell.color == "light" for cell in state.board.cells) == 0
    assert sum(cell.color == "combo" for cell in state.board.cells) == 4
    assert any("combo orbs" in note for note in state.unknowns)
