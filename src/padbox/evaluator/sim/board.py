"""6×5 board changes, skyfall, and the greedy matching policy.

Skills paint the board with the quantities and colors the decoder recorded.
The match that follows is a policy approximation: it does not search orb paths,
and skyfall orbs are not matched on the turn they appear.
"""

import random
from typing import Literal

from padbox.evaluator.sim.policy import PolicyTarget
from padbox.evaluator.sim.state import (
    BoardState,
    ClauseTag,
    GameState,
    MatchedGroup,
    MemberState,
    TurnReport,
    board_is_supported,
    team_hp_max,
)
from padbox.mechanics.clauses import (
    ClauseVerdict,
    LeaderClause,
    MatchObservation,
    evaluate_clauses,
)
from padbox.mechanics.decode import Effect

_MATCH_COLORS = frozenset({"fire", "water", "wood", "light", "dark", "heal"})
_ATTRIBUTE_COLORS = frozenset({"fire", "water", "wood", "light", "dark"})
_DEFAULT_SKYFALL = ("fire", "water", "wood", "light", "dark", "heal")
_Shape = Literal["cross", "l_shape", "row", "group"]
_BOARD_CLAUSES = frozenset(
    {"linked", "cross", "l_shape", "row", "combo", "multi_color", "awakening"}
)
_ROW_TRIGGER = {
    "fire": "enhanced_fire_rows",
    "water": "enhanced_water_rows",
    "light": "enhanced_light_rows",
    "dark": "enhanced_dark_rows",
}
_COLUMN_BITS = (
    (1, "the leftmost column"),
    (2, "the 2nd column from the left"),
    (4, "the 3rd column from the left"),
    (8, "the 3rd column from the right"),
    (16, "the 2nd column from the right"),
    (32, "the rightmost column"),
)
_COLUMN_GROUPS = {
    3: "the 2 leftmost columns",
    7: "the 3 leftmost columns",
    12: "the 3rd column from the left and the 3rd from the right",
    18: "the 2nd columns from the left and the right",
    33: "the outermost columns",
    35: "the rightmost and the 2 leftmost columns",
    37: "the outermost columns and the 3rd from the left",
    48: "the 2 rightmost columns",
    49: "the leftmost and the 2 rightmost columns",
    51: "the 2 leftmost and the 2 rightmost columns",
    56: "the 3 rightmost columns",
}
_ROW_BITS = (
    (1, "the top row"),
    (2, "the 2nd row from the top"),
    (4, "the 3rd row from the top"),
    (8, "the 2nd row from the bottom"),
    (16, "the bottom row"),
)
_ROW_GROUPS = {
    3: "the 2 top rows",
    10: "the 2nd rows from the top and bottom",
    17: "the top and bottom rows",
    19: "the 2 top rows and the bottom row",
    24: "the 2 bottom rows",
    27: "the 2 top rows and the 2 bottom rows",
}


class _Group:
    def __init__(self, color: str, cells: tuple[int, ...]) -> None:
        self.color = color
        self.cells = cells


def _phrase_indexes(
    bits: tuple[tuple[int, str], ...], groups: dict[int, str]
) -> dict[str, list[int]]:
    """Map a decoder line phrase to indexes. The phrases are the decoder's labels."""
    found: dict[str, list[int]] = {}
    allowed = sum(bit for bit, _label in bits)
    for mask in range(1, allowed + 1):
        if mask & ~allowed:
            continue
        if mask in groups:
            phrase = groups[mask]
        else:
            names = [label for bit, label in bits if mask & bit]
            if len(names) == 1:
                phrase = names[0]
            else:
                phrase = " and ".join(names)
        found[phrase] = [index for index, (bit, _label) in enumerate(bits) if mask & bit]
    return found


_COLUMN_INDEXES = _phrase_indexes(_COLUMN_BITS, _COLUMN_GROUPS)
_ROW_INDEXES = _phrase_indexes(_ROW_BITS, _ROW_GROUPS)


def _orientations(seed: set[tuple[int, int]]) -> frozenset[frozenset[tuple[int, int]]]:
    found: set[frozenset[tuple[int, int]]] = set()
    current = set(seed)
    for _ in range(4):
        for cells in (current, {(-col, row) for col, row in current}):
            min_col = min(col for col, _row in cells)
            min_row = min(row for _col, row in cells)
            found.add(frozenset((col - min_col, row - min_row) for col, row in cells))
        current = {(row, -col) for col, row in current}
    return frozenset(found)


_L_SHAPES = _orientations({(0, 0), (1, 0), (2, 0), (0, 1), (0, 2)})


def apply_board_effect(state: GameState, effect: Effect) -> None:
    """Paint one verified board effect. Unknown quantities are recorded and skipped."""
    kind = effect.kind
    if kind == "create_orbs":
        _create_orbs(state, effect)
    elif kind == "orb_replace":
        _replace_orbs(state, effect)
    elif kind == "orb_convert":
        _convert_orbs(state, effect)
    elif kind == "orb_change":
        _change_orbs(state, effect)
    elif kind == "board_change":
        _paint_board(state, effect)
    elif kind in {"column_change", "row_change"}:
        _paint_lines(state, effect)
    elif kind == "board_refresh":
        _refresh_board(state)
    elif kind == "create_combo_orbs":
        _create_combo(state, effect)
    elif kind == "combo_convert":
        _convert_combo(state, effect)


def install_board(state: GameState, colors: list[str]) -> None:
    """Replace the 6×5 board with a fixed row-major color list. Locks are cleared."""
    cells = state.board.cells
    if not state.board.is_standard() or len(colors) != len(cells):
        raise ValueError("a scenario board must cover the 6×5 board")
    if any(not isinstance(color, str) or not color for color in colors):
        raise ValueError("a scenario board cell needs a color")
    for cell, color in zip(cells, colors, strict=True):
        cell.color = color
        cell.locked = False


def fill_empty(state: GameState) -> int:
    """Fill unlocked empty cells from the battle's skyfall stream. Returns how many."""
    if not state.board.is_standard():
        return 0
    holes = [cell for cell in state.board.cells if not cell.locked and cell.color is None]
    if not holes:
        return 0
    pool = _skyfall_pool(state)
    if not pool:
        state.unknowns.append(f"Turn {state.turn}: skyfall has no colors.")
        return 0
    rng = _rng(state)
    for cell in holes:
        cell.color = rng.choice(pool)
    return len(holes)


def resolve_turn(state: GameState, target: PolicyTarget, opening_skyfall: int) -> TurnReport:
    """Match the board, drop orbs, skyfall, and judge leader and helper clauses."""
    groups: list[tuple[_Group, _Shape]] = []
    post_skyfall = 0
    heal_matched = 0
    heal_kept = 0
    if board_is_supported(state) and state.board.unmatchable_turns <= 0:
        _note_combo_orbs(state)
        groups = _select(state, target)
        heal_matched, heal_kept = _heals(state, groups)
        _clear(state, groups)
        _gravity(state.board)
        post_skyfall = fill_empty(state)
        observed = True
    elif board_is_supported(state):
        _note_combo_orbs(state)
        heal_matched, heal_kept = _heals(state, [])
        observed = True
    else:
        observed = False
        state.unknowns.append(
            f"Turn {state.turn}: the board is unsupported, so clauses were not matched."
        )
    matches = [_matched(group, shape) for group, shape in groups]
    return TurnReport(
        turn=state.turn,
        basis="policy_approximation",
        matches=matches,
        heal_orbs_matched=heal_matched,
        heal_orbs_kept=heal_kept,
        skyfall_count=opening_skyfall + post_skyfall,
        board_observed=observed,
        leader_verdicts=_judge(state, state.leader_clauses, matches, "leader", observed),
        helper_verdicts=_judge(state, state.helper_clauses, matches, "helper", observed),
        leader_tags=_clause_tags(state.leader_clauses),
        helper_tags=_clause_tags(state.helper_clauses),
    )


def _clause_tags(clauses: list[LeaderClause]) -> list[ClauseTag]:
    tags: list[ClauseTag] = []
    for clause in clauses:
        offensive = False
        defensive = False
        if clause.evidence == "verified":
            for effect in clause.effects:
                if effect.kind in {"added_combos", "fixed_damage"}:
                    offensive = True
                elif effect.kind == "multiplier" and effect.stat in {None, "atk"}:
                    offensive = True
                elif effect.kind in {"damage_reduction", "shield"}:
                    defensive = True
        tags.append(
            ClauseTag(
                clause_id=clause.clause_id,
                offensive=offensive,
                defensive=defensive,
            )
        )
    return tags


def _rng(state: GameState) -> random.Random:
    rng = state._rng
    if not isinstance(rng, random.Random):
        rng = random.Random(state.seed)
        state._rng = rng
    return rng


def _skyfall_pool(state: GameState) -> list[str]:
    only: list[str] | None = None
    increased = False
    for effect in state.effects:
        if effect.kind != "skyfall" or effect.unknown_duration:
            continue
        if effect.mode == "only" and effect.orbs:
            only = list(effect.orbs)
        elif effect.mode == "increased":
            increased = True
    if only is not None:
        return only
    if increased:
        message = (
            f"Turn {state.turn}: increased skyfall has no modeled weight, "
            "so new orbs stay uniform."
        )
        if message not in state.unknowns:
            state.unknowns.append(message)
    return list(_DEFAULT_SKYFALL)


def _create_orbs(state: GameState, effect: Effect) -> None:
    count = effect.details.get("count")
    colors = effect.details.get("attributes")
    if not isinstance(count, int) or count < 1 or not isinstance(colors, list) or not colors:
        state.unknowns.append(f"Turn {state.turn}: create_orbs has no quantity or color.")
        return
    rng = _rng(state)
    assigned: dict[int, str] = {}
    for color in colors:
        if not isinstance(color, str):
            state.unknowns.append(f"Turn {state.turn}: create_orbs has an unknown color.")
            continue
        pool = [
            index
            for index, cell in enumerate(state.board.cells)
            if not cell.locked and index not in assigned and cell.color != color
        ]
        rng.shuffle(pool)
        chosen = pool[:count]
        for index in chosen:
            assigned[index] = color
        if len(chosen) < count:
            state.unknowns.append(
                f"Turn {state.turn}: create_orbs placed {len(chosen)} of {count} {color}."
            )
    for index, color in assigned.items():
        state.board.cells[index].color = color


def _replace_orbs(state: GameState, effect: Effect) -> None:
    count = effect.details.get("count")
    destinations = effect.details.get("to")
    excluded = effect.details.get("excluded")
    if (
        not isinstance(count, int)
        or count < 1
        or not isinstance(destinations, list)
        or not destinations
        or not isinstance(excluded, list)
    ):
        state.unknowns.append(f"Turn {state.turn}: orb_replace has no quantity or color.")
        return
    blocked = {str(color) for color in excluded}
    goals = [str(color) for color in destinations]
    pool = [
        index
        for index, cell in enumerate(state.board.cells)
        if not cell.locked
        and cell.color is not None
        and cell.color not in blocked
        and cell.color not in goals
    ]
    rng = _rng(state)
    rng.shuffle(pool)
    chosen = pool[:count]
    for index in chosen:
        state.board.cells[index].color = goals[0] if len(goals) == 1 else rng.choice(goals)
    if len(chosen) < count:
        state.unknowns.append(
            f"Turn {state.turn}: orb_replace changed {len(chosen)} of {count} orbs."
        )


def _convert_orbs(state: GameState, effect: Effect) -> None:
    sources = effect.details.get("from")
    destination = effect.details.get("to")
    if not isinstance(sources, list) or not sources or not isinstance(destination, list):
        state.unknowns.append(f"Turn {state.turn}: orb_convert has no source or destination.")
        return
    if len(destination) != 1 or not isinstance(destination[0], str):
        state.unknowns.append(f"Turn {state.turn}: orb_convert has no single destination.")
        return
    source_colors = {str(color) for color in sources}
    goal = destination[0]
    for cell in state.board.cells:
        if not cell.locked and cell.color in source_colors:
            cell.color = goal


def _change_orbs(state: GameState, effect: Effect) -> None:
    changes = effect.details.get("changes")
    if not isinstance(changes, list) or not changes:
        state.unknowns.append(f"Turn {state.turn}: orb_change has no pairs.")
        return
    mapping: dict[str, str] = {}
    for item in changes:
        if not isinstance(item, dict) or "from" not in item or "to" not in item:
            state.unknowns.append(f"Turn {state.turn}: orb_change has an unknown pair.")
            return
        mapping[str(item["from"])] = str(item["to"])
    colors = [cell.color for cell in state.board.cells]
    for cell, color in zip(state.board.cells, colors, strict=True):
        if cell.locked or color not in mapping:
            continue
        cell.color = mapping[color]


def _paint_board(state: GameState, effect: Effect) -> None:
    orbs = effect.details.get("orbs")
    cells = state.board.cells
    if not isinstance(orbs, list) or len(orbs) != len(cells):
        listed = len(orbs) if isinstance(orbs, list) else 0
        state.unknowns.append(
            f"Turn {state.turn}: board change lists {listed} orbs for a board of {len(cells)}."
        )
        return
    for cell, color in zip(cells, orbs, strict=True):
        cell.color = str(color)
        cell.locked = False


def _paint_lines(state: GameState, effect: Effect) -> None:
    if not state.board.is_standard():
        state.unknowns.append(
            f"Turn {state.turn}: {effect.kind} is unsupported on a "
            f"{state.board.columns}×{state.board.rows} board."
        )
        return
    lines = effect.details.get("lines")
    if not isinstance(lines, list) or not lines:
        state.unknowns.append(f"Turn {state.turn}: {effect.kind} has no lines.")
        return
    mapping = _COLUMN_INDEXES if effect.kind == "column_change" else _ROW_INDEXES
    axis = "column" if effect.kind == "column_change" else "row"
    rng = _rng(state)
    for line in lines:
        if not isinstance(line, dict):
            state.unknowns.append(f"Turn {state.turn}: {effect.kind} has an unknown line.")
            continue
        where = line.get("where")
        orbs = line.get("orbs")
        indexes = mapping.get(where) if isinstance(where, str) else None
        if indexes is None or not isinstance(orbs, list) or not orbs:
            state.unknowns.append(
                f"Turn {state.turn}: {effect.kind} {where!r} does not map to a 6×5 line."
            )
            continue
        goals = [str(color) for color in orbs]
        for index in _line_cells(state.board, indexes, axis):
            cell = state.board.cells[index]
            if cell.locked:
                continue
            cell.color = goals[0] if len(goals) == 1 else rng.choice(goals)


def _line_cells(board: BoardState, indexes: list[int], axis: str) -> list[int]:
    found: list[int] = []
    if axis == "column":
        for column in indexes:
            for row in range(board.rows):
                found.append(row * board.columns + column)
    else:
        for row in indexes:
            for column in range(board.columns):
                found.append(row * board.columns + column)
    return found


def _refresh_board(state: GameState) -> None:
    pool = _skyfall_pool(state)
    if not pool:
        state.unknowns.append(f"Turn {state.turn}: board refresh has no colors.")
        return
    rng = _rng(state)
    for cell in state.board.cells:
        cell.locked = False
        cell.color = rng.choice(pool)


def _create_combo(state: GameState, effect: Effect) -> None:
    count = effect.details.get("count")
    if not isinstance(count, int) or count < 1:
        state.unknowns.append(f"Turn {state.turn}: create_combo_orbs has no quantity.")
        return
    pool = [
        index
        for index, cell in enumerate(state.board.cells)
        if not cell.locked and cell.color != "combo"
    ]
    rng = _rng(state)
    rng.shuffle(pool)
    chosen = pool[:count]
    for index in chosen:
        state.board.cells[index].color = "combo"
    if len(chosen) < count:
        state.unknowns.append(
            f"Turn {state.turn}: create_combo_orbs placed {len(chosen)} of {count}."
        )


def _convert_combo(state: GameState, effect: Effect) -> None:
    source = effect.details.get("from")
    if not isinstance(source, str) or not source:
        state.unknowns.append(f"Turn {state.turn}: combo_convert has no source color.")
        return
    for cell in state.board.cells:
        if not cell.locked and cell.color == source:
            cell.color = "combo"


def _note_combo_orbs(state: GameState) -> None:
    if any(cell.color == "combo" and not cell.locked for cell in state.board.cells):
        state.unknowns.append(
            f"Turn {state.turn}: combo orbs are on the board and are not matched."
        )


def _select(state: GameState, target: PolicyTarget) -> list[tuple[_Group, str]]:
    clauses = [*state.leader_clauses, *state.helper_clauses]
    components = _components(state.board)
    selected: list[tuple[_Group, str]] = []
    chosen: set[tuple[int, ...]] = set()
    cross_made = False
    if _needs(clauses, "cross"):
        pluses = [group for group in components if _is_plus(group.cells, state.board.columns)]
        pluses.sort(key=lambda group: _plus_center(group.cells, state.board.columns))
        if pluses:
            selected.append((pluses[0], "cross"))
            chosen.add(pluses[0].cells)
            cross_made = True
    linked_colors = _linked_colors(clauses)
    if not cross_made and linked_colors:
        candidates = [
            group
            for group in components
            if group.color in linked_colors
            and len(group.cells) >= 3
            and group.cells not in chosen
        ]
        if candidates:
            best = min(candidates, key=lambda group: _rank(group, state.board.columns))
            selected.append((best, _shape(best.cells, state.board, as_cross=False)))
            chosen.add(best.cells)
    rest = [
        group
        for group in components
        if group.cells not in chosen
        and len(group.cells) >= 3
        and (group.color != "heal" or target.recovery)
    ]
    rest.sort(key=lambda group: _rank(group, state.board.columns))
    for group in rest:
        selected.append((group, _shape(group.cells, state.board, as_cross=False)))
    return selected


def _components(board: BoardState) -> list[_Group]:
    columns = board.columns
    seen: set[int] = set()
    groups: list[_Group] = []
    for start, cell in enumerate(board.cells):
        if start in seen or cell.locked or cell.color not in _MATCH_COLORS:
            continue
        color = cell.color
        stack = [start]
        seen.add(start)
        found: list[int] = []
        while stack:
            current = stack.pop()
            found.append(current)
            row, column = divmod(current, columns)
            for next_column, next_row in (
                (column - 1, row),
                (column + 1, row),
                (column, row - 1),
                (column, row + 1),
            ):
                if not (0 <= next_column < columns and 0 <= next_row < board.rows):
                    continue
                nxt = next_row * columns + next_column
                if nxt in seen:
                    continue
                other = board.cells[nxt]
                if other.locked or other.color != color:
                    continue
                seen.add(nxt)
                stack.append(nxt)
        groups.append(_Group(str(color), tuple(sorted(found))))
    return groups


def _clear(state: GameState, groups: list[tuple[_Group, str]]) -> None:
    for group, _shape_name in groups:
        for index in group.cells:
            cell = state.board.cells[index]
            cell.color = None
            cell.locked = False


def _gravity(board: BoardState) -> None:
    """Unlocked orbs fall toward the bottom of their column. Locked orbs stay."""
    for column in range(board.columns):
        segment: list[int] = []
        for row in range(board.rows):
            index = row * board.columns + column
            if board.cells[index].locked:
                _settle(board, segment)
                segment = []
            else:
                segment.append(index)
        _settle(board, segment)


def _settle(board: BoardState, segment: list[int]) -> None:
    if not segment:
        return
    colors = [
        board.cells[index].color
        for index in segment
        if board.cells[index].color is not None
    ]
    blanks = len(segment) - len(colors)
    for offset, index in enumerate(segment):
        cell = board.cells[index]
        if offset < blanks:
            cell.color = None
        else:
            cell.color = colors[offset - blanks]


def _heals(state: GameState, groups: list[tuple[_Group, str]]) -> tuple[int, int]:
    matched = {
        index
        for group, _shape_name in groups
        for index in group.cells
        if state.board.cells[index].color == "heal"
    }
    # Colors are cleared after the count is taken. Callers count before `_clear`.
    total = sum(1 for cell in state.board.cells if cell.color == "heal")
    return len(matched), total - len(matched)


def _judge(
    state: GameState,
    clauses: list[LeaderClause],
    matches: list[MatchedGroup],
    role: str,
    observed: bool,
) -> list[ClauseVerdict]:
    verdicts = evaluate_clauses(clauses, _observation(state, matches, role))
    if observed:
        return verdicts
    judged: list[ClauseVerdict] = []
    for clause, verdict in zip(clauses, verdicts, strict=True):
        if clause.condition.kind in _BOARD_CLAUSES:
            judged.append(ClauseVerdict(clause_id=clause.clause_id, status="unknown"))
        else:
            judged.append(verdict)
    return judged


def _observation(state: GameState, matches: list[MatchedGroup], role: str) -> MatchObservation:
    linked: dict[str, int] = {}
    rows: dict[str, int] = {}
    cross = False
    l_shape = False
    awakenings: list[str] = []
    colors: set[str] = set()
    for match in matches:
        if match.shape == "cross":
            cross = True
        else:
            linked[match.color] = max(linked.get(match.color, 0), match.size)
        if match.shape == "l_shape":
            l_shape = True
        covered = _full_rows(match.cells, state.board.columns, state.board.rows)
        if covered:
            rows[match.color] = rows.get(match.color, 0) + covered
        if match.color in _ATTRIBUTE_COLORS:
            colors.add(match.color)
    if cross:
        awakenings.append("cross_attack")
    if l_shape:
        awakenings.append("l_attack")
    for color, count in rows.items():
        trigger = _ROW_TRIGGER.get(color)
        if count and trigger is not None:
            awakenings.append(trigger)
    member = _role_member(state, role)
    maximum = team_hp_max(state)
    hp_percent = None
    if state.hp is not None and maximum:
        hp_percent = state.hp * 100 // maximum
    return MatchObservation(
        linked=linked,
        cross=cross,
        l_shape=l_shape,
        rows=rows,
        combos=len(matches),
        hp_percent=hp_percent,
        attributes=list(member.attributes) if member is not None else [],
        card_types=list(member.card_types) if member is not None else [],
        color_count=len(colors),
        awakenings=awakenings,
    )


def _role_member(state: GameState, role: str) -> MemberState | None:
    found = [member for member in state.members if member.role == role]
    if not found:
        return None
    return found[-1]


def _needs(clauses: list[LeaderClause], kind: str) -> bool:
    return any(
        clause.evidence == "verified" and clause.condition.kind == kind for clause in clauses
    )


def _linked_colors(clauses: list[LeaderClause]) -> set[str]:
    colors: set[str] = set()
    for clause in clauses:
        condition = clause.condition
        if (
            clause.evidence == "verified"
            and condition.kind == "linked"
            and condition.combination == "single"
            and len(condition.attributes) == 1
        ):
            colors.add(condition.attributes[0])
    return colors


def _matched(group: _Group, shape: _Shape) -> MatchedGroup:
    return MatchedGroup(
        color=group.color,
        size=len(group.cells),
        shape=shape,
        cells=list(group.cells),
    )


def _shape(cells: tuple[int, ...], board: BoardState, *, as_cross: bool) -> _Shape:
    if as_cross:
        return "cross"
    if _is_l(cells, board.columns):
        return "l_shape"
    if _is_one_row(cells, board.columns):
        return "row"
    return "group"


def _is_plus(cells: tuple[int, ...], columns: int) -> bool:
    if len(cells) != 5:
        return False
    points = {_col_row(index, columns) for index in cells}
    for column, row in points:
        plus = {
            (column, row),
            (column - 1, row),
            (column + 1, row),
            (column, row - 1),
            (column, row + 1),
        }
        if plus == points:
            return True
    return False


def _is_l(cells: tuple[int, ...], columns: int) -> bool:
    if len(cells) != 5:
        return False
    points = {_col_row(index, columns) for index in cells}
    min_col = min(column for column, _row in points)
    min_row = min(row for _column, row in points)
    norm = frozenset((column - min_col, row - min_row) for column, row in points)
    return norm in _L_SHAPES


def _is_one_row(cells: tuple[int, ...], columns: int) -> bool:
    if len(cells) != columns:
        return False
    rows = {index // columns for index in cells}
    return len(rows) == 1


def _full_rows(cells: list[int], columns: int, rows: int) -> int:
    del rows
    by_row: dict[int, set[int]] = {}
    for index in cells:
        column, row = _col_row(index, columns)
        by_row.setdefault(row, set()).add(column)
    return sum(1 for occupied in by_row.values() if len(occupied) == columns)


def _plus_center(cells: tuple[int, ...], columns: int) -> tuple[int, int]:
    points = {_col_row(index, columns) for index in cells}
    for column, row in points:
        plus = {
            (column, row),
            (column - 1, row),
            (column + 1, row),
            (column, row - 1),
            (column, row + 1),
        }
        if plus == points:
            return row, column
    return _anchor(cells, columns)


def _rank(group: _Group, columns: int) -> tuple[int, int, int]:
    top, left = _anchor(group.cells, columns)
    return (-len(group.cells), top, left)


def _anchor(cells: tuple[int, ...], columns: int) -> tuple[int, int]:
    positions = [_col_row(index, columns) for index in cells]
    top = min(row for _column, row in positions)
    left = min(column for column, row in positions if row == top)
    return top, left


def _col_row(index: int, columns: int) -> tuple[int, int]:
    return index % columns, index // columns
