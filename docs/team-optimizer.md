# Team optimizer

Layer C is `padbox.optimizer`. It is the only layer that searches. `generate_candidates` builds the owned pool. `search_teams` turns that pool into complete teams and asks `evaluate_team` to score the teams it kept. `padbox.mechanics` does not select a team. `padbox.evaluator` does not search.

A result is the diverse beam for the objective, seed, turn count, and simulation count named on that run. It is not a global optimum. Assists are chosen only for that beam, after the lineup is fixed.

## Algorithm

The search is a staged beam. It does not enumerate every lineup once the pool is larger than the beam, and it does not add an optimization dependency.

1. Generate the broad pool with `generate_candidates`. That pool already dropped unreachable forms, ownership conflicts, hard incompatibilities, missing essential roles, and clearly dominated forms of the same card. An unverified effect is an uncertainty score on the card. It is not a reason the pool dropped the card.
2. Keep one leader: the owned copy reserved for the requested leader. If no owned copy can become that leader, the search stops.
3. Choose the helper with the policy below. The helper's copy, when it is owned, is reserved before subs are chosen.
4. Fill exactly four sub slots. A candidate may enter a slot when it is a sub, its `(owned_id, copy_index)` is still free, and the box still has its copy plus every monster id on its evolution spend. One copy fills only one role: leader, helper, or a single sub. Forms of one copy stay alternatives.
5. Score each partial and each complete lineup with the cheap screen below. The screen does not roll the board and does not call `evaluate_team`.
6. At the helper step and after each sub slot, keep a diverse beam of width `beam_width` (default 200). Identical lineups reached in different insertion orders collapse to one. The groups are the essential roles the lineup covers. When more lineups exist than the width, the beam takes the highest-screened lineup from each group in turn, then the next, until the width is full. A group that already leads the screen does not fill the beam by itself while another covered-role set still has a lineup. When every lineup fits, the beam is that list sorted by the screen.
7. Choose assists for each team still in the beam. The lineup stays fixed. The choice uses the timing plan below, not a second board simulation.
8. Call `evaluate_team` on those teams with the assists attached. That report is the one ranking reads. Partial teams, discarded complete teams, and pruned candidates are not simulated. Explanations are left off. The call uses the requested objective, seed, turn count, and simulation count. Defaults are `general`, seed 0, 10 turns, and 1,000 Monte Carlo boards. The report records those values.
9. Re-order that beam by the evaluator, not by the cheap screen and not by the assist score. Feasibility `pass` comes before `unknown`, and `unknown` comes before `fail`. Inside one feasibility, the objective's component order is the order in `docs/scoring-model.md`. A resolved component comes before an unresolved one. A higher resolved value comes first. Total card uncertainty is only a tie-break after those four components. It does not outrank a better component, and it does not drop a team.

`complete_teams_scored` is how many distinct complete lineups the last slot scored before the beam cut. `beam` is that cut, in diverse-screen order, after simulation. `ranked` is the same teams in evaluator order. Each ranked team has a `report`.

## Ranked report

The report is filled from the `evaluate_team` result for that team and from the other teams in the same beam. It does not simulate a lineup the beam dropped.

Each report has the lineup, the helper, the assists, the decoder requirement results, the transform check, the skill order the simulator uses, the activation metrics it recorded, the survivability and damage estimates, the failed and unresolved checks, the unknown mechanics, why this team is in this place, and a trade-off against every other tested team.

Survivability stays unresolved when no enemy damage is modeled. Damage stays unresolved when no damage target is set. Context metrics are named and are not substituted for those estimates. The simulation records rates, not a cast-by-cast rotation.

The trade-off is the first difference in the evaluator order for the named objective: feasibility, then the objective's components, then lower uncertainty, then the cheap screen. A resolved component sorts ahead of an unresolved one. A higher resolved value sorts first. When the objective terms tie, the report says so instead of inventing a component win. The scope repeats the objective, seed, turn count, simulation count, beam width, how many complete lineups were scored, and that this is not a global optimum.

## Cheap screen

Each component is a number where higher is better. The screen sorts by those four numbers in the objective's order. This is a filter. It is not the scalar in `docs/scoring-model.md`, and a win on the screen is not a win on `evaluate_team`.

Members are the leader, the helper, and the subs chosen so far. Subs are walked in `(owned_id, copy_index, form_id)` order so the score does not depend on insertion order.

Needed color keys are the pool's essential roles whose names end in `_orbs` or `_board`.

```text
color_hits = how many needed color keys the members' verified answers cover
cooldown_term = 0 when no member that covers a needed color has an integer cooldown
cooldown_term = 1 / (1 + minimum such cooldown) otherwise
boost_term = min(total skill boost, transform_turns) / transform_turns
             when the pool records a positive transform cooldown
boost_term = 1 when that cooldown is absent and skill boost is not essential
boost_term = min(total skill boost, 4) / 4 when skill boost is essential and the cooldown is unknown

activation_consistency = color_hits + cooldown_term + boost_term
survivability = how many of the essential defensive roles the answers cover
damage = subs that can damage on the leader color, plus 1 when the helper can
flexibility = 1 - max(0, unused_utility - 1) / slot_count    when slot_count > 0
flexibility = 1                                              when slot_count = 0
```

Defensive roles, when they are essential for this leader, are bind clear, awoken bind clear, unmatchable clear, attribute absorb, damage absorb, combo absorb, void pierce, and haste.

`slot_count` is the number of subs chosen so far. A sub is unused utility when every verified answer it has is already covered by the leader, the helper, or an earlier sub. The value is clamped to `[0, 1]`. One extra copy of a covered answer is not penalized. Each further extra copy is.

`transform_turns` is the pool's known transform cooldown: the leader's, or the larger of the leader's and the explicit helper's when that helper was part of the pool. Total skill boost is the sum across the leader, the helper, and the subs chosen so far.

`general` reads the four numbers in the order activation consistency, survivability, damage, flexibility. The other objectives use the same permutation as `docs/scoring-model.md`. After those four, a lower total uncertainty sorts first, then a stable lineup identity. Uncertainty never moves a lineup ahead of one that is better on an earlier component.

## Helper policy

A helper is either explicit or owned.

- An explicit helper is the `helper_id` passed to the search. The pool marks that card `helper_origin: explicit`. If the box can field it, the copy is one of the user's. `supply` is `explicit` and `borrowed` is false. If the box cannot field it, the helper is the supplied card, `supply` is `explicit`, and `borrowed` is true. The search does not also try other owned leaders once an explicit helper was named.
- With no `helper_id`, the helper is an owned copy that has a leader skill and survived the pool's hard-incompatibility check. `supply` is `owned` and `borrowed` is false.

A card that is not owned and was not passed as `helper_id` is not a helper. The search does not invent a friend's helper. The explicit helper's copy is still rejected when it is the reserved leader copy or its spends are already gone.

## Assists

Assists are chosen after the beam exists. Each shortlisted team is one lineup plus its equips. An assist candidate is an owned form with Awoken Assist. Its `(owned_id, copy_index)` and its evolution spends have to be free after the leader, the helper, and the four subs. A card that is not owned is not equipped. Super awakenings are not selected.

The timing plan uses the simulator's charge rule and does not roll a board. Skill boost on the fielded forms and the equips applies once, before turn 1. A counter of C after that boost is ready on turn C, and on turn 1 when C is 0. An assist replaces the host active: charge spends the host's minimum cooldown first, then the assist's minimum. An unknown cooldown is not a timed answer. The plan does not invent a haste amount, so haste does not pull another skill forward.

A host keeps its base active when the card transforms, or when this plan casts that active inside the scenario's turn count. Another copy of the same answer does not make that overwrite legal. The plan casts a ready active when its verified answers still include an essential role nobody earlier in the order has covered. Order is leader, subs in lineup order, then helper. A transforming card's battle-form answers are not treated as that opening active.

The search adds one legal assist at a time and scores the whole set after each addition. It stops when no addition raises the score. The score, highest term first, is:

```text
held roles           essential answers still present on an unequipped base active,
                     or on an inherited active whose cooldown is known
in-time roles        those answers whose ready turn is inside the turn count
transform progress   min(team skill boost, transform_turns) / transform_turns
                     when the pool records a positive transform cooldown, else 1
backup pressure      for each held role that is not in time, 1 / earliest ready turn
hazard resists       bind, skill bind, blind, jammer, and poison at 100 percent,
                     plus cloud, tape, and skill delay resistance when present
attribute changes    equips that set a host's sub attribute to a leader color
                     the host did not already have
```

An equip that moves a known leader-color sub attribute off every leader color is rejected. An unknown sub attribute is not counted as a gain or a loss. Two different sub-attribute awakenings on one equip are not counted as a gain. Hazard percents use the checklist magnitudes on the host's normal awakenings plus the equip's. Tape, cloud, and skill delay are presence, not percents.

`evaluate_team` then runs on the assisted team. Ranking uses that report.

## Parameters

| Parameter | Default | Role |
| --- | --- | --- |
| `objective` | `general` | Cheap-screen order and the objective passed to `evaluate_team` |
| `beam_width` | 200 | Diverse lineups kept after each slot, and the maximum number of full simulations |
| `seed` | 0 | Monte Carlo seed recorded on every finalist report |
| `turns` | 10 | Simulated turn count recorded on every finalist report |
| `simulations` | 1000 | Monte Carlo board count recorded on every finalist report |
| sub slots | 4 | Fixed. A missing helper does not open a fifth sub |

`beam_width` must be positive. `turns` and `simulations` must be positive. The scenario on each finalist report stores the objective, seed, turns, and simulations actually used.

`padbox team optimize --leader ID [--helper ID] [--objective NAME]` runs this search. `--beam-width`, `--seed`, `--turns`, and `--simulations` override the table. `--dungeon FILE` is accepted and refused: the file is not read, and the search cannot claim a dungeon clear. `padbox team evaluate --team FILE` scores one supplied team and does not search. `padbox team compare A B` compares two named teams and selects neither. `padbox evaluate` stays the recognition-accuracy command.

## What this search cannot guarantee

- It cannot claim a global optimum. A lineup the beam dropped at an earlier slot is not simulated, including a lineup the cheap screen underrated.
- The cheap screen is not `evaluate_team`. Survivability and damage here are counts of known answers and damage-colored members. The evaluator leaves survivability unresolved when no enemy damage is modeled, and it leaves damage unresolved when no damage target is set. Skill Boost on the screen is measured against the leader's known transform cooldown, and against the explicit helper's when that helper was part of the pool. A transforming sub's own cooldown is not in that number. `evaluate_team` still applies it.
- Activation rates on a finalist report are the greedy policy's approximation on the simulated boards. They are not a claim about human play.
- An unverified effect stays uncertainty. The search does not treat it as a miss, and it does not treat it as a covered mechanic.
- The assist step is a hill-climb over the beam, not every equip permutation. An equip the score does not raise is left off, including a pair that would help only together when neither equip raises the score alone. Skill boost and backup pressure are on the score so a second charge that only matters after the first still gets a chance.
- The timing plan is not the Monte Carlo cast list. It does not apply a haste amount. A transforming card is scored with the fielded form's skill boost; `evaluate_team` counts the pre-transform entry for that card. A transforming sub's own cooldown is not inside `transform_turns` unless it was already the pool's recorded cooldown.
- A dungeon clear is outside this search. No enemy mechanic is marked handled from this beam alone.
- Two copies of one monster may both be subs. One copy may not fill two roles. Evolution materials are spent from the same box counts, one lineup at a time.
