# PADBox — Complete Team Evaluation & Optimization Engine

You are a senior software engineer and algorithm designer continuing development of PADBox, an existing local-first Puzzle & Dragons (PAD) collection scanner, mechanics database, and team-building toolkit.

I am an experienced developer on macOS, playing Puzzle & Dragons North America on iPhone.

The collection scanner, inventory management, monster data ingestion, skill decoding, candidate discovery, and basic team validation systems already exist.

Your task is to **complete the team-building system**, transforming PADBox from a collection-aware mechanics explorer into a trustworthy, explainable, and effective team optimizer.

This is an implementation project, not merely a planning or research exercise.

Work incrementally, run tests, and implement as much as possible autonomously. Do not stop after creating a roadmap.

---

# 1. Read the existing implementation and audit

Before making changes, read:

- `docs/team-builder-audit.md`
- `docs/team-builder-scoring.md`
- `docs/team-builder-trace.json`
- `docs/team-builder-issues.md`
- `docs/architecture.md`
- `AGENTS.md`
- Existing mechanics code under `src/padbox/mechanics/`
- Relevant tests

The audit establishes several important facts:

1. There is currently no actual team optimizer or team-quality scoring function.
2. Candidate discovery uses independent utility buckets and lexicographic ordering.
3. `check_team` checks a caller-supplied lineup but does not establish gameplay quality.
4. Team structure validation permits five subs without a helper and incomplete lineups.
5. Entry, transformed, and inherited actives can be treated as simultaneously available.
6. Selected sub transformation costs are not fully modeled.
7. Unknown effects can push useful monsters outside candidate shortlists.
8. Orb generation is evaluated categorically rather than through actual board availability.
9. Assist recommendations are local rather than jointly optimized.
10. Some evolving skills and complex leader conditions remain unsupported.

Treat these as starting points, not unquestionable truths. Verify them against the current code, since the repository may have changed since the audit.

**Preserve the existing collection scanner and its working functionality.**

If `AGENTS.md` currently prohibits team optimization, recognize that this request explicitly authorizes implementing it. Update the relevant project guidance rather than silently ignoring contradictory instructions.

---

# 2. Target user experience

The eventual CLI should support:

```bash
padbox evaluate --team team.json
padbox optimize --leader 13967 --server NA
padbox optimize --leader 13967 --helper 13967 --server NA
padbox optimize --leader 13967 --dungeon dungeon.json
padbox compare team-a.json team-b.json
```

The optimizer should:

- Use only monsters I own, except for a helper explicitly supplied or permitted by the selected helper policy.
- Consider legal evolutions and transformations.
- Respect ownership quantities and resource reservations.
- Choose four subs.
- Select a compatible helper.
- Recommend assists where appropriate.
- Evaluate skill rotations and activation consistency.
- Check survivability, healing, damage, and dungeon mechanics.
- Explain why each monster was selected.
- Identify weaknesses and unresolved assumptions.
- Produce alternative teams when meaningful.

The optimizer should be useful both for general-purpose teams and dungeon-specific builds.

It should never claim a team is optimal without defining the objective and the scope of the search.

---

# 3. Architecture: separate knowledge, evaluation, and search

Maintain three distinct layers.

## Layer A — Mechanics and game-state model

Responsible for interpreting PAD monster data and representing game rules.

It must not select teams.

## Layer B — Team evaluator

Responsible for determining how a supplied team performs under a specified scenario.

It must not search for better teams.

## Layer C — Team optimizer

Responsible for constructing candidate teams, calling the evaluator, comparing results, and returning ranked recommendations.

All three layers should have independent tests.

Prefer deterministic, typed data structures and explicit uncertainty reporting.

Avoid using an LLM as the authoritative source of PAD game mechanics or as the scoring function.

---

# 4. Milestone 1 — Fix validation semantics and correctness

Start by addressing the highest-impact issues from the audit.

## 4.1 Separate validation concepts

Replace the overloaded meaning of `ok` with distinct results:

- `ownership_valid`
- `evolution_feasible`
- `team_structure_valid`
- `team_complete`
- `transform_feasible`
- `mechanics_coverage`
- `strategic_quality`
- `unknowns`

A team with one leader and five subs must not be represented as a valid complete standard team.

A partial team may still be evaluated, but it must be explicitly marked partial.

Standard team structure:

- One leader
- Four subs
- One helper

Do not confuse a missing helper with permission to add a fifth sub.

Preserve backward compatibility where practical, but deprecate ambiguous result fields.

## 4.2 Fix fail-open behavior

Unknown transformation requirements must produce an `unknown` or `unresolved` state, not a passing zero-cost requirement.

A missing decoder result must never silently mean that no mechanic exists.

Use three-valued results where appropriate:

- Pass
- Fail
- Unknown

## 4.3 Ownership and evolution feasibility

Validate:

- Duplicate copy limits
- Reserved leader ownership
- Sub ownership
- Assist ownership
- Evolution-path feasibility
- Mutually exclusive forms
- Material consumption
- Transform base forms
- Special evolution requirements

Distinguish exact validation from assumptions about untracked stackable materials.

## 4.4 Regression tests

Add tests for every confirmed high-priority issue in `team-builder-issues.md`.

Do not remove or weaken existing tests merely to make the new behavior pass.

**Acceptance criterion:** A structurally invalid team cannot be reported as a valid complete team, and missing mechanics data cannot silently satisfy a hard requirement.

---

# 5. Milestone 2 — Improve mechanics decoding and confidence

Before optimizing, make the mechanics representation sufficiently reliable.

## 5.1 Structured effects

Expand the existing decoder to represent:

- Orb creation, conversion, and board replacement
- Orb quantities and source-color dependencies
- Lock and unlock behavior
- Skyfall modifiers
- Roulette effects
- Damage reduction
- HP and RCV modifiers
- Healing
- Damage absorption void
- Attribute absorption void
- Combo absorption void
- Damage void penetration
- Bind, awoken bind, and unmatchable recovery
- Skill haste and delay
- Transformations
- Evolving and multi-stage skills
- Active skill durations
- Damage caps
- Attribute changes
- Relevant awakening effects

Do not assume all effects are independent. Some skill components interact or have conditional behavior.

## 5.2 Skill lifecycle

Represent skills as state machines where necessary.

For example:

```text
Base active
    |
    | Transform
    v
Transformed active
    |
    | Cooldown cycle
    v
Reusable transformed active
```

For evolving skills, represent the full sequence of stages, including whether the sequence loops or terminates.

Do not flatten these into a single reusable active.

## 5.3 Leader skills

Represent activation clauses explicitly:

- Attribute requirements
- Type requirements
- Linked-orb counts
- Cross matches
- L-shaped matches
- Rows
- Combo thresholds
- HP thresholds
- Multiple-color conditions
- Fixed move time
- Added combos
- Fixed damage
- Shields
- Attack multipliers

Preserve distinctions between independent clauses.

For example, a leader might grant damage reduction for five linked Light orbs but require a Light cross for an additional offensive effect.

These must be evaluated separately.

## 5.4 Confidence and provenance

Every interpreted mechanic should retain:

- Source
- Decoder version
- Evidence level
- Supported parameters
- Unknown parameters
- Confidence or verification status

Separate syntactic decoding from verified game-rule semantics.

Unsupported mechanics must be surfaced rather than silently ignored.

## 5.5 Specific regression fixtures

Use verified data for:

- Rozé #13966 → #13967
- Ultraman Gaia (V2) #8336
- Super Reincarnated Apollo #7844
- Ordeal Venus #10756
- Paladin #7949
- Saphira #7095 → #7096

Verify monster IDs and skill details against the current source data before constructing expectations.

Do not perpetuate the earlier #8736 Gaia identification error.

**Acceptance criterion:** The mechanics model can explain how a skill changes game state and when it is available, not merely classify its text.

---

# 6. Milestone 3 — Implement a deterministic turn-based evaluator

This is the most important new subsystem.

Build a game-state simulator that can evaluate a supplied team over multiple turns.

Do not attempt to reproduce the entire PAD battle engine initially.

Implement the smallest model that can reliably answer the questions relevant to team construction.

## 6.1 Game state

Represent:

- Current turn
- Team members and current forms
- Skill cooldowns
- Skill evolution stages
- Assist cooldowns
- Active effect durations
- Board state
- Orb locks
- HP and maximum HP
- RCV modifiers
- Shields
- Relevant awakenings
- Leader/helper activation conditions

Use deterministic random seeds for reproducible simulations.

## 6.2 Skill scheduling

Model:

- Starting cooldowns
- Initial Skill Boosts
- Transformation readiness
- Skill haste
- Skill delays
- Cooldown progression
- Skill use
- Evolving skills
- Assist inheritance and overcharge
- Skill durations
- Shared active-slot constraints

A monster must not use a base active and inherited active independently when the game rules make them mutually exclusive.

## 6.3 Orb generation and matching

For a leader requiring a Light cross:

- Track actual Light-orb availability.
- Track conversion source orbs.
- Model generated orb quantities.
- Check whether a valid cross arrangement is possible.
- Distinguish five linked Light orbs from a Light cross.
- Preserve healing orbs where relevant.
- Account for board size and orb locks.
- Model skill-generated board changes.

Start with a simplified but explicit matching policy.

Do not claim to simulate optimal human orb movement unless an actual solver exists.

If board geometry is not yet modeled accurately, report activation estimates as approximations.

## 6.4 Simulation modes

Support two modes:

**Deterministic scenario mode**

Evaluate a fixed board sequence and skill-use policy. Useful for tests and reproducible debugging.

**Monte Carlo mode**

Evaluate many seeded board scenarios to estimate activation and recovery consistency.

Expose:

- Number of simulations
- Number of turns
- Random seeds
- Assumptions
- Confidence intervals where appropriate

## 6.5 Metrics

Report:

- Leader activation rate
- Full offensive activation rate
- Defensive activation rate
- Orb-generation uptime
- Longest activation gap
- Turns to transform
- Skill availability
- Healing-orb availability
- Recovery sufficiency where modeled
- Active-effect uptime
- Mechanic-response availability
- Failure reasons

Never invent metrics that the simulator does not actually calculate.

**Acceptance criterion:** Two teams with different orb-generation rotations should produce meaningfully different, reproducible activation results.

---

# 7. Milestone 4 — Build a team-quality evaluator

Implement:

```python
evaluate_team(team, scenario, ruleset) -> EvaluationReport
```

The evaluator should return both feasibility and strategic quality.

## 7.1 Hard constraints

Hard constraints include:

- Valid team structure
- Ownership and copy limits
- Evolution feasibility
- Leader/helper legality
- Known transformation requirements
- Known scenario-specific requirements

Unknown requirements should remain unresolved rather than automatically passing or failing.

## 7.2 Quality dimensions

Evaluate separately:

**Activation consistency**
- How frequently leader skills activate
- How often offensive and defensive conditions overlap
- Whether generators support sustained activation

**Survivability**
- Effective HP
- Shield uptime
- Recovery capacity
- Ability to survive modeled enemy attacks

**Mechanic coverage**
- Damage absorption
- Attribute absorption
- Damage void
- Awoken bind
- Unmatchable
- Other scenario-specific requirements

**Damage**
- Attribute compatibility
- Damage awakenings
- Attack multipliers
- Damage caps
- Damage consistency

**Flexibility**
- Available utility slots
- Assist flexibility
- Rotation resilience
- Redundancy where beneficial

## 7.3 Scoring design

Do not immediately combine everything into a naive weighted sum.

Use hierarchical evaluation:

1. Exclude invalid teams.
2. Identify unresolved feasibility.
3. Enforce essential scenario requirements.
4. Rank feasible teams by strategic quality.
5. Use secondary metrics to break ties.

For general-purpose teams, use a documented default scenario and configurable priorities.

Allow different objectives for:

- General farming
- High-difficulty dungeons
- Boss damage
- Consistent clearing
- Fast clearing

Any scalar score must be explainable and traceable to its component metrics.

Do not reward redundant utility indefinitely.

Use marginal contribution and diminishing returns where appropriate.

## 7.4 Counterfactual explanations

For each selected card, explain:

- Its primary role
- Its unique contribution
- What becomes worse if it is removed
- Which alternatives were considered
- Why the chosen card ranks higher

If no reliable alternative comparison was performed, do not claim one.

**Acceptance criterion:** The evaluator can compare two supplied teams and identify meaningful differences without relying on arbitrary keyword counts.

---

# 8. Milestone 5 — Implement actual team optimization

Only begin this after the evaluator is functioning and tested.

## 8.1 Candidate generation

Generate candidates from the owned collection.

Include:

- Directly owned forms
- Reachable evolutions
- Transformable forms
- Relevant assists
- Duplicate copies
- Alternative helper leaders

Do not allow a single owned copy to occupy multiple roles.

## 8.2 Candidate pruning

Prune based on:

- Hard incompatibilities
- Unreachable forms
- Ownership conflicts
- Missing essential roles
- Clearly dominated candidates

Do not automatically eliminate a card because it has an unrelated undecoded effect.

Use uncertainty as a separate ranking dimension.

Track pruning decisions and provide reasons.

Ensure useful candidates such as Apollo are not hidden by arbitrary shortlist limits.

## 8.3 Search algorithm

Choose an algorithm appropriate for the search space.

Possible approaches include:

- Constraint-based candidate filtering
- Beam search
- Branch-and-bound
- Integer programming
- Local search
- Hybrid approaches

Do not use brute-force enumeration if the candidate space makes it impractical.

Use a staged approach:

1. Generate a broad candidate pool.
2. Enforce ownership and role constraints.
3. Construct plausible complete teams.
4. Apply a cheap deterministic evaluator.
5. Retain diverse high-potential teams.
6. Run expensive simulations on finalists.
7. Rank and explain the results.

Document the algorithm and its limitations.

Do not claim global optimality unless exhaustive or mathematically proven.

## 8.4 Helper selection

Support:

- Explicit helper
- Fixed helper pool
- Compatible helper discovery

Clearly distinguish owned helpers from friend helpers that are assumed available.

Do not invent friend availability.

## 8.5 Assist optimization

Treat assists as part of the full team configuration.

Account for:

- Ownership and copy reservations
- Assist awakenings
- Skill Boosts
- Active skill inheritance
- Cooldown interactions
- Attribute changes
- Hazard resistance
- Assist availability

Avoid recommending assists that unintentionally overwrite essential base-skill timing.

Initially optimize assists for shortlisted teams, then consider joint optimization if necessary.

## 8.6 Output

Return multiple ranked teams, not just one.

For each team provide:

- Complete lineup
- Helper
- Assists
- Role coverage
- Transformation plan
- Skill rotation
- Activation metrics
- Damage and survival estimates
- Weaknesses
- Unknown mechanics
- Why it was selected
- Alternatives and trade-offs

**Acceptance criterion:** The optimizer can generate complete legal teams, evaluate them, and explain why the top-ranked team outperforms other tested candidates.

---

# 9. Milestone 6 — Dungeon-specific optimization

Support optional structured dungeon profiles.

Example:

```json
{
  "name": "Example Dungeon",
  "floors": [
    {
      "enemy_hp": 10000000000,
      "preemptive_damage": 1000000,
      "mechanics": [
        "damage_absorb",
        "awoken_bind"
      ]
    }
  ]
}
```

This is a schema example, not real dungeon data.

Dungeon profiles should be validated and versioned.

Model encounter-specific requirements such as:

- Preemptive damage
- Resolve
- Damage absorption
- Attribute absorption
- Damage void
- Awoken binds
- Unmatchable status
- Skill delays
- Enemy shields
- HP thresholds

Do not claim a team can clear a dungeon unless the modeled requirements support that conclusion.

For unmodeled enemy behavior, explicitly identify the uncertainty.

---

# 10. Milestone 7 — CLI, reports, and user interface

Expose the new engine through Typer CLI commands.

Provide both machine-readable JSON and human-readable reports.

Recommended output sections:

```text
TEAM EVALUATION

Legality
  Structure:
  Ownership:
  Evolution:
  Transformation:

Activation
  Offensive activation:
  Defensive activation:
  Orb supply:
  Longest activation gap:

Utility
  Damage absorption:
  Attribute absorption:
  Damage void:
  Awoken bind:
  Unmatchable:

Survivability
  Effective HP:
  Healing:
  Shield uptime:

Damage
  Estimated output:
  Damage cap limitations:

Uncertainty
  Unsupported mechanics:
  Unverified assumptions:

Recommendations
  Ranked alternatives:
  Suggested assists:
  Skill rotation:
```

Extend the existing UI if doing so provides meaningful value, but prioritize the engine and CLI first.

The tool should make it easy to inspect why a card was chosen or rejected.

---

# 11. Testing strategy

Testing must be a core part of implementation, not a final cleanup task.

## Unit tests

Test individual mechanics and scoring components.

## Integration tests

Test complete team evaluation and optimization.

## Property-based tests

Where useful, verify invariants such as:

- A single owned copy cannot be used twice.
- Adding an irrelevant unknown effect does not automatically destroy a card's known utility.
- A transformation cannot occur before its prerequisites are met.
- An active cannot be used before it is ready.
- A team cannot exceed its legal slot count.
- An assist cannot be used independently of its host's skill state.

## Regression tests

Use the previous Rozé audit as a reference.

Test the engine against:

- Rozé + Gaia + Apollo + Ordeal Venus + Paladin
- The previously generated Ciel/Kushinadahime/Rathios/Pixel Lightning/Leona lineup
- Teams with insufficient Skill Boosts
- Teams with overlapping clerics
- Teams with conflicting assist cooldowns
- Teams with incomplete mechanic decoding

Use verified card data and explicit helper choices.

Do not assume the Rozé/Gaia/Apollo lineup is automatically superior. The evaluator must demonstrate the trade-offs.

## Performance tests

Measure:

- Candidate generation time
- Team construction time
- Evaluation throughput
- Simulation throughput
- Search runtime
- Memory consumption

Establish reasonable local execution targets based on actual benchmarks.

---

# 12. Engineering requirements

- Python 3.12+
- Preserve existing CLI conventions.
- Preserve existing SQLite data.
- Maintain typed interfaces.
- Use Pydantic where appropriate.
- Keep the simulator deterministic under fixed seeds.
- Avoid unnecessary cloud services.
- Do not require paid APIs.
- Do not depend on an LLM for core mechanics.
- Keep source adapters replaceable.
- Cache expensive intermediate computations.
- Support partial and uncertain data.
- Avoid destructive database migrations.
- Maintain backward compatibility where practical.

Prefer straightforward, testable implementations over elaborate frameworks.

Do not introduce a large optimization dependency unless its benefits justify the complexity.

---

# 13. Required documentation

Maintain:

- `docs/team-evaluator.md`
- `docs/skill-state-model.md`
- `docs/team-optimizer.md`
- `docs/scoring-model.md`
- `docs/known-mechanics-gaps.md`

Document:

- Exact implemented mechanics
- Approximations
- Scoring formulas
- Search strategy
- Unsupported mechanics
- Verification evidence
- Performance characteristics

Update the documentation as implementation changes.

---

# 14. Execution order and autonomy

Implement in this order:

1. Correct team legality and validation semantics.
2. Improve mechanics decoding and state representation.
3. Build a deterministic turn-based simulator.
4. Build the team evaluator.
5. Build the team optimizer.
6. Add dungeon-specific optimization.
7. Improve CLI, reports, and UI.

Do not skip directly to team search.

After each milestone:

- Run relevant tests.
- Report what changed.
- Show an example output.
- Identify remaining gaps.
- Confirm whether the milestone's acceptance criteria were met.

Continue to the next milestone autonomously when tests pass and no blocking decision is required.

If a mechanic cannot be modeled accurately, implement a narrower verified model and report unsupported cases explicitly.

Do not invent PAD rules or silently substitute generic RPG assumptions.

---

# 15. Definition of done

The project is complete when I can:

1. Import my monster collection from my iPhone.
2. Select a leader.
3. Generate multiple complete, legal team candidates.
4. Evaluate their actual skill rotations.
5. Measure activation consistency under documented assumptions.
6. Account for transformations and inherited skills.
7. Identify mechanic coverage and weaknesses.
8. Rank candidate teams using a transparent objective.
9. Inspect why each card was selected.
10. Compare alternatives and understand the trade-offs.
11. Optionally optimize for a specific dungeon.
12. Reproduce the results using the same database, ruleset, and random seed.

A successful optimizer is not merely one that produces plausible-looking teams. It must demonstrate that its recommendations outperform reasonable alternatives under the same evaluation criteria.

---

# 16. Start now

Begin by inspecting the current repository and audit reports.

Produce a short implementation plan identifying dependencies and the most important correctness risks.

Then immediately implement **Milestone 1: validation semantics and correctness**, including regression tests.

Proceed through subsequent milestones autonomously, prioritizing correctness, explainability, and reproducibility.

Do not stop at planning, and do not mark a milestone complete unless its acceptance criteria are actually satisfied.
