"""Time team search on the committed regression cards.

The collection is ``tests/fixtures/cards``. The script does not use the network.
Candidate generation and team construction are repeated and the median is kept.
Evaluation and simulation throughput use one Rozé lineup for 10 turns and
1,000 boards. Search runtime is one owned-box search at beam width 200,
10 turns, and 10 simulations. Peak memory is the Python heap during that
search and the process high-water mark after every stage.

Run: uv run python scripts/benchmark_team.py
"""

import gc
import json
import platform
import resource
import sys
import tempfile
import tracemalloc
import urllib.request
from collections import Counter
from contextlib import closing
from pathlib import Path
from time import perf_counter

from padbox.evaluator import Scenario, evaluate_team
from padbox.evaluator.sim import MemberInput, run_monte_carlo
from padbox.optimizer import generate_candidates, search_teams
from padbox.optimizer import search as search_module

ROOT = Path(__file__).resolve().parents[1]

LEADER = 13967
HELPER = 7712
SUBS = (8336, 7844, 10756, 7949)
TURNS = 10
THROUGHPUT_SIMULATIONS = 1000
SEARCH_SIMULATIONS = 10
SEARCH_BEAM = 200
GENERATION_REPEATS = 20
CONSTRUCTION_REPEATS = 3
_MARGIN = 4


def main() -> None:
    urllib.request.urlopen = _refuse_network
    cards = _fixtures().load_regression_cards()
    with tempfile.TemporaryDirectory(prefix="padbox-bench-") as directory:
        store = _fixtures().build_regression_store(Path(directory))
        _box(store, cards)
        lineup = _team()
        members, skills, card_index = _battle(cards)
        report = {
            "collection": "tests/fixtures/cards",
            "cards": len(cards),
            "leader_id": LEADER,
            "machine": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "processor": platform.processor() or platform.machine(),
            },
            "candidate_generation": _generation(store),
            "team_construction": _construction(store),
            "evaluation": _evaluation(store, lineup),
            "simulation": _simulation(members, skills, card_index),
        }
        report["search"] = _search(store)
        report["targets"] = _targets(report)
    print(json.dumps(report, indent=2))


def _generation(store: object) -> dict:
    samples = [
        _seconds(lambda: generate_candidates(store, LEADER)) for _ in range(GENERATION_REPEATS)
    ]
    pool = generate_candidates(store, LEADER)
    return {
        "repeats": GENERATION_REPEATS,
        "median_seconds": _median(samples),
        "min_seconds": min(samples),
        "max_seconds": max(samples),
        "candidates": len(pool.candidates),
        "pruned": len(pool.pruned),
        "kinds": dict(Counter(row.kind for row in pool.candidates)),
    }


def _construction(store: object) -> dict:
    """Build the assisted beam. ``evaluate_team`` returns one finished report."""
    sentinel = evaluate_team(
        _team(),
        Scenario(seed=0, turns=1, simulations=1),
        store=store,
        explain=False,
    )
    calls = {"count": 0}
    original = search_module.evaluate_team

    def stub(*_args: object, **_kwargs: object) -> object:
        calls["count"] += 1
        return sentinel

    samples = []
    result = None
    search_module.evaluate_team = stub
    try:
        for _ in range(CONSTRUCTION_REPEATS):
            calls["count"] = 0
            started = perf_counter()
            result = search_teams(
                store,
                LEADER,
                beam_width=SEARCH_BEAM,
                seed=0,
                turns=1,
                simulations=1,
            )
            samples.append(perf_counter() - started)
    finally:
        search_module.evaluate_team = original
    if result is None:
        raise RuntimeError("team construction did not run")
    return {
        "repeats": CONSTRUCTION_REPEATS,
        "median_seconds": _median(samples),
        "min_seconds": min(samples),
        "max_seconds": max(samples),
        "beam_width": SEARCH_BEAM,
        "complete_teams_scored": result.complete_teams_scored,
        "beam": len(result.beam),
        "evaluate_calls_last_repeat": calls["count"],
        "note": (
            "evaluate_team was replaced with one already-finished report. "
            "The time is candidate use, the cheap screen, the diverse beam, and assists."
        ),
    }


def _evaluation(store: object, lineup: dict) -> dict:
    scenario = Scenario(seed=0, turns=TURNS, simulations=THROUGHPUT_SIMULATIONS)
    evaluate_team(lineup, scenario, store=store, explain=False)
    elapsed = _seconds(lambda: evaluate_team(lineup, scenario, store=store, explain=False))
    return {
        "team": lineup,
        "turns": TURNS,
        "simulations": THROUGHPUT_SIMULATIONS,
        "explain": False,
        "seconds": elapsed,
        "evaluations_per_second": 1 / elapsed,
        "simulations_per_second": THROUGHPUT_SIMULATIONS / elapsed,
    }


def _simulation(
    members: list[MemberInput], skills: dict[int, dict], cards: dict[int, dict]
) -> dict:
    run_monte_carlo(
        members, skills, cards, turns=TURNS, simulations=50, seed=0
    )
    elapsed = _seconds(
        lambda: run_monte_carlo(
            members,
            skills,
            cards,
            turns=TURNS,
            simulations=THROUGHPUT_SIMULATIONS,
            seed=0,
        )
    )
    return {
        "team": _team(),
        "turns": TURNS,
        "simulations": THROUGHPUT_SIMULATIONS,
        "seconds": elapsed,
        "simulations_per_second": THROUGHPUT_SIMULATIONS / elapsed,
        "turn_steps_per_second": THROUGHPUT_SIMULATIONS * TURNS / elapsed,
    }


def _search(store: object) -> dict:
    gc.collect()
    tracemalloc.start()
    started = perf_counter()
    result = search_teams(
        store,
        LEADER,
        beam_width=SEARCH_BEAM,
        seed=0,
        turns=TURNS,
        simulations=SEARCH_SIMULATIONS,
    )
    elapsed = perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "beam_width": SEARCH_BEAM,
        "turns": TURNS,
        "simulations": SEARCH_SIMULATIONS,
        "seed": 0,
        "objective": "general",
        "helper": "owned copies only",
        "seconds": elapsed,
        "complete_teams_scored": result.complete_teams_scored,
        "beam": len(result.ranked),
        "python_heap_peak_bytes": peak,
        "process_rss_high_water_bytes": _rss_bytes(),
    }


def _targets(report: dict) -> dict:
    generation = report["candidate_generation"]["median_seconds"]
    construction = report["team_construction"]["median_seconds"]
    evaluation = report["evaluation"]["seconds"]
    simulation = report["simulation"]["seconds"]
    search = report["search"]["seconds"]
    rss = report["search"]["process_rss_high_water_bytes"]
    return {
        "margin": (
            f"{_MARGIN} times the measured duration, and {_MARGIN} times the process "
            "high-water mark. These bounds are for this fixture workload only."
        ),
        "candidate_generation_seconds": _time_bound(generation),
        "team_construction_seconds": _time_bound(construction),
        "evaluation_seconds": _time_bound(evaluation),
        "evaluation_simulations_per_second": (
            report["evaluation"]["simulations_per_second"] / _MARGIN
        ),
        "simulation_seconds": _time_bound(simulation),
        "simulation_simulations_per_second": (
            report["simulation"]["simulations_per_second"] / _MARGIN
        ),
        "search_seconds": _time_bound(search),
        "process_rss_high_water_bytes": _memory_bound(rss),
    }


def _team() -> dict:
    return {
        "leader_id": LEADER,
        "helper_id": HELPER,
        "subs": [{"owned_id": monster_id, "form_id": monster_id} for monster_id in SUBS],
    }


def _battle(records: list[dict]) -> tuple[list[MemberInput], dict[int, dict], dict[int, dict]]:
    by_id = {int(record["monster_id"]): record for record in records}
    skills = _fixtures().regression_skill_index(records)
    cards = {monster_id: record["card"] for monster_id, record in by_id.items()}
    members = [MemberInput(role="leader", card=cards[LEADER])]
    members.extend(MemberInput(role="sub", card=cards[monster_id]) for monster_id in SUBS)
    members.append(MemberInput(role="helper", card=cards[HELPER]))
    return members, skills, cards


def _box(store: object, records: list[dict]) -> None:
    document = json.dumps(
        {
            "schema_version": 1,
            "server": "NA",
            "monsters": [
                {"monster_id": int(record["monster_id"]), "quantity": 1, "confirmed": True}
                for record in records
            ],
            "unresolved": [],
            "complete": True,
        }
    )
    with closing(store.connect()) as db, db:
        db.execute("INSERT INTO box_state VALUES (1, 1, ?, NULL)", (document,))


def _seconds(action) -> float:
    started = perf_counter()
    action()
    return perf_counter() - started


def _median(samples: list[float]) -> float:
    ordered = sorted(samples)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _time_bound(seconds: float) -> float:
    scaled = seconds * _MARGIN
    if scaled < 0.1:
        return round(scaled + 0.0005, 3)
    if scaled < 10:
        return round(scaled + 0.05, 1)
    return float(round(scaled + 0.5))


def _memory_bound(size: int) -> int:
    step = 10 * 1024 * 1024
    scaled = size * _MARGIN
    return ((scaled + step - 1) // step) * step


def _rss_bytes() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return int(rss)
    return int(rss) * 1024


def _fixtures():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tests import conftest

    return conftest


def _refuse_network(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("benchmark_team must not use the network")


if __name__ == "__main__":
    main()
