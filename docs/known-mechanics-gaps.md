# Known mechanics gaps

An effect the decoder does not recognize stays an unsupported effect. A leader component that cannot be placed on one condition stays an unknown clause. Neither is treated as met, and neither is treated as absent. Description text is not used to fill either one.

This list is every unsupported effect on the regression cards' active and leader skills, plus the clause limits below. It is not an inventory of the whole pinned snapshot. Any other unrecognized skill type becomes unsupported in the same way.

Team coverage still folds a cross trigger into the linked color requirement when it searches for orb makers. `evaluate_clauses` does not. That search has not been switched over to these verdicts.

## Unsupported effects in the regression fixtures

The parameters are the skill record. They are not an interpretation.

| Type | Skill | Monster | Role | Parameters | Decoder result |
| --- | --- | --- | --- | --- | --- |
| 66 | 11785 | 4834 | leader | `[4, 1000]` | Unrecognized skill type 66 |
| 66 | 24310 | 7844 | leader | `[7, 5000]` | Unrecognized skill type 66 |
| 124 | 9070 | 3939 | leader | `[8, 1, 0, 0, 0, 2, 700]` | Unrecognized skill type 124 |
| 129 | 8527 | 3821 | leader | `[0, 72, 0, 300, 300]` | Unrecognized skill type 129 |
| 129 | 21115 | 7095 | leader | `[8, 0, 200, 1600]` | Unrecognized skill type 129 |
| 129 | 21121 | 7096 | leader | `[8, 0, 280, 6000, 280]` | Unrecognized skill type 129 |
| 129 | 23729 | 7712 | leader | `[8, 0, 400]` | Unrecognized skill type 129 |
| 129 | 24301 | 7844 | leader | `[8, 0, 270]` | Unrecognized skill type 129 |
| 129 | 41373 | 10756 | leader | `[0, 32, 330, 0, 600]` | Unrecognized skill type 129 |
| 151 | 24549 | 7949 | leader | `[15000, 0, 83]` | Unrecognized skill type 151 |
| 163 | 9895 | 4206 | leader | `[0, 32, 350, 350, 350]` | Unrecognized skill type 163 |
| 169 | 9896 | 4206 | leader | `[5, 1300, 35]` | Unrecognized skill type 169 |
| 171 | 24302 | 7844 | leader | `[8, 8, 0, 0, 2, 100, 50]` | Unrecognized skill type 171 |
| 171 | 41372 | 10756 | leader | `[8, 8, 8, 0, 2, 100, 35, 4400]` | Unrecognized skill type 171 |
| 178 | 9069 | 3939 | leader | `[8, 0, 20, 0, 250, 250]` | Unrecognized fixed move time |
| 183 | 23730 | 7712 | leader | `[31, 0, 0, 0, 0, 50, 100, 300]` | Unrecognized skill type 183 |
| 185 | 11784 | 4834 | leader | `[200, 0, 8, 540, 540]` | Unrecognized skill type 185 |
| 185 | 26764 | 8336 | leader | `[300, 8, 0, 240]` | Unrecognized skill type 185 |
| 192 | 23731 | 7712 | leader | `[32, 4, 2400, 3]` | Unrecognized skill type 192 |
| 200 | 21124 | 7096 | leader | `[24, 6, 20000000]` | Unrecognized skill type 200 |
| 200 | 26770 | 8336 | leader | `[8, 9, 1]` | Unrecognized skill type 200 |
| 200 | 39565 | 3821 | leader | `[8, 6, 1]` | Unrecognized skill type 200 |
| 201 | 41374 | 10756 | leader | `[8, 8, 8, 0, 3, 8000000]` | Unrecognized skill type 201 |
| 209 | 24550 | 7949 | leader | `[3]` | Unrecognized skill type 209 |
| 219 | 21123 | 7096 | leader | `[24, 6, 2]` | Unrecognized skill type 219 |
| 223 | 24311 | 7844 | leader | `[7, 16000000]` | Unrecognized skill type 223 |
| 223 | 27395 | 4206 | leader | `[5, 15000000]` | Unrecognized skill type 223 |
| 223 | 28868 | 7949 | leader | `[10, 30000000]` | Unrecognized skill type 223 |
| 223 | 35673 | 4834 | leader | `[4, 10000000]` | Unrecognized skill type 223 |
| 230 | 45806 | 7844 | active | `[8, 1, 1000]` | Unrecognized attack-buff target |

Each leader row above is also an unknown clause on that leader skill. The active row stays an unsupported effect on the active. A fixed move time of one parameter in the accepted range is a clause; skill 9069's six parameters are not that layout.

## Unknown clauses that are already represented

These parsed clauses still have no verdict.

- Skill 21122, on #7096, is 6+ linked light/dark with 50% damage reduction. The attributes and the threshold are known. The record does not say whether both colors are required, so `combination` is `unknown` and the verdict stays unknown.
- Skill 26756, type 232, on #8336, is an evolving active with stages 26757 and 26760. The `loops` parameter is unknown, so the sequence after the last stage is unknown. See `docs/skill-state-model.md`.

## Parsed clauses

Rozé #13967 leader 64136 is three clauses. They are evaluated separately:

- Skill 64137 sets move time to 12 seconds, with no match condition.
- Skill 64138 is 83% damage reduction with 5+ linked light. The 1x attack field on that skill is not a second bonus.
- Skill 64139 is the cross offense: 200x ATK, +4 combos, and 17,000,000 fixed damage when a cross is used. Its parameters name `cross_attack` and do not name a color. Five linked light does not meet this clause, and a cross does not meet the reduction.

#8336 also has two linked clauses on skill 26765, 38x ATK and 50% damage reduction for 9+ linked light, beside the unknown clauses 26764 and 26770.

Rank EXP on #13966 skill 64128 is a decoded leader effect. It is not an activation clause.

A combo threshold, an HP threshold, an attribute requirement, a type requirement, an L match, a row, and a multi-color count can be evaluated when a clause carries that condition and the observation supplies that fact. None of those conditions is inferred from a cross or from a linked count. The regression leaders do not currently decode a combo-count or HP-threshold skill; those shapes remain available for a clause that names them.
