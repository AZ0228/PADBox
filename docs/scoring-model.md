# Scoring model

`evaluate_team` ranks one supplied team with the formulas below. The optimizer may call that ranking only for the objective and the search scope named on the run. `check_team` does not apply these formulas, and its `strategic_quality` stays `not_evaluated`.

Hard checks come first and are not folded into the score: board size, team shape, a complete team, ownership, evolution, leader legality, helper legality, known transforms, mechanics coverage, each decoder requirement, and each scenario requirement. A requirement whose result is `unknown` stays unresolved. An unresolved hard check excludes the team from a scalar and does not become a zero.

`strategic_quality` on the evaluation report is then:

1. `excluded` when feasibility is `fail`.
2. `unresolved` when feasibility is `unknown`, or when any component in the objective tuple is unresolved.
3. `ranked` when feasibility is `pass` and every component in the objective tuple has a value. The scalar exists only in this case.

Mechanic coverage is computed and reported. It is not one of the four rank terms.

## Component metrics

Each resolved value is a number in `[0, 1]`. The report keeps the formula, the detail, and a trace for every input. A trace with role `value` is the number that entered the component. A trace with role `beside` or `context` is recorded and stays out of the scalar.

### Activation consistency

```text
activation_consistency = leader_activation
```

`leader_activation` is the simulator rate: the mean, across simulations, of the share of resolved turns on which every leader clause is met. That is the share of turns on which every offensive and defensive leader clause is met. A turn the simulator left unresolved is not a miss and is not a success.

These simulator metrics are stored beside the value and do not enter it:

- `offensive_activation`
- `defensive_activation`
- `orb_generation_uptime`
- `longest_activation_gap`

`longest_activation_gap` is also copied to the component's `gap`. It is a turn count, not a rate.

The component is unresolved when the simulator omitted `leader_activation` or that metric's value is null. The battle is omitted when the board is not 6×5.

### Survivability

```text
survivability = (runs with HP above 0) / runs
```

The rate would use the effective HP, shield uptime, and recovery the simulator recorded. The simulator does not apply enemy damage. Recovery sufficiency is not one of its metrics. A scenario with no enemy damage leaves survivability unresolved. `healing_orb_availability` and `active_effect_uptime` are context traces when the simulator computed them. They are not the survival rate, and a team at full HP is not recorded as having survived an attack.

### Mechanic coverage

This dimension is separate from the four-term rank.

When the scenario names a mechanic the simulator can time, `evaluate_team` passes that effect kind to the battle and:

```text
mechanic_coverage = mechanic_response_availability
```

The timed kinds are `bind_clear`, `awoken_bind_clear`, `unmatchable_clear`, `absorb_void`, and `void_pierce`. Attribute, damage, and combo absorption all time as `absorb_void`, because that is the kind the simulator records.

When the simulator omits `mechanic_response_availability`:

```text
mechanic_coverage = (decoder requirements judged pass) / (pass + fail)
```

A reported `mechanic_response_availability` whose value is null stays unresolved and does not fall back to the decoder share. Any decoder requirement or scenario requirement whose result is `unknown` leaves the value unresolved, including when a rate was computed. An unknown requirement is not a covered mechanic and is not a miss. A judged failure lowers the decoder share. It does not by itself zero the other components.

### Damage

```text
damage = (turns that meet the damage target) / (turns with a damage target)
```

A turn with no damage target is left out of the average. The target is met only when the attack multiplier, the attribute match, and the damage-cap state all meet it. The simulator does not record those three facts, and no current scenario sets a damage target, so the average has no turns and the component is unresolved. `offensive_activation` may be stored as context. It is not this share.

### Flexibility

Utility slots are the listed subs, then each sub's assist when one is named, then the leader assist, then the helper assist. `slot_count` is how many of those slots the team has. The leader and the helper are not slots. Their entry-active kinds are already covered before the first slot is judged. An assist is its own slot. Kinds come from the entry active's verified effects. A transform effect is not a utility kind. An evolving skill contributes the first stage only. Absorb subtypes are `absorb_void:attribute`, `absorb_void:damage`, and `absorb_void:combo`.

Walk the slots in that order. A slot is useful when it brings a relevant kind that is not covered yet. A slot whose relevant kinds are all already covered adds one to `unused_utility`. A slot with no relevant kind adds nothing. When the scenario names one of the utility keys above, only those keys are relevant. Otherwise every verified kind is relevant, which is the `general` case. The first copy of a kind fills the lack. A later copy of a kind the team already has is the redundant one. One redundant copy is not penalized. Each further redundant copy subtracts `1 / slot_count`.

```text
flexibility = 1 - max(0, unused_utility - 1) / slot_count    when slot_count > 0
flexibility = 1                                              when slot_count = 0
```

The value is clamped to `[0, 1]`. Four identical utility subs and no assists have `unused_utility = 3` and `slot_count = 4`, so flexibility is `1/2`. Two identical subs have `unused_utility = 1`, so flexibility stays `1`.

## Objectives

`general` is the default. It does not use a weighted sum. A team sorts by the tuple, earlier components first:

```text
(activation_consistency, survivability, damage, flexibility)
```

An unresolved component sorts after every resolved value. Two unresolved components tie on that position. Ties on the whole tuple stay ties. The report prints each component next to the tuple. The scalar is absent while any of the four is unresolved, so an unresolved component is never stored as zero.

The other objectives permute the same four metrics. Every scale is 1. The named scale is the component the objective calls out. It stays 1, and the other components stay at 1.

| Objective | Order |
| --- | --- |
| `general` | activation consistency, survivability, damage, flexibility |
| `farming` | activation consistency, flexibility, survivability, damage |
| `high-difficulty` | survivability, activation consistency, damage, flexibility |
| `boss-damage` | damage, activation consistency, survivability, flexibility |
| `consistent` | activation consistency, survivability, flexibility, damage |
| `fast` | damage, flexibility, activation consistency, survivability |

No objective adds mechanic coverage as a fifth term.

For term `i` from the left, with value `v_i` in `[0, 1]` and scale `s_i`:

```text
rounded_i = min(1000, max(0, half_up(v_i * s_i * 1000)))
score = rounded_0 * 1001^3 + rounded_1 * 1001^2 + rounded_2 * 1001 + rounded_3
```

`half_up` rounds the exact decimal of `v_i * s_i * 1000` to the nearest integer, and a remainder of one half rounds up. `0.5005` with scale 1 becomes `501`. `1001` keeps a higher term ahead of any lower term, because each rounded value is at most `1000`. The report stores every `v_i`, `s_i`, `rounded_i`, and place value on `rank.terms`. The scalar is that sum and nothing else.

## Default scenario

`general` is the scenario `evaluate_team` uses when the caller does not pass one.

- Order: activation consistency, survivability, damage, flexibility. Every scale is 1.
- Seed 0, 10 turns, 1,000 Monte Carlo boards.
- No named mechanic requirements.
- No enemy damage, so survivability is unresolved.
- No damage target, so damage is unresolved.
- Board 6×5, greedy matching, `policy_approximation`.
- Recovery is not requested, so the skill policy does not spend hearts only to heal.

On this scenario the scalar is absent. Activation consistency, mechanic coverage, and flexibility are still reported, and mechanic coverage uses the decoder-requirement share because the simulator has no mechanic list to time.
