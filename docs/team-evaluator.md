# Team evaluator

Layer B is `padbox.evaluator`. The turn simulator lives in `padbox.evaluator.sim`. This layer scores one supplied team under a stated scenario. It does not search for another team.

`padbox.mechanics` still owns legality: ownership, evolution, team shape, transforms, and decoder coverage. `check_team` reports `strategic_quality: not_evaluated` until the evaluator writes a score. A passing `ok` is a complete-team checklist, not a battle result.

The board is 6×5. Any other size is unsupported and is reported as such.

Simulation defaults, recorded on every report and overridable by flag:

- 10 turns
- 1,000 Monte Carlo boards
- seed 0

## Matching policy

The simulator uses one deterministic greedy policy. There is no orb-path solver. Every activation rate is an approximation of what this policy achieves on the simulated boards. It is not a claim about human play.

Each turn, after skills have changed the board:

1. Locked orbs stay in place and are unavailable for a match.
2. If a leader clause still needs a cross of a color, and five orbs of that color form a plus (a center with four orthogonal neighbors), the policy makes that cross. Five orbs that only touch in a line are a linked match, not a cross. A full board of the linked color clears as one match and does not satisfy a cross.
3. Otherwise the policy matches the largest connected group of a color that a still-unmet linked-count clause needs.
4. Heart orbs are matched when the scenario records a recovery need for the turn, or when they sit inside a cross or linked group already chosen. Otherwise they stay, so a cross or linked clause can be met while hearts remain.
5. Any other connected group of three or more orbs is then matched, largest group first, topmost then leftmost on ties.

Skyfall fills empty cells from the scenario's seeded board stream. The policy does not rearrange orbs by hand and does not retry a worse match to hunt for a later combo.

## Skill-use policy

One monster has one usable active on a turn: its entry active, its transformed active, or the inherited assist active. Those states are alternatives.

1. Cooldowns tick at the start of the turn. Haste from a skill used earlier applies before that tick. A skill whose cooldown is still above zero is not used. Haste on a transform skill does not reduce that transform's own cooldown.
2. A selected battle form transforms on the first turn its transform skill is ready, before other actives. The transform does not happen before that cooldown and before any evolution the team recorded as a prerequisite.
3. After transforms, the leader, subs in slot order, and the helper each use their current active when that active changes the board toward an unmet clause or answers a mechanic the scenario still lacks. An inherited active replaces the host active for that turn.
4. The policy uses this fixed order. It does not search cast orders.

## What a report may claim

A report lists the turns, the simulation count, the seed, and the assumptions it used. An activation rate names this policy. A leader clause is met or missed per turn from the board the policy actually matched. Metrics the simulator did not compute are omitted.
