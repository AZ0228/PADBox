"""Deterministic turn simulator.

The public entry points build a battle, advance turns, and read HP, RCV,
shields, and whether the board size is supported. Each turn also matches
the 6×5 board with the greedy policy and records clause verdicts.
`run_scenario` replays a fixed board sequence. `run_monte_carlo` repeats
seeded skyfall boards and summarizes the metrics the battle actually measured.
"""

from padbox.evaluator.sim.policy import PolicyTarget
from padbox.evaluator.sim.schedule import (
    MemberInput,
    apply_bind,
    apply_skill_delay,
    open_battle,
    run,
    step,
)
from padbox.evaluator.sim.simulate import (
    DEFAULT_SEED,
    DEFAULT_SIMULATIONS,
    DEFAULT_TURNS,
    Metric,
    SimulationReport,
    report_battles,
    run_monte_carlo,
    run_scenario,
)
from padbox.evaluator.sim.state import (
    ASSUMPTIONS,
    BOARD_COLUMNS,
    BOARD_ROWS,
    GameState,
    MatchedGroup,
    TurnReport,
    board_is_supported,
    effective_rcv,
    empty_board,
    enemy_is_delayed,
    shield_percent,
    team_hp_max,
)

__all__ = [
    "ASSUMPTIONS",
    "BOARD_COLUMNS",
    "BOARD_ROWS",
    "DEFAULT_SEED",
    "DEFAULT_SIMULATIONS",
    "DEFAULT_TURNS",
    "GameState",
    "MatchedGroup",
    "MemberInput",
    "Metric",
    "PolicyTarget",
    "SimulationReport",
    "TurnReport",
    "apply_bind",
    "apply_skill_delay",
    "board_is_supported",
    "effective_rcv",
    "empty_board",
    "enemy_is_delayed",
    "open_battle",
    "report_battles",
    "run",
    "run_monte_carlo",
    "run_scenario",
    "shield_percent",
    "step",
    "team_hp_max",
]
