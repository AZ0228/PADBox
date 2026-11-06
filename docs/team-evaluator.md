# Team evaluator

Layer B is `padbox.evaluator`. The turn simulator lives in `padbox.evaluator.sim`. This layer scores one supplied team under a stated scenario. It does not search for another team.

`padbox.mechanics` still owns legality: ownership, evolution, team shape, transforms, and decoder coverage. `check_team` reports `strategic_quality: not_evaluated`. `evaluate_team` reuses that check, runs the scenario's simulation, and scores the team with `docs/scoring-model.md`. A passing `ok` is a complete-team checklist, not a battle result.

The board is 6×5: 6 columns by 5 rows. Any other size is unsupported and is reported as such.

## Hard-constraint report

`evaluate_team(team, scenario, ruleset)` returns an `EvaluationReport` for one supplied team. The team names `leader_id` and the subs, helper, and assists `check_team` already accepts. The applied box and the reference snapshot come from the store passed with the call. This function does not search. After the hard checks, a 6×5 board is simulated for the scenario's turn count, simulation count, and seed. The quality components use the formulas in `docs/scoring-model.md`, read from that simulation and from the utility-slot count. `strategic_quality` is `excluded`, `unresolved`, or `ranked`. The scalar exists only when the team is ranked.

The report stores the ruleset, the scenario, the seed, and the data provenance of that run. The scenario records its name, seed, turn count, and simulation count. The defaults are `general`, seed 0, 10 turns, and 1,000 simulations. The ruleset records the greedy matching policy and `policy_approximation`. A board other than 6×5 fails the board check, and the requested size stays on the ruleset.

Provenance is the reference source and version on the leader's monster row, the applied box revision, whether the skill index was supplied, cached, or missing, and the decoder version.

Hard checks reuse `check_team` and stay `pass`, `fail`, or `unknown`:

- Team structure, and whether the team is complete: one leader, four subs, and one helper. A missing helper does not add a fifth sub. A partial team is still checked, with `team_complete` false.
- Ownership and copy limits.
- Evolution feasibility. A special requirement the reference cannot interpret stays `unknown`.
- Leader legality passes when an owned copy can become the leader.
- Helper legality passes when a helper was supplied explicitly. A borrowed helper is legal. A missing helper fails, because a friend's helper is not assumed.
- Transform requirements, including each transforming member. An unresolved cooldown stays `unknown`. A known miss on another member stays `fail`.
- Mechanics coverage and each decoder requirement, each with its own result.
- Each requirement the scenario names. A name the decoder already judged keeps that result. A name this layer does not model stays `unknown`.
- Each mechanic on a dungeon profile attached to the scenario. The check uses the simulator's response and its timing. See Dungeon profiles below.

`feasibility` is `pass` only when every hard check passes. Any `fail` makes it `fail`, including an incomplete team. `unknown` with no `fail` makes it `unknown`. An unresolved check is not a pass and is not a zero.

## Dungeon profiles

A `DungeonProfile` can be the scenario, or the `dungeon` field on a scenario. Each listed mechanic on each floor becomes an essential requirement named `floor:<n>:<mechanic>`.

The battle times these responses: damage absorb void, attribute absorb void, damage void pierce, awoken bind clear, and unmatchable clear. A requirement is met when every simulation shows that response with known timing at least once. A known miss is `fail`. Skill delay, resolve, enemy shields, and HP thresholds have no response model, so they stay `unknown`.

`report.dungeon` says a floor is handled only when every listed mechanic on it is modeled and met. Enemy HP is not a damage target, and preemptive damage above zero is not applied. Those stay uncertainties. The report does not say the team clears a floor. The profile also does not say which turn the enemy uses a mechanic, so the timing is the team's response across the simulated battle.

Simulation defaults, recorded on every report and overridable by flag:

- 10 turns
- 1,000 Monte Carlo boards
- seed 0

## Matching policy

The simulator uses one deterministic greedy policy. There is no orb-path solver. Every activation result is a policy approximation. Each turn report sets `basis` to `policy_approximation`. That is a result for this policy on the simulated board, not a claim about human play.

A supported turn runs in this order:

1. Effects whose `expires_on` has arrived drop off.
2. Empty unlocked cells skyfall from the battle seed. Turn 1 fills the opening board here. Scenario mode installs that turn's fixed board instead of this skyfall.
3. The skill policy runs. A verified board change is applied with that skill's quantity and source colors. Locked orbs are left in place. A full board pattern whose list covers every cell, and a board refresh, replace every cell and clear locks. The pattern is row-major from the top left. Column and row changes use the decoder's line phrases on a 6×5 board. A list that does not cover the board is not painted.
4. The policy matches. Orbs then fall toward the bottom of their column. A locked orb stays and blocks the cells above it.
5. Empty unlocked cells skyfall again. Orbs that appear this turn are not matched until a later turn. The policy does not cascade and does not rerun a match to hunt for a later combo.

An unsupported board, including an active board-size effect other than 6×5, skips the match. Board clauses on that turn are unknown. While unmatchable turns remain, the policy matches nothing and those clauses are unmet.

The match itself:

1. Locked orbs are not part of a group. Groups are orthogonal and one color.
2. If a leader or helper clause still needs a cross, and a group is exactly five orbs in a plus (a center with four orthogonal neighbors), the policy matches that plus as a cross. The topmost center wins, then the leftmost. A cross clause does not name a color, so the plus may be any color. Those five orbs do not count as a linked match.
3. Otherwise the policy matches the largest group of at least three orbs of a color that a still-unmet single-color linked clause needs. Ties use the topmost cell, then the leftmost.
4. Heart orbs are matched when the scenario sets recovery, or when they are the cross or the linked group just chosen. Otherwise they stay, so a cross or linked clause can be met while hearts remain.
5. Every other group of three or more orbs is matched, largest first, with the same tie break.

Five orbs in a line are a linked match, not a cross. A full board of one color is one linked match and does not satisfy a cross. An L of five is reported as an L and still counts toward its linked count. A matched group that covers a full row counts as a row of that color.

Leader and helper clauses are judged from that match, in clause order. The turn report keeps both verdicts. `leader_verdicts` and `helper_verdicts` on the battle are the latest turn, and they stay null until the first turn.

Skyfall draws uniformly from fire, water, wood, light, dark, and heal. An active skyfall whose mode is `only` and whose duration is one number draws from that skill's colors. An increased rate has no modeled weight, so the draw stays uniform and the turn records that. A duration given as a range stays unknown and does not change the draw. Combo orbs leave their source color and are not matched. Orb enhance does not change colors.

## Skill-use policy

One monster has one usable active on a turn. That active is its entry skill, its transformed skill, or its inherited assist skill. `padbox.evaluator.sim` runs this policy on the machines in `padbox.mechanics.skill_state`. It does not search cast orders.

Counters start at the max-skill minimum. The team's skill boost is the sum of Skill Boost, Skill Boost+, and Anti-Skill Boost on the forms that entered and on their assists. Super awakenings, latents, and the team badge are outside that sum. The boost is applied once, before turn 1. When an assist is equipped, charge fills the host skill's opening cooldown first and the assist counter after that. The host active is not cast while the assist occupies the slot. Skill Charge and Skill Charge+ have no checklist magnitude, so they add no charge. A missing awakening list makes the team boost unknown: counters stay at their opening minimums and no active is used.

Each turn, including turn 1, ticks one charge from every known counter before any active is used. Haste from an earlier turn is already on the counter, so the tick includes it. A counter that is still above zero after the tick is not used. Durations that reach `expires_on` leave before actives.

Actives then resolve in one fixed order: leader, subs in listed order, helper. The order is two passes.

1. A transform is used on the first turn that skill is ready. Ready means the counter and any assist overcharge are zero, the cooldown is known, the monster is not bound, and the evolution recorded for that member is met. Haste on that cast charges the other members and leaves the cooldown of the form just entered unchanged.
2. A ready active that is not a transform is used when it changes orbs toward a color the scenario still needs, heals when the scenario still needs recovery, or carries a verified mechanic the scenario still lacks. An unverified effect does not answer that need. The pass walks each slot once. Haste from a cast can ready a later slot in the same pass. It does not give an earlier slot another action.

A new state whose minimum is zero stays used until the next turn's tick. Enemy move delay from an active covers that many enemy turns, including the turn it is cast. Skill delay, the charge an enemy adds to these counters, is separate. A member with Skill Delay Resistance and no checklist magnitude keeps the counter unchanged, and that member's cooldown stays unknown.

A counter of C after the opening boost is ready on turn C, and on turn 1 when C is 0. The coverage checklist still compares skill boosts with the full cooldown before this tick. HP and RCV start as the sum of max-level card stats. A transform moves current HP by the difference in that member's max HP. The team shield is the highest active damage shield. RCV multipliers multiply, in application order, with integer division. A duration given as a range is stored as unknown and is not applied as a number. The battle board is 6×5. Any other size is unsupported.

Leader and helper clauses are stored on the battle. Each turn writes a report with both verdicts, and the battle keeps the latest pair. The assumptions list and the seed are stored with the battle.

## Simulation

`run_scenario` plays one supplied board per turn. The board is 30 color names, row-major from the top left, installed after effects expire and before actives. Locks on that board are cleared. The post-match skyfall still runs, and the next turn's board replaces it. One scenario is one simulation, so its rates have no interval.

`run_monte_carlo` repeats the battle on skyfall boards. The defaults are 10 turns, 1,000 simulations, and seed 0. Simulation `i` is opened with `seed + i`. A caller can pass other positive counts and another seed. The report records the base seed and every seed it used.

Both modes return the same report. It names the mode, the simulation count, the turn count, the seeds, and the assumptions. The assumptions are the battle assumptions plus how this mode built the boards, how a 95% interval is computed, and that recovery sufficiency is omitted. `basis` is `policy_approximation`.

A rate's value is the mean of the per-simulation rates. A simulation with no resolved trials contributes no rate. The 95% interval is the normal approximation across those rates: sample variance with `n - 1`, margin `1.96 * sqrt(variance / n)`, clamped to `[0, 1]`. It is omitted when fewer than two simulations have a rate. The longest gap and the turns to transform are not proportions, so they have no interval.

## Metrics

The report lists only metrics this simulator computed. Recovery sufficiency is omitted because enemy damage is not simulated. A metric with no samples of its kind is omitted too. `value` is null when every sample was unresolved.

Each metric carries `successes`, `trials`, `unresolved`, and `failures`. A failure code is sorted and counted. Codes are `unmet:{clause_id}`, `unknown:{clause_id}`, `board_unobserved`, `no_generator`, `no_heal_orbs`, `no_active_effect`, `unavailable:{kind}`, `not_ready`, `bound`, `unknown_cooldown`, and `not_transformed`.

- `leader_activation` is the share of turns on which every leader clause is met. A turn with any unknown verdict is unresolved, not a miss. A turn that was not matched also records `board_unobserved`. No leader clauses omits the metric.
- `offensive_activation` uses leader and helper clauses that carry a verified added-combo, fixed-damage, or attack multiplier. `defensive_activation` uses a verified damage reduction or shield. A clause that is both is counted in both. A turn is met only when every selected clause is met. No such clauses omits that metric.
- `longest_activation_gap` is the longest run of turns, in any simulation, that were not leader activations. An unmet turn and an unresolved turn both extend the gap. It is omitted together with leader activation. Its value is that length. It has no interval, and its failures are the leader-activation failures.
- `turns_to_transform` is the mean of the first turn on which a member with a verified transform casts it. A member who never does is `not_transformed`. It is omitted when nobody can transform, and it has no interval.
- `skill_availability` is read after the turn's tick and before casts. A member with an exact counter at zero who is not bound is ready. An inexact counter is unresolved (`unknown_cooldown`). A bound exact counter is a miss (`bound`). Any other known counter is `not_ready`.
- `healing_orb_availability` is met when the matched or kept heal orbs are above zero and the board was observed. An observed board with none is `no_heal_orbs`. An unobserved board is unresolved.
- `orb_generation_uptime` is the share of turns that applied a verified board change or skyfall, or still had one active for a known duration. The ordinary refill is not a generator. A turn without one is `no_generator`.
- `active_effect_uptime` is the share of turns that ended with a known-duration effect still up. A team with none is a real zero (`no_active_effect`).
- `mechanic_response_availability` is present only when the scenario names mechanics. A named mechanic is available when that verified effect was applied this turn or is still active for a known duration. Otherwise the turn records `unavailable:{kind}`.

## Card explanations

`evaluate_team` explains every fielded card. The role is that card's verified leader clauses and verified active effects. An effect the decoder did not verify is named and stays unresolved. The unique contribution is a verified effect kind no other fielded active also has, plus the leader slot's clauses, which are what activation consistency measures.

Removing a sub, assist, or helper runs `evaluate_team` again without that card, with the same scenario, ruleset, seed, turn count, and simulation count. Removing the leader runs the battle without that card. No other leader is evaluated, and the reduced skill-boost total is left unset rather than attributed to a substitute. The removal lists each component before and after, the kinds the team loses or gains, and the skill-boost total and requirement when both evaluations checked a team.

`alternatives_considered` is empty and `ranks_higher_because` is null. A ranking reason is rejected unless an alternative card was actually evaluated. This function does not evaluate a replacement.

An assist replaces the host active. The explanation records the host minimum cooldown and the assist minimum cooldown. Charge fills the host cooldown before the assist counter. When the assist cooldown is longer, the host active stays unavailable while the assist is equipped. Removing the assist returns the host active in the re-evaluation.

`compare_teams` evaluates two supplied teams under one scenario and lists the measured differences, including skill boost, transform, the five components, and which cards still have unresolved effects. `selected_team` stays null. Comparing two teams does not mark any card as having been compared with an alternative.

## What a report may claim

A report lists the turns, the simulation count, the seeds, and the assumptions it used. An activation result names this policy (`policy_approximation`). A leader clause is met or missed per turn from the board the policy actually matched. Metrics the simulator did not compute are omitted, and each computed metric lists why its misses happened.
