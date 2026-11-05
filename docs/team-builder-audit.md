# PADBox — architecture and team-decision audit

Audited on 2026-11-05, using the existing Python 3.12 environment, local reference snapshot, and applied inventory. This is a report of **current implementation**, including pre-existing uncommitted and untracked mechanics code. No production or test code was changed. Paths and line numbers refer to the audited working tree.

## Executive finding and evidence conventions

**There is no implemented team optimizer.** Given a leader and the applied collection, PADBox enumerates reachable forms, reports coverage, sorts forms into independent requirement shortlists, and suggests equips separately for each candidate. A caller must supply a team to `check_team`. No function chooses a winning team, selects a helper, or assigns a total team score. The previous lineup cannot be attributed to an optimizer in this codebase. Its selection may have occurred outside this implementation; no available evidence identifies that process. [Evidence: `src/padbox/mechanics/team.py:1–5,29–125`; `src/padbox/mechanics/coverage.py:1–5`; `src/padbox/cli.py:335–428`; `AGENTS.md:3`; `README.md:3–5`.]

“Verified” means that a decoder recognizes a parameter layout according to its handwritten rules. It does **not** certify game execution, turn availability, activation, recognition accuracy, team legality, or optimality. [Evidence: `decode.py:1–6,98–152`, paths below use the `src/padbox/mechanics/` prefix unless otherwise specified.]

Labels in this report:

- **Implemented:** executable behavior inspected in source and, where stated, reproduced.
- **Partial/modeling approximation:** executable behavior with narrower guarantees than the corresponding PAD mechanic.
- **Planned/not implemented:** documentation mentions the direction, or the requested behavior has no implementation.
- **Confirmed bug:** demonstrable inconsistency or behavior incompatible with the standard six-card structure specified in this request.
- **Concern/unverified:** a specific evidence gap; not a claim of proven in-game failure.

The focused mathematics is in [team-builder-scoring.md](team-builder-scoring.md). The prioritized findings are in [team-builder-issues.md](team-builder-issues.md). [team-builder-trace.json](team-builder-trace.json) is a **partial audit reconstruction**, not a historical search trace. It contains private collection IDs/counts because the requested comparisons require them; no files have been committed.

## 1. System architecture

### Repository, runtime, and boundaries

PADBox is its own Git repository inside the FunCoding workspace. Audited HEAD is `dcd1b5e45f5278062438f243356bf1b228451ae3`. At audit start, `AGENTS.md`, `README.md`, `docs/architecture.md`, and `src/padbox/cli.py` were modified; `src/padbox/mechanics/` and three mechanics test files were untracked. Their pre-existing content was preserved. The trace records source SHA-256 hashes so this report is not mistakenly tied to HEAD alone.

| Location | Responsibility and important entry points |
| --- | --- |
| `pyproject.toml:1–48`, `uv.lock` | Hatchling package, Python ≥3.12, CLI `padbox.cli:app`; dependency lock |
| `src/padbox/cli.py:27–48,51–52` | Typer command registration, data-home configuration, logging, JSON serialization |
| `src/padbox/models.py:9–94` | Pydantic provenance, source skill/monster/relationship, recognition candidate/match, and grid-layout contracts |
| `src/padbox/sources/adapters.py:29–235` | `CuratedSource`, `SnapshotCache`, `FormationSource`; source import and icon cache |
| `src/padbox/database/store.py:11–103,105–202` | SQL schemas, `Store`, migrations, reference ingestion/search |
| `src/padbox/video/reader.py:20–99` | `Frame`, `fingerprint`, `metadata`, `frames`; FFprobe/OpenCV sampling |
| `src/padbox/detection/grid.py:12–188` | `Cell`, `detect`, `_detect`, `_scroll_phase`, `annotate` |
| `src/padbox/recognition/matcher.py:19–135` | `normalize`, `features`, `appearance_similarity`, `Matcher.match` |
| `src/padbox/inventory/pipeline.py:22–116` | `import_media`, `rematch`; file-import orchestration |
| `src/padbox/inventory/processor.py:24–349` | `FrameProcessor.process`, shared frame ingestion, evidence and per-copy tracking |
| `src/padbox/inventory/tracking.py:20–78`, `consensus.py:14–33`, `box_map.py:108–743` | Adjacent alignment/optical flow; ordered-row consensus; `BoxMap.locate`, `join`, `reconcile`, checkpoint/save |
| `src/padbox/inventory/service.py:76–265`, `batch.py:16–153` | Identity acceptance, rejection, copy merge, quantity edits, manual entries, bulk review |
| `src/padbox/inventory/snapshots.py:69–79,164–294` | `current_box`, `_preview`, `preview_scan`, `apply_scan`; immutable applied documents and history |
| `src/padbox/live/session.py:64–377`, `live/server.py:71–89` | `LiveSessions`, loopback `ThreadingHTTPServer`, authenticated local API |
| `src/padbox/live/web/{app,minimap,review-queue}.js`, `style.css`, `index.html` | Browser capture, reconstruction, minimap and review queue; no team search |
| `src/padbox/review/app.py:30–405` | Streamlit review, calibration, batch acceptance, replacement controls |
| `src/padbox/mechanics/decode.py:98–1425` | `Effect`, `decode_skill`, typed leaf decoders and dispatch |
| `src/padbox/mechanics/explain.py:16–310` | `explain_monster`, `kit_members`, `roles_from`, `_skill_report`, skill-cache lookup |
| `src/padbox/mechanics/awakenings.py:168–235` | Names, limited `CHECKLIST` magnitudes, `describe`, `skill_boost`, `total`, `capped` |
| `src/padbox/mechanics/coverage.py:56–318,419–1084` | `Card`, `BoxSession`, `leader_context`, `cover_box`, reachability and coverage heuristics |
| `src/padbox/mechanics/team.py:29–125,364–984` | `pool_box`, `shortlist_box`, `check_team`; local ranking and validation |
| `src/padbox/export/collection.py:12–73` | `collection`, `csv_export`; applied ownership export |
| `tests/`, `scripts/validate_reference_slice.py`, `docs/` | Unit/integration/browser tests, recognition diagnostic, architecture/source/validation handoff |
| `.padbox/`, `data/` | Ignored local databases, source caches, icons, captures, logs, exports and diagnostic artifacts |

Production Python dependencies are NumPy, headless OpenCV, Pydantic, Typer, and Streamlit. SQLite, HTTP serving and URL fetching use the standard library. Browser code is plain JavaScript/CSS/HTML; there is no Node build or ML inference service. FFmpeg/FFprobe are optional external video tools with OpenCV metadata fallback. Dev dependencies are pytest, Ruff, Playwright. Installed versions observed: Python 3.12.16; NumPy 2.5.4; OpenCV 4.14.0.94; Pydantic 2.14.2; Typer 0.27.3; Streamlit 1.66.1; pytest 9.2.0; Playwright 1.64.1; Ruff 0.17.3. [Evidence: `pyproject.toml`; environment metadata read during audit; `src/padbox/video/reader.py:34–99`.]

### Data flow

```text
Pinned public mon_en.json + skill_en.json + ckey.json + icon sheets
                 |
       FormationSource / SnapshotCache
                 +--> raw hashed cache + PNG icons
                 +--> typed Monster / Skill / Relationship
                             |
                    reference.sqlite (schema 1)
                             |
image / movie / live window   |                 skill_en cache
        |                    |                       |
frames -> grid crops -> Matcher                  decode_skill
        |                    |                       |
FrameProcessor + BoxMap -> per-copy observations  typed Effects
        |                                            |
inventory.sqlite draft -> review -> diff/apply        |
        |                                            |
box_state applied document ------> leader_context <--+
                                      |
                        reachable forms + reserved leader
                         /              |              \
                      cover           pool          shortlist
                    checklist       alternatives    bucket order
                                                        |
                                           caller chooses team JSON
                                                        |
                                                   check_team
                                                        |
                                        problems / holes / SB / ok

There is no implemented arrow from shortlist to an automatically selected team.
Collection JSON/CSV exports branch from box_state, not from a team optimizer.
```

### Collection scanning and ownership acceptance

`import_media` fingerprints input bytes, persists the exact layout/sampling configuration and registers an isolated full-box draft. A completed same-file/same-config import is reused; a changed configuration with existing observations is rejected. Sampled frames share `FrameProcessor` with live capture. It skips nearly unchanged views (96×160 thumbnail mean absolute difference below 0.12), detects complete grid cells, saves annotated frames/crops, locates cells in the within-scan spatial map, matches artwork, and transactionally saves observations/copy associations. A missing grid or broken continuity creates tracking uncertainty rather than proving removal or a new copy. Live input similarly checkpoints sequence/timestamp and rejects changed frame dimensions or inappropriate state. [Evidence: `src/padbox/inventory/pipeline.py:22–113`; `processor.py:109–140,231–349`; `box_map.py:195–305,589–743`; `src/padbox/live/session.py:185–247`.]

Artwork matching is a separate numerical model from team ranking. It resizes icons to 64×64 and crops `[10:48,10:54]`, builds a 63-bit DCT hash, HSV histogram and normalized pixels, and uses a stable 96-reference coarse retrieval shortlist for larger libraries. Coarse similarity is `0.65*hash + 0.35*color`. Detailed score is `clip(0.25*hash + 0.15*histogram + 0.35*pixel + 0.25*correlation,0,1)`, with `pixel=max(0,1-2*mean_absolute_pixel_error)` and low-texture correlation gated to 0. Top results tie-break by monster ID. A proposed identity needs score≥0.60 in the processor; the matcher returns three ranked candidates by default. `models.Candidate.score` and its component fields therefore refer to **recognition similarity**, never monster/team quality. [Evidence: `src/padbox/recognition/matcher.py:14–135`; `src/padbox/inventory/processor.py:231–234`; `src/padbox/models.py:53–64`.]

Automatic draft acceptance requires clear copy tracking and either a non-stackable strong match (score≥0.94, lead≥0.055, at least three reference candidates), or the three latest saved distinct frames agreeing on identity with each score≥0.85 and lead≥0.055. Manual decisions are preserved. Bulk identity acceptance has independent user-chosen similarity/separation thresholds (defaults 0.80/0.04), evidence consistency requirements and a stale-preview token; it does not prove those thresholds are calibrated probabilities. Stack quantities require explicit review or an explicit assume-one decision. [Evidence: `src/padbox/recognition/matcher.py:119–135`; `src/padbox/inventory/snapshots.py:93–161`; `src/padbox/inventory/batch.py:16–20,22–153`; `src/padbox/inventory/service.py:203–263`.]

Each observed copy has its own UUID, and repeated/overlapping views reuse spatial copy identity rather than deduplicating by monster ID. Accepted identity and copy-tracking certainty are separate. Applying a scan requires completed capture, nonempty active slots, resolved identity/tracking/quantity, a current diff token, full-box attestation and any supplied expected-slot count. Apply atomically replaces the aggregate saved box and writes before/after history; drafts cannot leak into exports. Real capture completeness and recognition accuracy remain unmeasured beyond the project's stated pilot evidence. A missed row is not reconstructed by the team layer. [Evidence: `src/padbox/database/store.py:31–70`; `src/padbox/inventory/snapshots.py:164–294`; `src/padbox/export/collection.py:12–15`; `README.md:5`; `docs/validation.md`.]

### Database schema

Reference and inventory use separate SQLite files. `Store.connect` enables foreign keys and WAL; schemas are versioned by `PRAGMA user_version`. Target relationship IDs do not have a foreign key, allowing missing targets to survive as source facts. [Evidence: `database/store.py:11–103`.]

| Database/table | Fields and meaning |
| --- | --- |
| Reference `monster` | `id` PK, `name`, `rarity`, `icon`, JSON `provenance`, JSON `raw` |
| `availability` | `(monster_id,server)` PK |
| `attribute` | `(monster_id,position)` PK, numeric `value` |
| `monster_type` | `(monster_id,value)` PK |
| `skill` | `id` PK, name/description, base/minimum cooldown, JSON effects, evidence, JSON raw |
| `monster_skill` | `(monster_id,role)` PK, `skill_id`; active vs leader |
| `awakening` | `id` PK, optional name/effect; ordinary source import inserts IDs only |
| `monster_awakening` | `(monster_id,position,kind)` PK, awakening ID; ordered normal/super lists |
| `relationship` | `(source_id,target_id,kind)` PK, evidence; directed evolution/assist_evolution/transform |
| Inventory `recording` | ID PK, path, JSON metadata/config, capture status, creation time |
| `owned` | UUID PK per observed copy/slot; confirmed/proposed ID, status, tracking certainty, similarity, strong-match flag, recording/merge IDs, level/skill_level/awakenings/assist_id, creation time; v2 adds quantity and quantity_verified |
| `observation` | UUID PK, recording, time/frame, crop rectangle/path, JSON ranked candidates, matched inventory UUID, matching/tracking reasons |
| `review_event` | integer PK, inventory UUID, action, JSON detail, timestamp |
| `box_scan` | recording PK, draft/applied status, creation/application times |
| `box_state` | singleton `id=1`, revision, JSON document, source_scan |
| `box_history` | revision PK, recording, before/after JSON documents, JSON diff, applied time |
| `scan_map_state` | recording PK, JSON checkpoint |
| `scan_map_slot` | inventory UUID PK, recording, segment, column, global y, rectangle, observation UUID |

Reference schema is v1; inventory migrates v1→v2→v3. Copy UUIDs survive scan review but **team mechanics uses aggregate quantities keyed by monster ID**, not per-copy UUIDs or per-copy skill levels. Stored `owned` level/skill/assist fields are not loaded into `Card`. [Evidence: `database/store.py:31–103`; `snapshots.py:16–30,69–79`; `coverage.py:56–64,114–143,321–332,345–374`.]

## 2. Monster ingestion and mechanics representation

### Source transformation

`FormationSource.load` reads public PADDashFormation NA files at pinned commit `6d8b0698208aebef17e4f4fefe6b3c616f8d0ed8` (project documentation dates the snapshot to 2026-10-30). It admits `enabled` and non-`isEmpty` cards; selected icons control recognition availability, not metadata import. `ckey` provides the upstream update timestamp. Provenance retains URL, version, download time and SHA-256. `SnapshotCache.fetch` validates cached hashes on its fetch path. Icon sheets use one-based 10×10 sheets, 100-pixel art and 2-pixel gutters; missing/blank icons do not exclude metadata. [Evidence: `sources/adapters.py:21–22,45–84,110–169`.]

`attrs`, `types`, awakenings, super awakenings, active/leader IDs and full raw card records enter `Monster`. `henshinTo` yields transform edges. A second pass reverses each child's `evoBaseId` into a parent→child evolution; a child with awakening 49 makes that edge `assist_evolution`. Only enabled loaded parents participate. No reverse/devolution edge is created. [Evidence: `sources/adapters.py:170–218`.]

`Store.load_monsters` transactionally upserts cards and associated normalized rows; it retains an existing icon only for the same source/version. It updates incoming records, rather than deleting all old monsters absent from a later snapshot. Reference refresh is isolated from inventory, but a mixed historical reference set is possible. Mechanics `_load` uses JSON `raw` and `skill.raw`, not the normalized attribute/type/skill-effect columns. [Evidence: `database/store.py:105–186`; `coverage.py:345–374`.]

### Evidence and unknowns

Source `Skill.evidence` permits `unknown`, `source_encoded`, `verified`, `heuristic`; the public adapter stores `source_encoded` with empty typed effects. Runtime `Effect.evidence` is a free string; accepted handwritten decoder branches construct `verified`, and unsupported/malformed shapes construct `unknown`. The two fields are different layers. Awakening `describe` emits `source_encoded` for known names and `unknown` otherwise; sync acquisition evidence remains unknown. [Evidence: `models.py:19–33`; `adapters.py:220–235`; `decode.py:98–169`; `awakenings.py:210–217`; `explain.py:226–234`.]

`decode_skill` recursively flattens only type 116 compound actives and type 138 compound leaders, preserving child order. It stops cycles, emits unknown components for missing skill IDs, and dispatches leaves through `_LEAVES`. It does not parse English descriptions. The “verified” label is justified in the code comment by repeated parameter/NA-description pairings, not by an attached per-effect verification dataset or an in-game test. Existing synthetic tests verify the decoder's expected layout outputs, not all real mechanics. [Evidence: `decode.py:1–6,107–169,1375–1425`; `tests/test_mechanics.py:25–152`.]

Unknowns are neither silently discarded nor generally exclusionary: they remain in effects; a form with some verified answers can enter the pool; shortlist prioritizes fully decoded cards; a role supplied only by unknown-bearing actives stays open. Conversely, an extra unknown card can coexist with `ok: true` if clean cards satisfy all required predicates. `check_team.unknown_effects` lists active unknowns only, not unknown leader clauses or unnamed awakenings. [Evidence: `team.py:224–285,544–670,801–813`; synthetic probe in trace.]

The skill index is reloaded from the local cached `skill_en.json` matching the leader's provenance version, or explicitly supplied by a caller. `_cached_skill_file` selects by cache sidecar URL and latest fetch timestamp; this decode read path does not recheck the sidecar's hash. Mixed-version cards are decoded against the leader's one chosen index. Missing cache leaves compound IDs unresolved even though root skills exist in SQLite. [Evidence: `explain.py:237–310`; `coverage.py:130–135`.]

### Mechanics support matrix

Status numbers requested by the audit: **1** = fully represented for the stated narrow/static operation; **2** = decoded/represented but partially used; **3** = raw/unstructured data only for unsupported layouts; **4** = unsupported/ignored by decision-making. No row implies full battle simulation. Different opcodes implementing the same English mechanic can have different support.

| Mechanic | Status and actual implementation | Consequences not modeled | Evidence |
| --- | --- | --- | --- |
| Base/evolution forms | **2**: directed graph, limited material checks, path/stage metadata | Devolution, full costs, levels and some special evolution encodings | `coverage.py:405–513`; `adapters.py:211–217` |
| Transformations | **2**: direct edges, entry/battle distinction; leader/helper minimum cooldown and SB threshold | Chosen sub transform timing/cost, chains beyond one transform step, state schedule | `coverage.py:405–465,640–707`; `team.py:495–541,629–649` |
| Assist evolution/equip | **2**: reachable alternative; awakening 49 required as equip; consumes copy | Ordinary non-equip skill inheritance, host compatibility, inherited active timeline | `team.py:418–443,472–492,834–872,934–935` |
| Attributes/types | **1** for storage; **2/4** for use: known main attribute gate; types unused for team compatibility | Subattribute damage, type restrictions, assist attribute/type modification | `models.py:36–50`; `coverage.py:345–374`; `team.py:956–969` |
| Active skills | **2** for recognized leaf layouts, **3/4** otherwise | Actual skill state/targets/availability and whole-effect interaction | `decode.py:107–169,1375–1425` |
| Leader skills | **2**: linked shield, fixed time, single awakening trigger, rank EXP and limited generic scalar layouts | General leader-condition expression evaluation | `decode.py:520–639,1118–1150`; `coverage.py:844–908` |
| Base/minimum cooldown | **1** for formula, **2** for use; max-skilled minimum assumed | Actual owned skill level, skill-stage transitions | `adapters.py:220–235`; `explain.py:237–264` |
| Skill Boost | **1** for static normal-awakening count: 21→+1, 56→+2, 105→−1; **2** for readiness | Seal/delay/dungeon effects, acquired awakening state, super/sync | `awakenings.py:171–223`; `team.py:629–649` |
| Super awakenings | **2**: display menu | Selection, unlock cost/conditions, contribution to SB/damage/resists | `team.py:212–213,342`; `coverage.py:1382–1412` |
| Sync awakenings | **2**: raw ID/conditions, known name, explicitly not assumed unlocked | Acquisition and effects on team | `explain.py:226–234`; `coverage.py:1382–1412` |
| Latents | **2/4**: 6/8 slot capacity displayed; no selection | Latent effects, capacity validation, delay resistance, latent SB | `team.py:211,342`; `coverage.py:1405` |
| Awoken Assist inheritance | **2**: equip's normal SB is added, equip active is counted | Combined awakening state except SB, stat bonuses, assist-removal restrictions, resonance/self-reliance interactions | `team.py:495–520,629–649`; `awakenings.py:205–207` |
| Haste / Skill Charge / delay | **2**: equal min/max haste and simple enemy delay decoded; haste providers displayed; **4** for awakening Skill Charge timing | Haste scheduling, enemy delay as stalling, enemy skill delay, skill-charge activation | `decode.py:272–283,684–700`; `coverage.py:599–603,640–707,1333–1378` |
| Shields/damage reduction | **2**: active shield percent/turns, linked leader shield, attribute guard details | Incoming damage, stacking, effective HP, shield loops, HP thresholds | `decode.py:569–591,651–667,1133–1150` |
| Orb creation/conversion | **2**: masks/counts/from/to/row/column/skyfall data; categorical color answer | Enough orbs, existing source orbs, locks, effect order, board geometry, consumption | `decode.py:432–517,807–843,1037–1072,1152–1175,1334–1364`; `coverage.py:950–998` |
| Cross / awakening conditions | **2**: trigger bit decoded; cross text and mono-board rejection | A legal cross arrangement, presence/use of Cross Attack awakening, other patterns' operational constraints | `decode.py:71–94,595–639`; `coverage.py:879–887`; `team.py:919–930` |
| Absorption/void | **2**: separate attribute/combo/damage flags and active void-pierce turns; Boolean role requirements | Enemy thresholds, duration adequacy, continuous uptime, per-target inherited availability | `decode.py:300–311,329–349`; `coverage.py:31–53` |
| Healing/RCV/HP | **2**: direct heal, regen, RCV buff, HP-cost and max-HP operands; **3/4** for unsupported leader HP/RCV clauses | Team stats, effective healing, HP sustainability, heart loss, HP-threshold activation | `decode.py:196–255,380–407,927–943,1104–1115,1272–1297` |
| Damage/cap/ATK buffs | **2**: operands decoded; awakening names stored | Actual damage estimation, awakening scaling, cap attainment, enemy matchup | `decode.py:352–423,768–804,852–923`; `team.py:963–969` |
| Hazards/resistance | **2**: limited awakening checklist and source counts; not named-team requirements | Actual combined resists including equips, hazard match constraints | `awakenings.py:171–235`; `coverage.py:1098–1137` |
| Evolving/looping skill stages | **3/4** for unsupported opcodes such as type 232 | Selecting or stepping skill stages | `decode.py:120,1375–1425`; runtime #8336 below |

The full current leaf dispatch includes types `0,3,6,9,10,11,15,18,19,20,21,37,50,71,88,90,93,117,126,127,128,132,140,141,142,146,148,154,160,161,172,173,178,179,182,184,190,191,195,196,202,207,230,237,241,244,258,259,271`. Each handler can reject parameter shapes. Raw descriptions remain in reference storage for unsupported meanings. [Evidence: `decode.py:1375–1425`; trace `decoder_inventory`.]

## 3. Exact candidate generation and filtering

1. `leader_context` requires `box_state`; it does not use draft scans or arbitrary legacy exports. `_owned` aggregates positive quantities by exact monster ID and counts explicitly unconfirmed quantities. It does not itself exclude `confirmed:false` rows. Normal snapshot application only produces confirmed rows. [Evidence: `coverage.py:114–143,321–332`; `snapshots.py:16–30`.]
2. Missing reference IDs are excluded from `known_owned` and reported by coverage. `Card`/facts are cached per session. Mechanics does not filter the `availability` table by NA; it relies on the imported reference set. [Evidence: `coverage.py:118–143,345–400`.]
3. `_reachable` starts with the exact owned form. It traverses only outgoing evolution/assist_evolution edges, limits expansion at path depth eight, and appends **one direct transform target** for every reachable entry. It does not recurse through transformation chains or reverse evolutions. [Evidence: `coverage.py:419–465`.]
4. `_material_cost` requires every named nonzero material to have a reference record. Stackable materials cost nothing, even if not owned. Non-stackables must exist in sufficient quantity for that edge and are added to `spends`. A missing special material ID can block a path. For example, the raw Ordeal Venus recipe includes 65535, so generic recipe processing is not a full ordeal model. [Evidence: `coverage.py:468–485`; trace #10756 facts/raw inspection.]
5. One path is retained per entry form, preferring fewer **material copies**; equal-length spend lists do not replace the first path, regardless of material identity or later allocation conflicts. Cumulative spend feasibility is not checked during every path expansion; leader reservation and final `_copy_problems` check aggregate spending later. A potentially usable alternative path can be lost before validation. [Evidence: `coverage.py:425–444,488–513`; `team.py:472–492`.]
6. `_identity` resolves a named combat leader to its direct transform parent(s), or a named base to its direct combat targets. `_reserve` chooses the payable entry route by `(number of spent copies, owned_id != entry_id, owned_id, entry_id)`; this is a local deterministic resource choice, not team optimization. One source copy and materials are reserved. [Evidence: `coverage.py:137–168,405–416,488–507`.]
7. `_form_row` attaches normal SB, full/unknown active effects, source ID, stage/path/materials, equip flag, main attribute, `can_damage`, menus and latent capacity. `availability="in_box"` means no evolution/assist_evolution in the path, **even for an unperformed transformation**. [Evidence: `team.py:178–221`.]
8. `_answers` contains any of the seven verified active roles, relevant linked-color board/orb answers, haste and positive SB. For cross leaders, mono-color boards and only-that-color skyfall do not provide `*_orbs`. Answers do not inspect cooldown or orb count. [Evidence: `team.py:224–255`; `coverage.py:950–998`.]
9. `_candidates` retains a whole owned-ID group if it is the reserved leader source, or **any** of its forms is relevant. If the leader/helper transforms, positive SB alone is relevant; otherwise SB-only groups are omitted. Once retained, **all** the group's reachable forms remain, including irrelevant forms, transforms and equips. No pool-wide attribute/type gate or numeric size limit applies. [Evidence: `team.py:128–175,251–255,314–322`.]
10. `shortlist_box` flattens forms; excludes borrowed rows and `can_damage:false` rows; keeps matching bucket answers; sorts using `_bucket_order`; returns all pinned leader-group forms plus enough other forms to target eight per bucket. It does not remove an equip from sub candidates merely for being an equip. No type, stats, dungeon, or global role budget applies. [Evidence: `team.py:61–104,746–813,963–969`.]

The damage gate uses only known **main** attribute and nonempty normal awakenings. If required colors, attributes, or a numeric main attribute are missing, it returns true. Any nonempty awakening list satisfies the awakening part; no damaging awakening is necessary. This is a heuristic rejection rule, not PAD damage computation or general PAD team legality. [Evidence: `team.py:956–969`.]

Duplicates are quantities under an owned monster ID. Every form under that ID is an alternative consuming one copy; two distinct owned IDs can reach the same form. They can both appear and occupy separate shortlist positions. Final validation sums leader reservation, subs, equips and non-stackable evolution materials by source ID and compares to inventory counts. Thus one copy used as both sub and assist is caught **when a named team is checked**. Pool/shortlist suggestions themselves do not allocate globally. [Evidence: `team.py:165–175,472–492,746–758,853–857`.]

**Can a strategically best candidate be discarded?** Yes, even though “best” is undefined here: irrelevant-group filtering, main-attribute gating, minimum-spend path retention, eight-row truncation, and four-assist truncation can remove a candidate useful under a richer model. Apollo is an actual example of a two-turn generator excluded by tuple ordering, not a demonstrated loss under an implemented team objective.

## 4. Team assembly, slot counts and legality

`check_team(store, leader_id, team, skill_index=None)` accepts `helper_id`, optional `leader_assist`/`helper_assist`, and a `subs` list. Each sub/equip names integer `owned_id` and `form_id`. Booleans and non-integers are rejected; additional JSON keys are not an optimizer specification. Leader selection is the separate function argument and its source reservation is automatic. [Evidence: `team.py:107–125,355–361,695–734`.]

The decisive code is:

```python
sub_slots = 4 if session.helper else 5
if len(subs) > sub_slots:
    problems.append(...)
...
"ok": not problems and not open_holes
```

[Evidence: `team.py:372–374,405–415`; duplicated in requirements at `team.py:293–310`, coverage at `coverage.py:235–249`.]

This explains five subs with helper `null`: **an explicit internal slot-count convention**, not a serialization error. It allocates all five non-leader positions to subs when helper is absent. There is no separate `partial_team` mode flag or message exempting that result from standard PAD structure. It never requires exactly four subs, never requires a helper, and with a helper only checks `len(subs) <= 4`. A synthetic five-sub team with no helper and a one-sub team both returned `ok:true`. The existing test `test_check_accepts_a_team_that_closes_the_holes` itself expects success with one sub. [Evidence: `tests/test_team.py:215–227`; trace `synthetic_audit_probes`.]

Hard checks actually enforced:

- At most four/five subs according to that convention; a reachable owned leader reservation.
- Source ownership and target reachability for each supplied sub/equip.
- Awakening 49 on an equip; helper assist requires a helper.
- Aggregate copy/material demand, including the reserved leader source.
- Main-attribute/nonempty-awakening heuristic for subs and the helper **entry** card; assists exempt.
- At least one fully decoded active source per required role/color; known leader/helper transform SB threshold.

[Evidence: `team.py:364–649`.]

It does not check exact six-card completeness, general PAD assist host eligibility, general non-equip inheritance, actual evolution resources, acquired awakenings, actual skill levels, sub transform readiness, dungeon restrictions, helper access/friend availability, team cost, leader type/stat restrictions, health, damage, or a playable rotation. It permits equips as subs if otherwise accepted. A helper is caller-supplied and borrowed, requiring only a reference entry, not ownership. `_resolve` checks **all reachable forms**, not membership in the displayed pool/shortlist, despite the pool contract telling a consumer to choose listed forms. [Evidence: `team.py:325–351,418–443`; `coverage.py:128–129,545–567`.]

**Conclusion:** the checker combines internal data consistency with a small strategic checklist and a non-game-rule color heuristic. It is not a complete PAD legality checker.

## 5. Exhaustive quality/ranking model

There is no complete team score formula to decompose. The full executable ranking keys, every requested category's treatment, exact SB/cooldown/uptime equations, saturation, caps and interactions are documented in [team-builder-scoring.md](team-builder-scoring.md), sections 2–6.

The central preferences, in priority order, are: reserve/pin leader-group forms; prefer no unknown active effects; prefer no required evolution; prefer fewer consumed non-stackable copies; then prefer cooldown or SB according to the bucket; finally IDs break ties. Assist suggestions prefer no unknowns, more answers absent from the host, and more SB before evolution and cooldown. There are no strategic score weights or numeric role rewards. [Evidence: `team.py:801–813,834–872`.]

Card lists are ranked independently. Named-team role closure and copy demand are evaluated jointly, but without a joint objective. There is no redundancy penalty for multiple clerics/void actives, healing sufficiency metric, fixed-time adjustment, extra-damage estimate, or sustained-generator synergy. Coverage saturates at existence; extra sources do not improve `ok`. Assist answer difference uses only the host's answers, so a cleric assist can rank well even if the leader already fills all relevant cleric predicates. [Evidence: `team.py:544–626,841–848`.]

## 6. Time, initial state and rotations

| Question | Actual answer |
| --- | --- |
| Simulate cooldown progression or first skill states? | No; max-skilled minimum cooldown and static SB only |
| Model transformation timing? | Leader/helper entry threshold only; transform base/combat actives simultaneously enter coverage; sub cost not required |
| Model haste? | Decode/list; does not reduce the skill's own cost and is not scheduled into readiness |
| Assist overrides base active? | No; both actives count as independent providers |
| Availability across turns / effect overlap / shield loops? | No |
| Distinguish two-turn vs ten-turn generator? | Yes in shortlist order; not in named-team color existence check |
| Orb supply/consumption / healing-orb loss? | No |
| Activation probability / board simulation? | No |
| Five linked vs plus-shaped cross? | Typed distinction and mono-board rejection; no geometric board validation |

[Evidence: `explain.py:237–264`; `coverage.py:640–707,808–830,950–1084`; `team.py:495–649,801–813,919–930`.]

There is one restricted temporal **approximation**: ordinary-link `cover` estimates mono-board supply by summing `copies/cooldown`, greedily filling remaining slots; `fits` means this sum reaches approximately one. It excludes battle-stage subs and material-spending forms, does not schedule actions or jointly budget several color conditions, and is not used as a loop requirement in `check_team`. For cross leaders, `_colors` deliberately skips that calculation (`loop:null`) and uses provider existence. Therefore the current Rozé analysis demonstrates **no continuous orb-generation rotation**. [Evidence: `coverage.py:808–830,1001–1084`; `team.py:559–626`.]

Durations appear in decoded facts but are not used to show cleric availability, a damage-void loop, or a shield loop. The validator can use Rozé's one-time base bind clear to close a permanent bind-clear requirement even though the combat active lacks bind clear. This is an explicit consequence of `_chosen_actives`, not an inferred in-game rotation. [Evidence: `team.py:495–520`; actual #13966/#13967 effects in trace.]

## 7. Leader-specific reasoning, including Rozé #13967

### General leader semantics

The decoder structurally represents specific linked-attribute/count/shield clauses, fixed move time, and single-known-bit awakening-trigger bonuses. Those trigger details can include ATK, RCV, shield, fixed damage, and added combos. Limited attribute multipliers/guards are also decoded. It is not a general leader-skill grammar: required monster types, rainbow conditions, combo thresholds, HP thresholds, arbitrary rows/L/T patterns, and unsupported stat clauses are not converted into operational team constraints. Known trigger names beyond cross remain descriptive. [Evidence: `decode.py:71–94,520–639,1118–1150`; `coverage.py:844–908`.]

`_linked` makes one requirement per decoded `linked_orb_leader` effect. `_match_shape` returns `cross` if **any** awakening trigger says cross, then attaches that shape to every linked requirement for that form. Thus separate clauses remain separate at decode time but get collapsed into a shared shape label downstream. Other trigger bonuses do not enter damage/survival/combo math. Fixed time is displayed and extracted into roles, not used by ranking. A helper adds its own linked conditions and SB/cooldown data; there is no leader-pair compatibility objective or multiplication of shields/ATK/RCV. [Evidence: `coverage.py:777–908`; `explain.py:176–181`; `team.py:293–310,446–456`.]

### Exact Rozé code path

1. Applied inventory has **one #13966**, no directly owned #13967. The reference has a `transform` relationship #13966→#13967. `_identity(13967)` finds incoming transform parent #13966; `_reachable(13966)` lists the entry, its battle target #13967, and reachable equip #13968; `_reserve` reserves #13966. No ID alias is hardcoded. [Evidence: `coverage.py:137–168,405–465,488–507`; trace `leader_resolution`.]
2. Entry active **#64124**, “This...is my resolve.”, is compound 116. It decodes full unmatchable/bind/awoken-bind clears, two-turn haste, and transformation into #13967. `initialCooldown=28`, `maxLevel=5` gives **24**; entry has two SB. `_transform_card` requires the decoded target to match the combat ID before using its cooldown. Own haste does not turn that into 22. [Evidence: `explain.py:252–256`; `coverage.py:693–707`; trace `roze_explain`.]
3. Combat leader **#64136** is compound 138, flattening:

| Child/type/params | Decoded fact | Actual downstream use |
| --- | --- | --- |
| #64137 /178 /`[12]` | fixed **12 seconds** | Display only; no move-time ranking adjustment |
| #64138 /182 /`[8,5,100,83]` | **83%** shield for **5+ linked Light**, ATK hundredths 100 | Adds Light/count requirement; no survival calculation |
| #64139 /271 /`[8,20000,0,4,17000000]` | Cross Attack trigger; **200× ATK**, **+4 combos**, **17,000,000 fixed damage** | Sets global `match="cross"`; bonuses not evaluated |

[Evidence: `decode.py:520–639`; trace actual decoded skill IDs/parameters.]

4. The decoder distinguishes the shield's “five or more linked Light” condition from the Cross Attack trigger. The coverage layer sees both and labels the Light requirement `cross`. It excludes mono-Light boards as cross providers, but it does not independently evaluate shield-only activation vs cross bonus activation. [Evidence: `coverage.py:844–908,961–998`; `tests/test_mechanics.py:89–106`.]
5. The text contract says a cross is exactly five orbs in a plus; a mono-board cannot be that cross. **No board positions, matching search, adjacency, supply count, or Cross Attack awakening use is checked.** Any qualifying Light conversion, row/column creation, mixed board or non-mono skyfall can satisfy the cross-provider predicate, even if it cannot guarantee five movable Light orbs. [Evidence: `team.py:919–930`; `coverage.py:961–998`.]
6. Combat active **#64131**, “Fortress Mode”, is four turns at minimum and decodes: full unmatchable and awoken-bind clear, four-turn damage-void pierce, four-turn 6×5 board, four-turn 2× ATK to all allies. No Light generation is present. Its values are facts; no simulation proves permanent coverage. [Evidence: trace `roze_explain`; `team.py:495–520`.]
7. Light generators receive no score. Clean ones are sorted by minimum cooldown after reservation/unknown/investment preferences. Actual top Light bucket includes Saphira #7096 at position 5 and Pixel Lightning #3821 at 6; Apollo #7844 is rank 124 because an unrelated attack-target effect is unknown. [Evidence: trace `eligible_bucket_orders.light_orbs`; `team.py:801–813`.]
8. Rozé's `loop` is `null`. No alternating generator rotation is assessed. Duplicate clerics are neither penalized nor suppressed by leader coverage. The fixed 12-second time neither benefits nor discounts timed move buffs because move buffs never enter these ranks. [Evidence: `coverage.py:808–830`; `team.py:224–248,761–813`.]

## 8. Search, optimality and determinism

**Team search:** absent. No exhaustive combinations, beam width, search depth over team slots, genetic algorithm, integer programming, randomness, team-score cache or best-team tie exists. Subs and assists are not jointly or sequentially **optimized**; sub rows are independently sorted, and each displayed row independently receives four equip suggestions. The latter depend on host answers only. [Evidence: `team.py:61–104,834–872`.]

Separate implemented algorithms:

- Directed reachability uses a deque, maximum evolution expansion depth eight, and fewer-spend-copy path replacement. Direct transform children are appended. Equal-cost path ties retain first traversal order; SQL graph loading has no explicit ordering. [Evidence: `coverage.py:335–342,419–465`.]
- Leader reservation uses a local tuple minimum; available-copy count greedily consumes payable routes, without solving a global resource optimization. [Evidence: `coverage.py:488–542`.]
- SB ceiling greedily takes strongest entry-stage forms per owned ID; ordinary-link uptime greedily takes fastest mono-board makers. Both have explicit narrow purposes, not an overall team objective. [Evidence: `coverage.py:710–774,1001–1084`.]
- Bucket and equip tuple sorts are deterministic for the same resulting rows and input order, with numeric IDs as late tie-breakers. No random sampling. Changing reference insertion/path tie order can affect retained reachability paths and therefore later output. [Evidence: `team.py:801–813,860–872`; `coverage.py:440–444`.]

For `F` retained form rows, `B` requirement buckets, `K` displayed rows, `N` owned-ID groups and `E` graph edges: flattening is `O(F)`; bucket filtering/sorting at most `O(B F log F)`; equip scanning/sorting is at most `O(K F log F)`; caches hold loaded cards and decoded facts for one context. Reachability starts anew per owned ID; bounded path replacement can revisit vertices, so it is not one global graph traversal. Decoder work is additional; `_skill_report` copies the skill index for each active/leader report. These are source-based complexity bounds, not benchmark claims. [Evidence: `coverage.py:118–143,385–400,419–465`; `explain.py:247`; `team.py:68–85,834–850`.]

Actual current run: **686** known owned IDs, **1,735** reachable form rows before group relevance, including **35** battle rows. Pool retains **523 groups / 1,562 form rows**, omits **163 groups**, and records **461** unknown-bearing reachable active/source combinations. `candidate_copies` and `omitted_copies` are misleading names: they count **owned-ID groups**, not quantities. There are **645** eligible bucket rows across nine buckets; each ordinary bucket targets eight, with leader pinning exceptions. These counts are not a combinatorial search-space enumeration. [Evidence: trace; `team.py:54–57,320–322,794–798`.]

No implemented highest-team-score guarantee exists. Even under an imagined sum of per-row preferences, independent truncation and resource conflicts would not establish global optimality.

## 9. Assist eligibility, assignment and consequences

Equips are owned forms or reachable assist evolutions with normal awakening **49**. The source copy and consumed non-stackable materials count against final ownership. Borrowed helper forms are not proposed as owned equips. Only the host/source local spare-copy rule is used during suggestion; another host can receive the same one-copy equip suggestion. Final `_copy_problems` catches spending it twice in a supplied team. No evolution affordability score or cost tradeoff exists. [Evidence: `team.py:178–221,418–443,472–492,834–857,934–935`.]

`_assist_order` counts new answer labels relative to the host, then SB; it never compares the equip's effect to the selected leader/team. All normal equip SB values enter `_boost_report`. Other inherited awakenings, type/subattribute changes, same-attribute stat inheritance, assist activation/disable conditions and acquired awakening levels are not combined into a selected loadout. `canAssist` appears in explain output but is not the equip eligibility gate. Generic non-equip inheritable skills are excluded by the mandatory awakening-49 rule. [Evidence: `explain.py:214`; `team.py:629–649,834–872`; `awakenings.py:12–207`.]

The checker adds both base and inherited actives to the list of possible providers, using each effect with no effective inherited cooldown (`base + assist`) calculation, readiness model, or overcharge state. Therefore it can accept an equip combination that makes a required base skill unavailable at the desired time; the code cannot assess that risk. This is a **missing temporal model**, not proof that a particular suggested equip will overcharge in a particular dungeon. [Evidence: `team.py:495–520,599–626`.]

Supplying assists changes named-team copy demand, investments, active unknowns, role/color closure and SB. It never recalculates a team quality score. The shortlist does not assign assists or a helper, and the validator does not require the equip to have appeared on the host's displayed four-item list. [Evidence: `team.py:364–443,472–649,673–691`.]

## 10. Output contracts and confidence

### `check_team` output: every field

| Field | Guarantee / computation | Does not guarantee |
| --- | --- | --- |
| `decoder_version` | Current decoder string, `"5"` | Reference freshness or mechanic correctness |
| `leader_id` | Caller-named leader ID | Direct ownership of that exact combat form; source may be base/evolution |
| `helper_id` | Caller-named borrowed helper, or null | Chosen optimal helper, availability, or completeness when null |
| `ok` | Exactly `not problems and not open_holes` | Standard six-card legality, uncertainty-free analysis, temporal feasibility, coherence or dungeon performance |
| `problems` | Slot upper bound, ownership/reachability/equip/copy/main-attribute checks | All PAD rules or strategic shortcomings |
| `open_holes` | Seven active-role predicates, color-provider check, and known leader/helper SB shortfall | Complete mechanic coverage for a chosen dungeon, duration/rotation feasibility, hazards or survival |
| `unknown_effects` | Non-verified active effects for chosen sources, with where/form/effects | Unknown leader conditions, unnamed awakening effects, all unsupported mechanics; need not be empty for `ok:true` |
| `investments` | Chosen **sub/equip** routes containing evolution/assist_evolution, with source/form/path/spends | Leader evolution investment (omitted here), full material costs, transform cost, SA/sync/latent costs |
| `skill_boost.total` | Entry leader/helper/sub normal SB + selected equips' normal SB | Actual unlocked loadout or applicable dungeon readiness |
| `skill_boost.required` | Maximum decoded leader/helper transform minimum; null when none or unresolved | Highest chosen-sub requirement; guaranteed verified transform when null |
| `skill_boost.meets` | True if required null; otherwise owned leader reserved and total≥required | All transformations usable at entry |
| `skill_boost.badge` | Literal `"excluded"` | Badge allowance or in-game SB total with a badge |

[Evidence: `team.py:405–415,629–691`.]

**There is no `slots` field in current `check_team` output and no selected-team serialization.** `pool.requirements.sub_slots`, `cover.transform.ceiling.sub_slots`, and `box.capture.slots` are different fields: allowance, heuristic ceiling allowance, and scanned physical slot count respectively. A previous `slots` array must have come from a caller/wrapper or another unavailable version; its exact semantics cannot be recovered. [Evidence: `team.py:293–310,355–361,405–415`; `coverage.py:762–774`; `snapshots.py:272–278`.]

### Other command outputs

`explain` returns named kit forms, provenance-selected skill-index status, decoded active/leader facts, awakening menus, outgoing edges and role facts. `cover` adds box metadata, reserved entry/combat leader, borrowed helper, transforms/ceiling, provider status/counts, linked-color requirements/providers, owned-form awakening counts, haste providers, holes, notes and loadout menus. Its `turn1_haste` list is gathered from reachable active effects without a turn-one readiness proof; the name does not imply that those skills can be cast at entry. `_finish` marks a role `available` if any provider has no unknown active effects, `partial` if all do, otherwise `hole`; displays only the first **30** providers but reports truncation. [Evidence: `explain.py:16–80,197–234`; `coverage.py:278–318,599–603,1229–1255`.]

`pool` adds requirements, alternative forms grouped by source ID, counts, explanatory contracts and input JSON shape. `shortlist` emits independent requirement buckets, `also_fights_for` labels, up to four per-host assist options, limits and contracts. It has no selected team, score or `ok`. Input contracts are guidance text; not all are enforced, as the unrestricted reachability check shows. [Evidence: `team.py:41–104,325–361,816–916`.]

The implementation does not separately report “legally constructible,” “SB sufficient,” “mechanically complete,” “strategically coherent,” and “likely successful.” `problems`, `open_holes`, and SB partially separate the first three, but `ok` conflates its narrow checks. Unknown or incomplete leader decoding can fail open: `cover` reports an unresolved transform, while `check_team` sees `required=None`, `meets:true`, and can return `ok:true`. [Evidence: `coverage.py:667–669,1281–1287`; `team.py:544–649`; trace synthetic probe.]

## 11. Reconstructing the previous Rozé output

### Available inputs and missing historical evidence

The current applied box is revision **2**, with **686 IDs / 832 copies**, `complete:true`, no unconfirmed quantities. Its capture metadata says 831 physical slots; this is not a claim that slot count must equal quantities. History preserves revision 1 with 685 IDs / 831 copies; revision 2 adds one **#11399 Rolo & Vincent**. The reconstruction also checks the earlier stored document with current code/reference; neither document is proven to be the earlier recommendation's input. Revision 1 produces 522 candidate groups/1,560 forms and the **same no-assist 20/24 failure**, with Rathios moving from SB rank 4 to 3 and no Light-bucket change. [Evidence: trace `inputs`, `inventory_history`, `historical_revision_1_reconstruction`.]

A local search of source/data filenames and JSON/log content found no earlier saved team, assist mapping, score trace or optimizer implementation. The user supplied monster IDs and 29/24 SB, but no `owned_id` routes, equips, reference/decoder version or run timestamp. The report therefore does not invent historical selection reasons or match 29 by choosing arbitrary assists.

The executable reconstruction is:

```json
{
  "helper_id": null,
  "leader_assist": null,
  "helper_assist": null,
  "subs": [
    {"owned_id": 4834, "form_id": 4834, "assist": null},
    {"owned_id": 4206, "form_id": 4206, "assist": null},
    {"owned_id": 3939, "form_id": 3939, "assist": null},
    {"owned_id": 3821, "form_id": 3821, "assist": null},
    {"owned_id": 7712, "form_id": 7712, "assist": null}
  ]
}
```

Leader argument: `13967`. Actual output: `problems:[]`, `unknown_effects:[]`, `investments:[]`, SB **20/24**, `ok:false`. Open holes:

```text
No chosen card has a verified combo absorb void.
Chosen skill boost is 20, short of the 24-turn transform.
```

Static SB is **2 + 2 + 2 + 6 + 4 + 4 = 20**, counting entry Rozé first. Super/sync/latent/badge contributions are excluded. The historical 29 requires information absent from the supplied lineup or a different implementation/data state; **the nine-point difference cannot be assigned to actual assists from available evidence**. Even 29 SB alone would not close the current combo-absorb hole. [Evidence: trace `reconstructed_named_team`; `team.py:544–649`.]

### What each selected card actually contributes

Positions below are current independent bucket positions for the directly owned form; they are **not a team selection explanation**. The code does not establish why the five-card set outranked alternatives.

| Named sub | Real facts used / displayed | Actual shortlist evidence and limits |
| --- | --- | --- |
| #4834 Valkyrie Ciel | CD5; SB2; full bind/awoken/unmatchable clear, full heal, unlock, heal/hazard→Light conversion | Bind rank2, awoken/unmatchable rank8. Light rank13, pruned. Heal magnitude has no rank weight; removing it does not reopen any role because leader entry/combat already cover them |
| #4206 Sweet Dianthus Kushinadahime | CD9; SB2; 75% two-turn shield, +8 combos two turns, attribute/damage absorption void three turns | Both absorb buckets rank5. Only source of those two roles in no-assist baseline; shield/combo strength does not rank it |
| #3939 Rathios | CD9; SB6; 50% shield three turns, 2× sub ATK and 18-billion sub cap three turns, Light left column/Fire right column | SB rank4; Light rank28, pruned. Removing it loses six SB but no unique required role/color |
| #3821 Pixel Lightning | CD3; SB4; unlock, bottom Light row, +2 combos one turn | SB rank5, Light rank6; no rotation metric. Removing it loses four SB but Light still has other providers |
| #7712 Leona | CD5; SB4; 2× RCV one turn, full awoken/unmatchable clear | Awoken/unmatchable rank7, SB rank7. RCV buff has no rank weight; removing it reopens no role |

[Evidence: trace `reconstructed_named_team.selected_form_facts`, `eligible_bucket_orders`, `leave_one_out`; `team.py:801–813`.]

Removing Ciel, Kushinadahime, Rathios, Pixel Lightning, and Leona respectively gives SB **18,18,14,16,16**. Only removing Kushinadahime opens additional required utility holes (attribute and damage absorb). These results demonstrate checklist redundancy, not comparative damage or healing quality. Rozé's original bind clear is counted as available even after choosing its combat form.

### Requested score decomposition

| Actual model output/category | Value |
| --- | --- |
| Scalar leader compatibility / orb generation / defense / healing / utility / damage / assists / penalties / total | **Unavailable: no such score exists** |
| Static Skill Boost total / known requirement | **20 / 24** |
| Closed active role predicates | **6 / 7** |
| Cross-color provider existence | Yes |
| Sustained Light supply, cross arrangement, helper compatibility | Not evaluated |
| Current named-team `ok` | **false** |

No fabricated numeric decomposition is substituted for an absent objective. The full local ranking formula is recoverable and supplied in the scoring report.

## 12. Counterfactuals and controlled swaps

### Eligibility, pruning and actual ordering

The requested “Ultraman Gaia (V2) #8736” is a **name/ID mismatch** in this reference: #8736 is a Wood equip, *Feathers of Fierce Wings*, with no owned/reachable source here. The actual named Ultraman is **#8336**, owned once; its evolved #8337 is also reachable. Both interpretations were audited, without silently replacing the input ID. [Evidence: trace `name_id_disambiguation`, `counterfactuals`.]

| Candidate | Ownership/reachability and pool | Actual individual order/pruning | Effect recognized; no scalar score |
| --- | --- | --- | --- |
| Literal #8736 | Not owned or reachable; absent | No bucket position | Different card than requested name; no controlled owned swap |
| Actual Gaia #8336 | Owned1; in pool through SB4 | SB rank193, pruned | Active type232 entirely unknown; base minimum10, no decoded generator stage |
| Gaia #8337 | Reachable from #8336 by evolution; pool | Attribute absorb rank10 and SB rank74, both pruned | CD4, SB4; no skyfall, board refresh, attribute absorb2; evolution preference precedes cooldown |
| Apollo #7844 | Owned2; pool via #7844, #573, #2750 | Direct form Light rank124/SB200; evolved routes Light132/133, SB233/234; all pruned | CD2, SB2; dark/heal→Light, Light buff/skyfall decoded; type230 target1 remains unknown and dominates the rank |
| Ordeal Venus #10756 | Owned1; in pool | Awoken/unmatchable rank6 (retained), SB rank30 (pruned) | CD4/SB2; heal creation4, 2×RCV4, orb enhancement, cleric effects; no Light-generation answer |
| Paladin #7949 | Owned #7948 Crusader transforms to #7949; pool | Attribute/damage absorb and void-pierce rank2 (retained) | CD2, battle SB0; entry SB2 used for validation; half-HP cost decoded but not penalized |
| Saphira #7095 | Owned1; pool | Light rank38/SB16, pruned | One-time 28-turn entry generator/transform; not a recurring two-turn form |
| Saphira #7096 | Battle target of owned #7095; pool | Light rank5 (retained) | CD2, battle SB0, entry SB3; healing, Light ATK, cap and conversions decoded; transform cost28 not added to required24 |

[Evidence: trace exact `counterfactuals[*].bucket_decisions`, active facts and sources.]

Apollo demonstrates that **successful decoding of its useful orb effect is not enough to survive ranking**. Its unknown sibling effect causes every clean unreserved row to rank ahead, independently of cooldown. Venus, Paladin and Saphira's combat form survive pruning, so the current code provides no reason to say they lost to the named team. No full-team comparison was ever made.

### Controlled swap results

All swaps keep the named baseline's other four entries, helper null, and no assists. The trace records **45** actual validation calls: every available source route for Apollo and each other reachable interpretation, replacing each of the five subs. The table shows direct Apollo and one route per other form; Apollo's other two routes have the same SB changes and report evolution investments.

| Replacement (minimum CD; entry SB) | Replace Ciel | Replace Kushina | Replace Rathios | Replace Pixel Lightning | Replace Leona |
| --- | --- | --- | --- | --- | --- |
| Gaia #8336 (10;4) | 22 (+2) | 22 (+2) | 18 (−2) | 20 (0) | 20 (0) |
| Gaia #8337 (4;4) | 22 (+2) | 22 (+2) | 18 (−2) | 20 (0) | 20 (0) |
| Apollo #7844 (2;2) | 20 (0) | 20 (0) | 16 (−4) | 18 (−2) | 18 (−2) |
| Venus #10756 (4;2) | 20 (0) | 20 (0) | 16 (−4) | 18 (−2) | 18 (−2) |
| Paladin #7949 (2;2 entry) | 20 (0) | 20 (0) | 16 (−4) | 18 (−2) | 18 (−2) |
| Saphira #7095 or #7096 (28/2;3 entry) | 21 (+1) | 21 (+1) | 17 (−3) | 19 (−1) | 19 (−1) |

Cells are **SB total (delta from 20)**, not scores. Every swap has `problems:[]`, still misses combo-absorb void, still has fewer than24 SB, and returns `ok:false`. Replacing Kushinadahime also opens both attribute/damage absorb except Paladin closes both, while evolved Gaia closes only attribute absorb. Apollo and base Gaia add unknown effects; unknowns do not create extra holes while another clean generator exists. [Evidence: trace `controlled_swaps`.]

No swap compares damage, HP cost, RCV, or orb rotation because those comparisons are absent. Alternating short-CD generators get individual cooldown ordering only; no bonus or feasibility proof recognizes the pair's rotation. A score delta cannot be computed.

## 13. Existing tests and audit verification

Executed safely with installed dependencies:

```sh
.venv/bin/python -B -m pytest -q -p no:cacheprovider
```

**Result: 217 passed in 73.08 seconds; no skips or failures.** Relevant mechanics files collect **40 tests**: 11 in `test_team.py`, 15 in `test_mechanics.py`, 14 in `test_coverage.py`. Full suite includes source/database, snapshot/copy identity, scan recognition/tracking, Streamlit and headless-browser tests. Passing synthetic fixtures do not establish real box recognition accuracy or strategic correctness. No dependencies, browser binaries, or firmware were installed; no real scan was applied.

| Requested coverage | Existing evidence | Missing assurance |
| --- | --- | --- |
| Team slot legality | One-sub success deliberately asserted: `test_team.py:215–227` | Exact four subs + helper requirement; current wrong convention is not caught |
| Ownership/duplicate limits | `test_check_reports_copy_conflicts_and_unreachable_forms`, `test_team.py:339–374`; snapshot tests | Global allocation of independent suggestions; per-copy acquired states |
| Evolution paths/costs | `test_pool_keeps_the_owned_form_beside_the_reachable_one`, `test_team.py:155–207`; `test_coverage.py:474–528` | Alternative material-path feasibility, cumulative spending, special recipes, devolution/depth limits |
| Transformations | `test_mechanics.py:25–70`; `test_coverage.py:134–174,252–274` | Chosen sub costs, multistage paths, missing transform fail-closed behavior |
| SB accounting | `test_coverage.py:213–274`; `test_team.py:513–561` | Actual awakening unlocks/skill levels, assist conditional effects and dungeon start states |
| Cooldowns | Fixture 28/5→24 checked through coverage (`test_coverage.py:140`); shortcode ordering tests | Actual owned skill level, evolving skills, inherited cooldown progression |
| Assist inheritance | Equip flag/copy conflict checks `test_team.py:339–374,420–467,513–561` | Active override, combined cooldown, inherited attribute/type/stats, combined non-SB awakenings |
| Leader activation | Separate fixed time/link/cross decoder `test_mechanics.py:89–106`; mono-board vs column `test_coverage.py:373–435`, `test_team.py:230–259` | Actual shape/orb-count probability, Cross Attack awakening, independent-clause evaluation, pair compatibility |
| Redundant utility | No strategic test | No redundancy metric exists; no assertion that leader-covered roles stop consuming shortlist capacity |
| Skill rotations | Mono-board reciprocal-CD heuristic `test_coverage.py:277–318` | Real turn schedules, alternating cross generators, shields, downtime and overcharge |
| Unknown effects | `test_mechanics.py:123–134,152–157`; `test_coverage.py:464–471`; `test_team.py:377–417` | Unknown leader/transform blocking; unrelated unknowns nonfatal behavior; mixed-version index consistency |

Additional **diagnostic probes**, without changing tests, reused fixture helpers in temporary synthetic stores. They reproduced: five subs/no helper accepted; one sub accepted; unrelated unknown active accepted; a 99-turn chosen sub transform accepted with required null and zero SB; and a leader with a transform relationship but no decoded transform skill accepted with required null. These are in `synthetic_audit_probes`, clearly separate from existing test results.

Most important missing regression cases, **not implemented in this audit**: exact team shape; unresolved transforms/unknown leader clauses; all chosen-sub transform requirements; one-time entry skill vs recurring role; actual inherited active state; short-CD generator rotations; independent link/cross conditions and minimum orb counts; alternative evolution resources; reserved-only equips consuming shortlist capacity; ordinary-link board assist-key mismatch. These follow directly from the issues register.

## 14. Findings and architectural risks

The prioritized register provides classification, severity, evidence, affected example, impact and verification state for each finding. The highest-impact conclusions are:

1. **Confirmed structure bug relative to a standard PAD team:** explicit five-sub/no-helper convention and no exact completion check; `ok:true` is not standard team legality.
2. **Temporal modeling gaps/fail-open validation:** required transformations omit subs; unresolved leader/helper cooldown maps to a passing null requirement; entry/combat/assist actives are all treated as available. A passed checklist does not prove its key skills are usable.
3. **Ranking approximation and pruning:** tuple preferences can discard a two-turn orb maker because of an unrelated unknown effect, reserve-only alternate equips consume scarce list positions, and no joint objective considers rotation, redundancy, damage or survival.

The absence of a team optimizer is an explicit scope boundary, not an undisclosed optimizer bug. The previous claim of “optimal” cannot be justified by these functions. Evidence supports what the lists prefer and what the checker accepts; it cannot establish why an outside process selected the reported lineup.

## 15. Trace methodology, reproduction and remaining limits

The audit called existing `pool_box`, `shortlist_box`, `check_team`, `leader_context`, and ranking/check helper functions through a tiny read-only store adapter. It opens databases with SQLite URI `mode=ro` and `PRAGMA query_only=ON`, bypassing `Store.__init__`, WAL settings, migrations and CLI log writes to the real data home. Temporary synthetic stores and an in-memory historical document were used for probes. Raw observations, review state and applied ownership were never updated.

Minimal reproduction of the named baseline against installed local data, without constructing `Store`:

```python
import sqlite3
from pathlib import Path
from padbox.mechanics.team import check_team, pool_box, shortlist_box

class ReadOnlyStore:
    home = Path(".padbox").resolve()
    def connect(self, name="inventory"):
        db = sqlite3.connect(f"file:{self.home / (name + '.sqlite')}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db

store = ReadOnlyStore()
named = {"helper_id": None, "subs": [
    {"owned_id": i, "form_id": i, "assist": None}
    for i in (4834, 4206, 3939, 3821, 7712)
]}
pool = pool_box(store, 13967)
shortlist = shortlist_box(store, 13967)
result = check_team(store, 13967, named)
```

`team-builder-trace.json` records full inventory ID/quantity inputs and hashes, source state, reference provenance, leader resolution and decoded Rozé kit, compact candidate forms, relevance omissions, every eligible bucket row's full tuple/rank/pruning result, displayed assist options, counterfactual facts, 45 controlled swaps, leave-one-out checks, historical-document reconstruction, synthetic probes and test results. Nonmatching bucket exclusions are represented by each row's `answers`/`can_damage` plus the documented predicate, rather than thousands of repeated rejection messages. `candidate_scores`, `team_scores`, `selected_team` and score deltas are explicitly null with explanations.

The report is complete for the **available implementation**. Missing evidence prevents only stronger claims: exact historical equips/routes/reference/decoder/selection process, exact historical input revision, real board/dungeon behavior, actual per-copy upgrades and skill levels, real helper access, labeled scan accuracy, and any absent team scoring/search implementation. Those limitations cannot be resolved by making up scores or attributing decisions to code that does not exist.
