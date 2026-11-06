# PADBox team-builder audit — prioritized issue register

Audit: 2026-11-05, current working tree at HEAD `dcd1b5e45f5278062438f243356bf1b228451ae3`, including pre-existing untracked mechanics code. No production or test changes were made. Source hashes and executable results are in [team-builder-trace.json](team-builder-trace.json); architecture and detailed context are in [team-builder-audit.md](team-builder-audit.md); all ranking formulas are in [team-builder-scoring.md](team-builder-scoring.md).

Severity measures the risk of misinterpreting output as a playable, strong PAD team. **High** can invalidate a core team claim; **medium** can materially bias candidates or feasibility; **low** affects reporting or narrower cases. “Confirmed bug” is used for a demonstrated implementation inconsistency, including incompatibility with the standard team structure stipulated by the audit. “Missing feature” is not an assertion that the current project promised to implement it. The findings below are the audit record. Each issue's **Status** says whether later validation work fixed it, left it open, or fixed part of it.

## Summary

| ID | Status | Priority / classification | Finding |
| --- | --- | --- | --- |
| TB-01 | Fixed | High — confirmed bug | Five subs with no helper, and incomplete teams, can report `ok:true` |
| TB-02 | Fixed | High — confirmed bug | Unresolved leader/helper transformation cost becomes a passing null SB requirement |
| TB-03 | Fixed | High — missing feature | Chosen sub transformation costs/readiness are not checked |
| TB-04 | Fixed | High — modeling approximation | One-time entry, combat and inherited actives count as simultaneously available coverage |
| TB-05 | Open | High — missing feature | No actual cross-generator rotation, board, damage, healing or survival model |
| TB-06 | Open | High — missing feature / scope boundary | No optimizer, winning-team function or quality objective exists |
| TB-07 | Open | Medium — modeling approximation | Unknown sibling effects lexicographically dominate useful cooldown improvements |
| TB-08 | Fixed | Medium — confirmed inconsistency | Reserved leader source's mutually exclusive forms consume shortlist positions without spare copies |
| TB-09 | Open | Medium — confirmed bug | Ordinary-link board bucket key does not match assist answer key |
| TB-10 | Open | Medium — modeling approximation | Main-attribute and awakening presence is treated as a legality/damage rule |
| TB-11 | Open | Medium — modeling approximation | Color provider does not prove sufficient orb count or a valid match |
| TB-12 | Partial | Medium — modeling approximation | Reachability uses incomplete material rules and one locally cheapest path |
| TB-13 | Fixed | Medium — confirmed inconsistency | Named-team ordinary-link check omits the uptime condition used by coverage |
| TB-14 | Open | Medium — modeling approximation | Coverage ceilings/loops are independent optimistic constructions, not jointly feasible teams |
| TB-15 | Partial | Medium — modeling approximation | Max-skilled, fully awakened normal loadout is assumed; actual per-copy state absent |
| TB-16 | Open | Medium — data-quality concern | Mechanics cache lookup lacks fetch-path hash revalidation and uses one leader-version index |
| TB-17 | Open | Medium — data-quality concern / evidence gap | Previous 29-SB result and historical selection cannot be reproduced from supplied details |
| TB-18 | Fixed | Low — confirmed reporting bug | `candidate_copies`/`omitted_copies` count monster-ID groups, not copies |
| TB-19 | Fixed | Low — confirmed reporting omission | Leader evolution investment is absent from named-team `investments` |
| TB-20 | Partial | Medium — data-quality concern | Gaia regression fixture pins #8336; evolving skill type 232 still unsupported |

## TB-01 — Standard team shape is not enforced

**Status:** Fixed. A leader plus five subs and no helper fails team shape. A leader plus one sub stays partial (`team_complete: false`, `ok: false`). A leader, four subs, and a helper can pass the hard checks. Tests: `test_five_subs_without_a_helper_are_not_a_standard_team`, `test_check_accepts_a_team_that_closes_the_holes`, `test_four_subs_without_a_helper_stay_partial`, `test_a_leader_four_subs_and_a_helper_can_pass_the_hard_checks`.

**Evidence:** `src/padbox/mechanics/team.py:293–310,372–374` uses `4 if helper else 5`. It checks only `len(subs) > sub_slots`, not exact occupancy or helper presence. `ok` at lines409–415 has no other team-shape condition. The same slot convention appears in `src/padbox/mechanics/coverage.py:235–249`.

**Observed example:** Audit probes returned `ok:true` for leader + five supplied subs + helper null, and for leader + one sub + helper null. Both had empty `problems` and `open_holes`. Existing `tests/test_team.py:215–227` explicitly asserts the latter success.

**Impact:** `ok` can describe a checklist-complete layout that is not the standard leader/four-subs/helper team. This is an explicit internal convention, not serialization corruption. There is no separate partial-mode flag making the distinction visible.

**Missing regression evidence:** Standard complete team, helper omitted, fifth sub, too few subs, with completion status separate from consistency/coverage if partial analysis is intentional.

## TB-02 — Unresolved transformation fails open

**Status:** Fixed. A missing transform active is `unknown`. An unresolved helper keeps a known leader cooldown. A missing skill-index component stays `unknown`. Tests: `test_unresolved_transform_is_unknown_not_a_zero_turn_pass`, `test_unresolved_helper_keeps_the_leader_transform_cost`, `test_missing_component_index_does_not_pass_a_transform`.

**Evidence:** `src/padbox/mechanics/coverage.py:693–705` yields `required_turns=None` unless a decoded transform target matches. Lines667–669 set the combined requirement to null when any leader/helper transform is unresolved. `src/padbox/mechanics/team.py:591–595` adds a shortfall only when required is non-null; lines645–647 make `meets` true when it is null. `_check` does not propagate `cover.holes` into failure.

**Observed example:** Synthetic leader with a transform relationship but no transform active passed `check_team` with zero SB, `required:null`, `meets:true`, `ok:true`. `cover` has an explicit warning for that unresolved transformation at `coverage.py:1281–1287`.

**Impact:** Lack of knowledge can be mistaken for absence of a readiness requirement. A missing helper transform can also hide a known leader requirement because the aggregate becomes null.

**Missing regression evidence:** Missing/unknown transform active, unresolved helper with known leader, absent component index, and visibly different unknown-vs-no-transform states.

## TB-03 — Sub transformation requirements are omitted

**Status:** Fixed. A selected sub transform can require more Skill Boost than the leader. Tests: `test_selected_sub_transform_can_cost_more_than_the_leader`, `test_transformed_sub_under_a_non_transform_leader_is_not_free`.

**Evidence:** Transform requirement construction at `src/padbox/mechanics/coverage.py:653–669` includes only leader and helper. Selected sub battle/entry actives are included by `src/padbox/mechanics/team.py:512–519`; `_boost_report` at629–649 counts sub entry SB but never their transform cooldowns.

**Observed examples:** Saphira #7096 is a retained two-turn Light generator from #7095's **28-turn** entry transform, but required SB remains Rozé's **24** in every controlled swap. A separate synthetic 99-turn transformed sub passed with zero SB under a non-transform leader.

**Impact:** Apparent access to a desirable combat active can precede the point at which that form can exist. This is not a demonstrated turn-one-ready team, even when the leader threshold passes.

**Missing regression evidence:** Chosen sub cost greater than leader cost, multiple sub transforms, delayed transforms, multi-step transformations, and transform-base route identity.

## TB-04 — Coverage treats incompatible skill states as a bag of effects

**Status:** Fixed for active state. Entry, transformed, and inherited actives are alternatives. Duration and cooldown schedules remain with TB-05. Tests: `test_entry_active_does_not_cover_roles_after_transform`, `test_inherited_active_replaces_the_base_active`.

**Evidence:** `src/padbox/mechanics/team.py:495–520` includes leader/helper entry and combat actives, each selected sub/equip active and the sub's entry active. Roles use an existence test at599–626. There is no shared cooldown, state transition, inherited skill override or schedule.

**Observed example:** Rozé's base #13966 supplies full bind clear; combat #13967 does not. Removing Ciel still does not open the bind-clear hole because the entry active continues to count. Assist actives are counted in addition to their hosts, without a base-plus-assist cooldown calculation.

**Impact:** A one-use transformation effect can masquerade as a recurring answer; an assist and essential base active can both satisfy roles regardless of which is actually available. An equip could undermine intended active timing without a warning. No specific dungeon overcharge failure is asserted.

**Missing regression evidence:** Entry skill used once, inherited active overcharge/reset, essential base active availability, competing uses of a single active slot, duration/cooldown schedules.

## TB-05 — Core battle performance is not evaluated

**Status:** Open. `strategic_quality` stays `not_evaluated`. A cross marked available still has `loop: null`, and the check output has no battle score. Test: `test_battle_quality_stays_unevaluated`.

**Evidence:** Cross requirements explicitly skip `_loop` at `src/padbox/mechanics/coverage.py:808–830`; `_produce_summary` at961–998 is categorical. `src/padbox/mechanics/team.py:963–969` substitutes a main-attribute/nonempty-awakening gate for damage. Role and assist selection at224–248,544–626,834–872 contains no HP/RCV/damage/board state.

**Observed example:** Rozé reports `match:"cross", loop:null`; Saphira's CD2 and Pixel Lightning's CD3 are individually ordered but never combined into a supply schedule. Rathios's cap/shield, Leona's RCV and Ciel's healing are displayed but do not improve a quality estimate.

**Impact:** “Consistent orb generation,” effective shield loops, healing adequacy, cross activation, dungeon survivability and damage output are unproven. These cannot be inferred from `ok` or shortlist membership.

**Missing regression evidence:** Alternating generator schedule with source-orb dependencies, board geometry, effect duration overlap, fixed-time interaction, sustained healing and enemy requirements. None can be meaningfully satisfied by the current non-simulation tests alone.

## TB-06 — “Optimal team” is not a current output

**Status:** Open. Pool, shortlist, and check-team still do not score or search, and the CLI has no optimizer command. Test: `test_mechanics_does_not_name_an_optimal_team`.

**Evidence:** `src/padbox/mechanics/team.py:1–5,29–125` implements enumeration, independent shortlists and checking. `src/padbox/cli.py:335–428` exposes `explain`, `cover`, `pool`, `shortlist`, `check-team`. There is no `score_team`, search result or helper selection. Local `AGENTS.md:3` explicitly prohibits adding a best-team search; README calls the optimizer deferred.

**Observed example:** Every requested scalar score and controlled score delta is null in the trace, with an explicit absence reason. The historical team is stored as a user-supplied claim, not an engine selection event.

**Impact:** Another AI must not reverse-engineer numerical weights from list order or claim the code proved this lineup best. No conclusion about the correctness of an unseen external selection process is possible.

**Classification note:** An intentional scope boundary. The high severity applies to downstream interpretation, not to violation of the current repository scope.

## TB-07 — Unknown-effect preference can bury useful short cooldowns

**Status:** Open. Shortlist order still places an unknown effect ahead of cooldown.

**Evidence:** The complete prefix `(not leader_copies, has_unknown, requires_evolution, len(spends))` precedes cooldown in `src/padbox/mechanics/team.py:801–813`. No effect-specific relevance or uncertainty magnitude is used.

**Observed example:** Directly owned Apollo #7844 has a decoded two-turn Light conversion, but type230 parameters `[8,1,1000]` are an unsupported target. It is Light rank124 and SB rank200, outside every displayed list. All clean competing rows rank ahead of it regardless of their cooldown. Its two reachable-source variants are also pruned. The trace preserves both useful decoded effects and the unknown sibling.

**Impact:** A conservative uncertainty preference can dominate the very utility being requested. This is not proof Apollo is optimal; it is proof its useful cooldown does not govern its relative order in this case.

**Missing regression evidence:** Unknown unrelated clause versus unknown utility clause, bounded ranking tradeoffs, and recall of useful candidates after eight-row truncation.

## TB-08 — Pinned alternate forms lack a spare-copy check

**Status:** Fixed. A shortlist pins the fielded leader form. Other forms of that copy appear only when a spare copy remains. Test: `test_pinned_alternate_forms_need_a_spare_copy`.

**Evidence:** `src/padbox/mechanics/team.py:169–173` sets `leader_copies` at group level. `_bucket_order` at805 pins that whole group; `_take` at794–798 retains all its forms before truncating other candidates. No available-unreserved-copy predicate filters sub rows.

**Observed example:** Only one #13966 is owned and reserved. Nevertheless #13966, #13967 and equip #13968 occupy the first three awoken/unmatchable bucket positions. The equip also occupies the first absorb slot even though that sole source cannot become both leader and equip. The final validator would reject double spending; the list still spends its display capacity on those alternatives.

**Impact:** A bucket of eight can contain fewer independent available options; a useful ninth candidate may disappear while unavailable alternatives remain. More than eight pinned forms can exceed the nominal limit.

**Missing regression evidence:** Sole reserved copy, spare second copy, multiple alternative leader/equip forms, independent-copy count versus displayed row count.

## TB-09 — Full-board assist answer-key mismatch

**Status:** Open. The ordinary-link board bucket key still does not match the assist answer key.

**Evidence:** `src/padbox/mechanics/team.py:238–241` creates `light_board`; `_bucket_specs` at779–785 names the ordinary-link bucket `full_light_board`; `_assists` at841–845 requires an answer to be a member of the bucket-key set. These strings cannot match.

**Affected example:** An equip offering only the required mono-Light board and zero SB will not be suggested for that missing answer. With another utility/SB it might appear, but the board is absent from `adds` and the answer-count rank.

**Impact:** Under-ranking/exclusion of relevant board assists for ordinary-link leaders. Rozé uses `light_orbs`, so this mismatch does not explain this specific run.

**Verification:** Confirmed by literal key paths; not a historical runtime event. Missing regression: a sole full-board equip for an ordinary linked leader.

## TB-10 — Damage heuristic is enforced as a hard problem

**Status:** Open. Main attribute and a non-empty awakening list are still a hard problem.

**Evidence:** `src/padbox/mechanics/team.py:446–469,949–969` unions linked attributes, requires a known main attribute within the union, and rejects matching-main cards with empty awakenings. Missing/untyped raw attributes pass. Types, subattributes, awakening damage magnitudes, actual ATK and assist attribute changes are unused.

**Affected example:** A support whose useful damage comes from a matching subattribute can be rejected; a card with any non-damaging awakening list can pass. Helper checking uses the entry card even when a transformed combat form is supplied. These are code consequences, not a quantified game-damage comparison.

**Impact:** “Problems” mixes strategic preference with ownership legality, potentially excluding valid support cards or suggesting unsupported damage assurance. Two leaders' colors are unioned rather than evaluated as independent party-compatibility obligations.

**Missing regression evidence:** Subattribute contribution, utility-only support, unknown attribute data, transformed helper attribute changes, exact leader type restrictions.

## TB-11 — Categorical orb provider overstates activation evidence

**Status:** Open. A color provider is still recognized by destination color, without an orb count or a board.

**Evidence:** `src/padbox/mechanics/coverage.py:961–998` checks destination color membership. It does not compare created orb count with `minimum_linked`, verify conversion source availability, or build a board. Cross shape is inferred globally from any cross trigger at879–887. `src/padbox/mechanics/team.py:559–589` accepts one clean provider.

**Affected example:** A one-Light-orb creation effect, a conversion with no present source orbs, a mixed/random column destination, or a future-turn skyfall buff can satisfy this categorical Light-provider predicate. Mono-board rejection is useful but insufficient to show an actual cross.

**Impact:** “Available” means a recognized type of source exists, not that activation is guaranteed. The distinct linked shield and awakening-dependent cross bonuses are not separately evaluated after shape inference.

**Missing regression evidence:** Counts below five, board arrangement, color starvation, locks, source depletion, matching Cross Attack awakening, linked-shield-only activation and independent clauses.

## TB-12 — Reachability and evolution costs are incomplete

**Status:** Partial. Equal-cost routes, cumulative material demand, uninterpreted special requirements, and stackable assumptions are reported separately from exact spends. Depth stops at 8, and one transform step is appended. Reverse evolution is still not enumerated. Tests: `test_equal_cost_routes_and_cumulative_materials`, `test_special_evolution_requirements_stay_unknown`, `test_evolution_depth_and_one_transform_step`, `test_stackable_material_is_free_and_a_specific_monster_is_spent`.

**Evidence:** `src/padbox/mechanics/coverage.py:419–485` only follows forward evolution/assist edges, caps depth8, appends one transform step, ignores stackable material ownership, rejects missing material references, and stores only a fewer-spend-copy path. Equal-count alternative materials and cumulative affordability are not resolved jointly. `_reserve` checks one retained route; final selected demand checks copies at `src/padbox/mechanics/team.py:472–492`.

**Affected examples:** A missing ID65535 in an ordeal recipe cannot be interpreted by generic `_material_cost`. Non-stackable costs repeated across multiple evolution steps may be listed before aggregate demand proves them impossible. An equal-copy-cost route spending an unavailable-after-reservation material can hide another route. Reverse-evolution access and chained transformations are not enumerated.

**Impact:** Both false availability and incomplete candidate recall are possible. Ordinary stackable supplies/coins/leveling are assumed affordable, not verified. The selected-path cost preference is not a global construction proof.

**Verification boundary:** Material policy is explicit and tested; alternative-route and special-recipe examples are code-based risks, not claimed failed historical selections. Missing regression: equal-length different-material paths, cumulative spending, special recipes, and depth/transform boundaries.

## TB-13 — Ordinary-link uptime is dropped by named-team validation

**Status:** Fixed. One 5-turn full board leaves the ordinary-link hole open. Test: `test_one_slow_full_board_does_not_keep_an_ordinary_link_up`.

**Evidence:** `src/padbox/mechanics/coverage.py:813–830,1001–1084` uses reciprocal-cooldown uptime for ordinary-link `status`. `src/padbox/mechanics/team.py:559–589,613–626` instead checks whether one selected clean active makes a mono-board, regardless of its cooldown. Its hole text still says “keeps a full board ... up.”

**Affected example:** A five-turn mono-board provider can close a named-team color hole even though one copy contributes only1/5 to the cover heuristic. Existing one-sub fixture `tests/test_team.py:129–146,215–227` accepts its CD5 answer as sufficient.

**Impact:** `cover` and `check_team` answer different questions using language implying similar continuity. A checked team has no proven rotation. This particular heuristic is skipped for Rozé crosses.

**Missing regression evidence:** One long-CD full-board source versus an actual sufficient schedule, and consistent wording/status semantics between commands.

## TB-14 — Independent box-level feasibility heuristics are not a team

**Status:** Open. Coverage ceilings and per-requirement loops are still independent of one another.

**Evidence:** `src/padbox/mechanics/coverage.py:710–774` greedily maximizes a restricted SB ceiling without damage-color/role checks. `_colors` and `_loop` at777–841,1001–1084 evaluate each requirement independently with the same remaining box budget. `_loop` does not reject unknown-bearing providers or schedule casts.

**Observed example:** Current Rozé coverage ceiling is38 with off-color/other strongest-SB forms; no-assist supplied lineup is20. The ceiling explicitly reports `is_team:false`. Other role reports can simultaneously cite incompatible evolutions of the same copy. Per-condition loop calculations do not reserve copies for each other.

**Impact:** Independent “available” requirements cannot be assembled by conjunction into a globally feasible team. The explicit ceiling disclaimer is correct and should be preserved by consumers.

**Missing regression evidence:** Two requirements competing for one copy, unavailable support slots, full joint resource budgets, and summed-uptime cases without a realizable schedule.

## TB-15 — Actual acquired states are absent

**Status:** Partial. Max skill and the full normal-awakening list are labeled assumptions, and `required_at_base_skill` sits beside the max-skill requirement. Per-copy skill level and awakening unlock are still absent. Test: `test_skill_readiness_separates_base_cooldown_from_max_skill`.

**Evidence:** Source cooldown formula at `src/padbox/mechanics/explain.py:252–256` assumes max skill level. `coverage.Card` at56–64 has no player-level/skill-level/unlock state. `src/padbox/mechanics/team.py:629–649` counts all normal reference awakenings. Super/sync/latent menus are deliberately not selected (`coverage.py:1382–1412`).

**Affected example:** Rozé requires24 at max skill, although its source base cooldown is28. A player's unskilled copy or incomplete normal-awakening unlocks can invalidate the reference-only entry calculation. Potential SA/sync/latent readiness benefits are also omitted rather than assumed.

**Impact:** A static recommendation can require unreported investment or have fewer/more usable starting boosts than the actual player loadout. This is an approximation, not evidence the user's actual Rozé is unskilled.

**Missing regression evidence:** Actual per-copy skill levels/awakening state, explicit upgrade assumptions and separate base/max-skilled readiness reports.

## TB-16 — Cache/version integrity has a different boundary during mechanics reads

**Status:** Open. A mechanics read still loads a cached skill file without the fetch-path hash check.

**Evidence:** `src/padbox/sources/adapters.py:49–57` validates a cached payload's SHA-256 when fetched. `src/padbox/mechanics/explain.py:267–310` instead chooses a sidecar by URL/commit and latest fetch timestamp, then loads its sibling file without hash verification. `coverage.py:130–135` chooses one index using the leader provenance. `database/store.py:105–186` does not remove stale monster IDs absent from later imports.

**Affected example:** A changed cache payload can be consumed by mechanics despite the fetch path's integrity guarantee. Mixed-source/version reference cards can be decoded using a different leader's component index. Curated normalized fields/effects without matching raw skill fields are not the source used by `_load`/`_skill_report`.

**Impact:** Source provenance alone does not prove every runtime decoded component matches that source. No tampering or mixed-version failure was detected in this audit; hashes and actual reference version are recorded.

**Missing regression evidence:** Corrupted cached skill payload on direct explain/check, mixed-version references, curated raw/normalized mismatch, and source replacement completeness.

## TB-17 — Earlier output lacks reproducible provenance

**Status:** Open. The supplied record still lacks the team, equips, and inventory revision needed to reproduce the earlier 29 Skill Boost result.

**Evidence:** User supplied five named subs, helper null, 29/24 SB and prior success, but no equips, source routes, run input revision or saved selection log. Current static SB is20, with missing combo-absorb void. Inventory history includes revision1 and revision2, differing by newly added #11399. Runtime reconstruction records both documents with current implementation.

**Observed result:** Named lineup with no assists: `ok:false`, `problems:[]`, open combo absorb and SB shortfall, unknowns empty. The current implementation has no selected-team or `slots` output field. `check_team` output fields are fixed at `src/padbox/mechanics/team.py:405–415`.

**Impact:** The nine-SB difference and original selection reasoning cannot be attributed honestly. Matching a number by arbitrarily attaching equips would fabricate history. Neither current inventory document is proven to be the previous recommendation's input.

**Missing evidence:** Original team JSON with equips/source forms, inventory revision/export, decoder/reference versions, wrapper/output serializer, and any external ranking instructions or logs.

## TB-18 — Copy-count field names describe group counts

**Status:** Fixed. `candidate_groups` and `omitted_groups` count monster IDs. `candidate_copies` and `omitted_copies` remain aliases of those group counts. Test: `test_pool_group_counts_stay_separate_from_copy_quantities`.

**Evidence:** `src/padbox/mechanics/team.py:54–56` increments once per nonborrowed candidate group. `_omitted` at320–322 returns the size of an owned-ID set difference, not summed quantities.

**Observed example:** `candidate_copies=523` and `omitted_copies=163` partition686 monster IDs; the applied inventory has832 copies. Multiple Ciel/Kushinadahime/Leona quantities do not each create a candidate group.

**Impact:** A downstream reader may confuse group count with inventory copies or search-space size. Actual `copies` in each group remains available for budgeting.

**Missing regression evidence:** Names/contract distinction for distinct IDs versus total quantity, with duplicates.

## TB-19 — Leader evolution investment is not in the check output list

**Status:** Fixed. A leader reached by evolution is the first `investments` row, ahead of a sub evolution. A directly owned leader stays out of that list. Test: `test_leader_evolution_investment_is_listed_with_the_subs`.

**Evidence:** `src/padbox/mechanics/team.py:673–691` builds investments from `selections` only. The leader's reservation is outside those selections; its materials are nevertheless counted by `_copy_problems` at474–477. Leader reservation/path/spends exist in the coverage report at `coverage.py:1175–1185`.

**Affected example:** A leader reachable only by evolution can be constructible and consume materials while `check_team.investments` lists no corresponding leader evolution. `investments:[]` therefore does not certify that no evolution work is needed.

**Impact:** Underreported preparation cost. No claim this affects directly owned entry Rozé, whose reserved path is empty.

**Missing regression evidence:** Leader reservation through evolution, distinguished from sub and equip investments.

## TB-20 — Gaia ID mismatch and unsupported evolving active

**Status:** Partial. Regression fixtures in `tests/fixtures/cards/` record Ultraman Gaia (V2) as #8336 from the pinned 2026-10-30 snapshot and do not include #8736. Test: `test_regression_cards_match_the_pinned_snapshot`. Active type 232 is still undecoded, so evolving-skill stage progression remains open.

**Evidence:** Local reference identifies #8736 as **Feathers of Fierce Wings**; named **Ultraman Gaia (V2)** is #8336. Literal #8736 is not owned/reachable. #8336 is owned once, has source active type232 with params `[26757,26760]`, and is unknown because `src/padbox/mechanics/decode.py:120,1375–1425` only expands compounds116/138 and has no232 leaf.

**Observed result:** #8336 enters the pool through SB4, but is SB rank193 and never appears as a decoded Light generator. Evolved #8337 is reachable, fully decoded for its own supported active, but its attribute-absorb rank10 is truncated. Both literal and corrected comparisons are preserved in the trace.

**Impact:** A name-based external recommendation and numeric-ID lookup can discuss different cards; the named Gaia's staged active cannot be evaluated by this decoder. An absence of generator answers does not establish that the real skill provides none.

**Missing regression evidence:** Evolving-skill stage decoding and state progression. The name/ID check is the fixture test above. No claim that #8736 itself was mistranslated by the adapter.

## Verification status

The installed full suite passed: **217 tests in 73.08 seconds**, including 40 existing mechanics tests. Five extra synthetic diagnostics were run in temporary stores without changing test files. Forty-five real-inventory controlled swaps, five leave-one-out checks, both Gaia interpretations, source/form eligibility and all eligible bucket tuple orders are recorded. No score differences were fabricated: the implementation has no scalar team objective. No production fix, dependency installation, environment reconfiguration or real inventory mutation was performed.
