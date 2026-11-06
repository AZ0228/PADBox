# Scoring model

The evaluator ranks a supplied team with the formulas below. The optimizer may call that ranking only for the objective and the search scope named on the run. `check_team` does not apply these formulas. Its `strategic_quality` stays `not_evaluated` until an evaluation report is produced.

Hard checks come first and are not folded into the score: team shape, ownership, evolution, known transforms, and scenario requirements whose decoder result is `pass` or `fail`. A requirement whose result is `unknown` stays unresolved. An unresolved requirement excludes the team from a ranked list and does not become a zero.

## Component metrics

Each metric is a number in `[0, 1]`, traced to the simulator output named with it.

- **Activation consistency.** Share of simulated turns in which every offensive and defensive leader clause the scenario requires is met by the matching policy. The longest gap between met turns is kept beside the rate and does not enter the scalar.
- **Survivability.** Share of simulated runs in which HP stays above zero, using the effective HP, shield uptime, and recovery the simulator recorded. A scenario with no enemy damage records this metric as unresolved.
- **Damage.** Share of turns in which the recorded attack multiplier, attribute match, and damage-cap state meet the scenario's damage target. Turns with no damage target are left out of the average.
- **Flexibility.** `1 - max(0, unused_utility - 1) / slot_count` after the first copy of a mechanic the scenario already has. Extra copies of a mechanic the scenario still lacks count as useful. `slot_count` is the number of sub and assist slots in the evaluated team.

Mechanic coverage (absorb, void, bind, unmatchable, and any other scenario requirement) is a hard check, not one of these four numbers.

## Objectives

`general` is the default. It does not use a weighted sum. A team sorts by the tuple, earlier components first:

```text
(activation consistency, survivability, damage, flexibility)
```

Ties stay ties. The report prints each component next to the tuple.

The other objectives permute or scale that tuple. The scale is applied to the named component only. Unlisted components stay at weight 1 and keep the relative order they have in `general`.

| Objective | Order, then scale |
| --- | --- |
| `general` | activation, survivability, damage, flexibility |
| `farming` | activation, flexibility, survivability, damage. Flexibility × 1 |
| `high-difficulty` | survivability, activation, damage, flexibility. Survivability × 1 |
| `boss-damage` | damage, activation, survivability, flexibility. Damage × 1 |
| `consistent` | activation, survivability, flexibility, damage. Activation × 1 |
| `fast` | damage, flexibility, activation, survivability. Damage × 1 |

`farming`, `high-difficulty`, `boss-damage`, `consistent`, and `fast` therefore reorder the same four metrics. They do not add a fifth term.

A scalar, when a caller asks for one, is the objective's tuple written as a base-1001 mixed radix integer so the lexicographic order is unchanged:

```text
score = c0 * 1001^3 + c1 * 1001^2 + c2 * 1001 + c3
```

where each `ci` is that position's metric multiplied by its scale, then rounded to the nearest integer in `0..1000`. The report keeps the unrounded components. A team with any unresolved hard check has no scalar.
