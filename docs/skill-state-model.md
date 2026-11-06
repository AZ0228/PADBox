# Skill state model

`padbox.mechanics.skill_state` is the active-skill state machine. It does not select a team, schedule a battle, or score one. A later simulator can tick and use these slots. Legality of a transform, an evolution, or an equip stays with the team checker.

A skill is a set of states. The slot has one current state. That state is the only active the monster can use on a turn.

## States

- **Base.** The entry card's active skill.
- **Transformed.** The active on the monster named by a transform effect. The chain follows that effect while the target card is available.
- **Evolving.** One state per stage id in a type 232 skill's parameter list, in that order. The type 232 record is the sequence. It is not itself a castable state.
- **Inherited.** An assist active placed in the same slot.

Compound components of the current skill are one cast. Rozé's bind clear and transform are effects of the base cast. They are not two actives. A transition runs when that cast happens.

`order` lists the skill's own states. An inherited active is stored on the machine and is not inserted into that list.

## One usable active

`ActiveSlot.usable` returns the current state when its counter is ready and the slot has not already been used this turn. Otherwise it returns nothing. `actives_this_turn` is that result as a list of length 0 or 1.

The base active and the transformed active are never both usable. The base active and the inherited active are never both usable. After a cast, the slot stays used until the next tick, including when the next state's minimum is 0.

## Cooldowns

For a skill record whose `initialCooldown` is a positive integer:

- `base` is that `initialCooldown`
- `minimum` is `base - (maxLevel - 1)`, and never below 0

That minimum is the max-skill assumption used elsewhere in the mechanics layer. It is not a measured skill level on a particular owned copy. A record with no positive `initialCooldown` stores a null base and minimum, and the counter starts at 0, ready. A missing skill record sets `known` false: the counter stays null, and that state is not usable.

The counter starts at the current state's minimum. `tick` is one start-of-turn step: it clears the used flag and subtracts one while the counter is positive. The active is usable when the counter is 0. `tick` does not apply haste, skill delay, or skill boosts.

Using a skill that has a known next state moves the slot there and sets the counter to the new state's minimum. `entry_charge` on the slot is `state_minimum`. The snapshot does not record a separate cooldown for the moment after a transform or a stage change. This entry charge is the model rule that keeps the new active from being a second cast on the same turn. Using a skill with no next state reloads that same state's minimum. That is the cooldown cycle of a reusable active.

## Rozé

#13966 active 64124, "This...is my resolve.", initial cooldown 28, max level 5, minimum 24. Its transform effect names #13967, the same id as the card's `henshinTo`. #13967 active 64131, "Fortress Mode", initial cooldown 4, max level 1, minimum 4.

```text
base 64124 (counter starts at 24)
    | use, once the counter is 0
    v
transformed 64131 (counter set to 4)
    | cooldown cycle
    v
reusable Fortress Mode
```

The base cast includes the transform and the other components of 64124. Fortress Mode's effects, including its void pierce, are not part of that cast. The transformed state's effects are 64131 only. The combat leader skill on #13967 is not an active state. While the counter is positive, `use` leaves the slot on the base state.

If the transformed card is not supplied, the transition still names #13967 and the cast still carries the transform effect. The transformed active's effects and cooldown stay unknown, and the slot does not become ready again.

## Saphira

#7095 active 21111, "Cryptic Riddle", initial cooldown 28, max level 1, minimum 28. The same cast creates orbs and transforms. The transform effect names #7096, matching `henshinTo`. #7096 active 21117, "Bright Solution", initial cooldown 2, max level 1, minimum 2.

The shape matches Rozé. The entry cast is one use. After it, the only active that can become ready is Bright Solution, on its own minimum of 2. The transformed state has no transform effect.

## Gaia

#8336 active 26756, "Gaia (V2) Lifting", type 232, parameters `[26757, 26760]`.

| State | Skill | Record | Minimum | Effects of that state |
| --- | --- | --- | --- | --- |
| stage 0 | 26757 | initial 15, max level 6 | 10 | delay |
| stage 1 | 26760, "Gaia Slash" | initial 2, max level 1 | 2 | that stage's components: a lock clear and an orb change |

The monster id stays 8336 on every stage. Using stage 0 casts the delay and moves to stage 1 with the counter set to 2. Stage 1's effects are not in that cast. The two stages are not both usable.

The decoded evolving effect has `loops` null, and `loops` is an unknown parameter. The machine's sequence is `unknown`. Using the last stage casts that stage's effects and sets the counter to unknown. The slot stays on stage 1 and is not used again. Description text is not an input, including the line that says the skill evolves.

## Loops and ends

`sequence` is `loops`, `ends`, or `unknown`.

- `loops` adds a known transition from the last stage back to the first. The first stage's minimum is loaded.
- `ends` leaves the last stage with no further transition. The next use of that stage reloads its own minimum. Earlier stages are not repeated.
- `unknown` adds a transition whose target is unknown. The last stage's effects can be cast once when it is ready. The counter then stays unknown.

`machine_for(..., sequence=)` supplies `loops` or `ends` when that behavior is known from outside the snapshot. The decoded flag is used when the argument is omitted. Gaia's flag is absent, so Gaia's machine stays `unknown`. A missing stage id stays in the sequence with an unknown cooldown. The machine does not join the stages on either side of the gap.

## Inherited active

`with_inherited` sets the current state to the assist skill. The host's base, transformed, and evolving states remain on the machine and are not the usable active. No transition has the inherited state as its source, so a later cast cycles the assist skill instead of restoring the host active.

The inherited counter is that assist skill's own minimum under the same max-skill assumption. #4834, which the snapshot marks `canAssist`, has active 11780, "Sacred Feather", initial cooldown 13, max level 9, minimum 5. Placing that skill in the slot shows the mutual exclusion. It does not decide whether the equip is legal on a particular host.

## Still unresolved

- `tick` subtracts one charge. It does not apply haste, skill delay, skill boosts, or assist overcharge. `padbox.evaluator.sim` applies those around the counter.
- The post-transform and post-stage counter is the labeled `state_minimum` rule.
- Gaia's sequence has no loop-or-end flag in the snapshot decode.
- Leader skills are outside this machine.
- This module does not choose a cast order or report a battle result.
