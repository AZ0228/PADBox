# PADBox team-builder scoring audit

Audit date: 2026-11-05. Scope: the working tree, including the untracked `src/padbox/mechanics/` implementation. HEAD: `dcd1b5e45f5278062438f243356bf1b228451ae3`; HEAD alone does **not** reproduce this audit. Decoder version: `5`. Source file hashes, inventory input, actual ordering keys, and validation results are in [team-builder-trace.json](team-builder-trace.json). All source paths below are relative to PADBox; line references refer to the audited files, not these reports.

## 1. There is no team objective or winning-team function

**Implemented:** `pool_box`, `shortlist_box`, and `check_team`. **Not implemented:** a team search, a scalar candidate quality score, a scalar team score, an objective to maximize, or a function assigning the best subs, assists, or helper. This is explicit in `src/padbox/mechanics/team.py:1–5,29–125`, `coverage.py:1–5`, and the command dispatch in `src/padbox/cli.py:335–428`.

The actual decisions are lexicographic ordering within independent buckets, local assist suggestions, and a Boolean checklist for a **caller-supplied** team. There is no sum of weighted offensive, defensive, and utility features. In particular, an absorption void does not add 15 points, nor any other number of points. A missing scalar is **undefined**, not zero.

## 2. Every requested quality category

The weight, score normalization, scalar minimum/maximum, and additive/multiplicative contribution are **N/A for every row**: no such score exists. The following table exhausts the requested categories and identifies their actual use.

| Category | Actual implemented operation | Saturation, redundancy, and interaction | Evidence |
| --- | --- | --- | --- |
| Leader compatibility | Derive linked colors; reject a known main attribute outside their union, or a matching card with no awakenings | Hard heuristic; no compatibility multiplier, type check, or pair evaluation; unknown attributes pass | `team.py:446–469,949–984` |
| Orb generation | `*_orbs` or `*_board` answer membership; order matching cards by cooldown tuple | No orb-count, orb/turn, healing-orb cost, activation probability, or extra-generator bonus; one clean provider satisfies the check | `team.py:224–248,559–589,761–813`; `coverage.py:950–998` |
| Healing / RCV | Heals and RCV buffs are decoded and displayed | No quality preference, expected recovery, heart supply, or RCV sufficiency test | `decode.py:196–255,380–407,768–804,927–943`; `team.py:224–248` |
| Damage | Main-attribute and nonempty-awakening gate only | No ATK/stat math, awakening multipliers, cap utilization, enemy defense, or combo/cross damage | `team.py:460–469,963–969` |
| Defense | Shield values decoded; awakening checklist counts in coverage | No effective HP, shield uptime, survival, or defensive ranking | `decode.py:569–591,651–667`; `coverage.py:1098–1137` |
| Skill Boost | Sum SB; descending SB in SB bucket; threshold against leader/helper transform cost | Uncapped sum; above threshold adds no validator benefit; transformed form's SB sorts candidates, entry form's SB validates | `awakenings.py:171–223`; `team.py:629–649,801–813` |
| Cleric | Three independent existence predicates: bind, awoken bind, unmatchable clear | One clean provider per predicate is sufficient; no bonus or penalty for duplicate clerics; clear duration/full-clear magnitude ignored by predicate | `coverage.py:31–53`; `team.py:544–610` |
| Absorption void | Three predicates: attribute, damage, combo | Each saturates at one clean provider; effect turns and magnitude do not affect pass/fail; cooldown affects shortlist only | Same as above; `decode.py:329–349` |
| Damage void | Active `void_pierce` predicate | Saturates at one clean active; match-based awakening is not interchangeable with the active role | `coverage.py:52,1121–1125`; `team.py:599–610` |
| Hazard resistance | Some percentage/count facts available in `cover` | Not a `check_team` requirement or shortlist category; no team aggregate optimization | `awakenings.py:171–207,226–235`; `coverage.py:1098–1137` |
| Assist | Sort by added answer **count**, SB, and other tuple fields | Local added-answer count removes duplicates relative to this host only; ignores leader and other selected cards | `team.py:834–872` |
| Cooldown penalties | Ascending minimum cooldown late in the tuple | No numerical penalty; any earlier tuple field dominates every cooldown difference | `team.py:801–813,860–872` |
| Redundancy penalties | None for subs; local answer-set difference for assist suggestions | Repeated utilities remain separately listed; no team-level duplicate-role penalty | `team.py:73–87,841–848` |
| Synergy bonuses | None | Neither generators working together nor matching assists to a rotation receives a score | `team.py:61–104,364–415` |
| Missing-role penalties | Add a string to `open_holes`; `ok` becomes false | Boolean veto, not subtraction of points; seven roles required regardless of dungeon | `team.py:405–415,544–596` |
| Move-time buffs | Decoded text/details only | No preference and no interaction with fixed 12-second leader time | `decode.py:520–546,738–765`; `team.py:224–248` |

Thus there is Boolean saturation for coverage, not a modeled diminishing-return curve. Multiple utility providers are not rewarded by a team score, because no team score exists. They can consume several independent shortlist positions even after the leader already covers their role.

## 3. Complete sub/form ordering formula

Let a form row `f` have:

- `L(f)`: its group has `leader_copies > 0`.
- `U(f)`: `bool(f.active.unknown_effects)`.
- `E(f)`: `f.availability != "in_box"`.
- `P(f)`: `len(f.spends)`; the number of non-stackable material copies on its stored path.
- `C(f)`: `f.active.minimum_cooldown`.
- `M(f)`: cooldown is not an `int`.
- `S(f)`: static `skill_boost` of this form's normal awakenings.
- `F(f), O(f)`: form monster ID and source owned monster ID.

The ascending sort keys are exactly:

```text
H(f) = (not L(f), U(f), E(f), P(f))
K_cooldown(f) = H(f) + (M(f), C(f) or 0, -S(f), F(f), O(f))
K_boost(f)    = H(f) + (-S(f), M(f), C(f) or 0, F(f), O(f))
```

Python tuple ordering compares the first unequal field; `False < True`. These are not weighted feature vectors. For example, a completely decoded 100-turn in-box card sorts before an unknown 2-turn in-box card when both are unreserved. An in-box slow card sorts before a reachable fast card, if preceding fields match. Spending one material outranks any cooldown improvement when the preceding fields match. `cooldown or 0` means zero is numeric, while `None` is separately flagged missing. There is no clipping of cooldown or SB. (`team.py:801–813`.)

For each bucket, eligible rows are:

```text
not borrowed AND can_damage AND any(bucket_answer in f.answers)
```

The seven utility buckets use `K_cooldown`. Add `skill_boost` using `K_boost` when a leader/helper transform cost is decoded. For each linked-color requirement, add a color bucket: `light_orbs`, for example, for a cross; `full_light_board` for an ordinary link. Duplicate bucket keys across leader/helper conditions are not explicitly merged. (`team.py:73–79,761–791`.)

After sorting, `_take` returns:

```text
pinned = all ordered rows whose group has leader_copies > 0
others = ordered rows without leader_copies
chosen = pinned + others[:max(0, 8 - len(pinned))]
```

This can exceed eight if more than eight pinned forms exist. The pinned flag applies to **every alternative form under the reserved owned ID**, not merely to the selected leader form, and it does not require an additional unreserved copy. There is no deduplication across buckets or within an owned group. (`team.py:794–798`.)

General pool output order is separate from ranking: groups sort by `(not leader_copies, not borrowed, owned_id)`; forms sort by `(availability != "in_box", stage != "entry", form_id)`. (`team.py:161,289–290`.)

## 4. Complete assist ordering formula

Let `R` be the set of **bucket keys**, `A(f)` the form's `answers`, and `h` a host candidate. `_assists` computes the ordered list:

```text
added(e,h) = [a for a in A(e) if a in R and a not in A(h)]
```

An equip must be unborrowed, have awakening 49, and pass the local spare-copy rule:

```text
spare(e,h) = e.copies - e.leader_copies
if e.owned_id == h.owned_id and not h.leader_copies:
    spare -= 1
eligible iff spare >= 1
```

It must also have `len(added) > 0` or `S(e) > 0`. Its ascending key is:

```text
K_assist(e,h) = (
    bool(e.active.unknown_effects),
    -len(added(e,h)),
    -S(e),
    e.availability != "in_box",
    len(e.spends),
    not isinstance(C(e), int),
    C(e) or 0,
    e.form_id,
    e.owned_id,
)
```

Return the first **four**. No helper selection, global copy budget, evolution-material budget, inherited cooldown sum, overcharge protection, or damage-inheritance estimate participates. Shorter assist cooldown wins a late tie. The base active and assist active are subsequently both treated as available providers by `check_team`; there is no shared active timeline. (`team.py:834–872,495–520`.)

**Confirmed key mismatch:** for ordinary links, `R` contains `full_light_board`, while `A(e)` contains `light_board`. The full-board answer therefore never counts in `added`, although other answers/SB may still make that equip appear. For Rozé's cross bucket, both use `light_orbs`, so this particular mismatch does not apply. (`team.py:238–241,781–784,841–845`.)

## 5. Coverage and validation mathematics

Let `X` be the list from `_chosen_actives`: entry and combat leader actives, entry and combat helper actives, and all selected subs/assists, plus their dungeon-entry actives when different. No temporal exclusivity applies.

For a role predicate `p`:

```text
matches(x,p) = exists effect e in x.active_effects:
               e.evidence == "verified" AND p(e)
clean(x) = x has no non-verified active effect
role_closed(p) = exists x in X: clean(x) AND matches(x,p)
```

Every one of the seven role predicates must close. A provider with unknown effects does not close its role alone, but unknown effects on an irrelevant extra card do not force failure. No minimum clear turns, coverage duration, or cooldown bound is checked. (`team.py:544–610`.)

Color closure is also existential. A cross requires a clean `_produce_summary(..., for_cross=True)` provider. An ordinary link requires a clean `_force_summary` provider: a verified one-color board change. The named-team checker does **not** require `_loop.fits`, despite the ordinary-link message saying “keeps a full board ... up.” (`team.py:559–589,613–626`.)

For normal awakenings:

```text
SB(card) = count(awakening 21) + 2*count(awakening 56) - count(awakening 105)
SB(team) = SB(leader entry, if reserved) + SB(helper entry, if present)
           + sum(SB(selected sub's dungeon entry)) + sum(SB(selected equip))
required = max(decoded leader/helper transform minimum cooldowns)
meets = (required is None) OR (leader reserved AND SB(team) >= required)
ok = (problems == []) AND (open_holes == [])
```

`required=None` also occurs when a transformation exists but its active cannot be decoded. It is not exclusively a non-transform case. Chosen subs' transformation costs are absent from `required`. Super/sync awakenings, badge, haste, Skill Charge, latents, and actual acquired skill levels are excluded. (`coverage.py:640–707`; `team.py:629–649`; `explain.py:252–256`.)

Minimum cooldown is calculated assuming max skill level:

```text
minimum = max(0, initialCooldown - max(0, maxLevel - 1))
```

The mechanics report uses this only when `initialCooldown` is a positive integer; otherwise `None`. Stored import columns use a slightly different guard and may contain zero. The team layer reads `skill.raw` and recalculates; it does not consume the stored normalized cooldown columns. (`sources/adapters.py:220–235`; `explain.py:237–264`; `coverage.py:353–374`.)

## 6. The two numerical coverage heuristics are not team scores

**Skill Boost ceiling:** per remaining owned ID choose the entry-stage, zero-non-stackable-spend form with largest SB, then greedily take available copies in descending SB order until the remaining slot count is full. Add leader/helper entry SB. It excludes assists and battle-stage subs but does not impose damage-color or role compatibility. Output explicitly has `is_team: false`. Current Rozé ceiling is **38**, not the named lineup's **20** and not the historical claim of **29**. (`coverage.py:710–774`.)

**Ordinary-link board uptime:** `_loop` adds `1/minimum_cooldown` for the best full-board producer on the leader and helper, then greedily takes the lowest-cooldown entry-stage, zero-spend owned full-board makers while slots remain and uptime is below one:

```text
U = sum(1/cooldown for selected leader/helper board providers)
    + sum(copies_taken / cooldown for greedy owned board providers)
fits = U >= 1 - 1e-9
reported_uptime = round(U, 3)
```

It can overshoot one, ignores readiness and schedule feasibility, ignores unknown sibling effects in this arithmetic, and checks each independent leader condition using its own inventory budget. It is neither a turn simulation nor a jointly feasible team construction. Crosses skip `_loop` entirely and report `loop: null`. (`coverage.py:808–830,1001–1084`.)

## 7. Actual Rozé decomposition

The user's earlier assist assignments and prior selection log are unavailable. The current executable reconstruction uses the five directly owned named subs and no assists, helper `null`.

| Implemented quantity | Actual value |
| --- | --- |
| Leader-entry SB (#13966) | 2 |
| Valkyrie Ciel #4834 SB | 2 |
| Kushinadahime #4206 SB | 2 |
| Rathios #3939 SB | 6 |
| Pixel Lightning #3821 SB | 4 |
| Leona #7712 SB | 4 |
| Assist SB | 0 in this reconstruction; historical value unknown |
| Total / required SB | **20 / 24** |
| Utility roles closed | **6 of 7**; combo absorb remains open |
| Light cross-provider check | Closed existentially; no rotation verified |
| Problems | `[]` |
| Open holes | Missing combo-absorb void; four-SB shortfall |
| `ok` | **false** |
| Leader/orb/defense/healing/damage/assist/penalty/total quality scores | **N/A: no scoring implementation** |

The missing nine SB between the no-assist reconstruction and the historical 29 are an unexplained difference, not evidence for any particular equip assignment. Controlled swaps and exact tuple positions are recorded in the trace and discussed in [team-builder-audit.md](team-builder-audit.md). A team score difference cannot be calculated without inventing a scoring model.
