# MVP validation and milestone handoff

Verified on **2026-11-04**, macOS ARM64, Python 3.12.16, OpenCV 4.14.0.94. Dependencies are pinned in `uv.lock`. An initial private iPhone recording has now been processed. There is still **no independently labeled real PAD recognition/count accuracy** measurement. A runnable vertical slice is implemented, but the full real-recording definition of done is not claimed.

## Count pending material stacks as one — 2026-11-04

Added **Use quantity 1 for all pending stacks** under Session entries and in Streamlit.
The action works across the entire selected paused/completed draft, regardless of queue
filter. It sets active unverified stack quantities to 1 and resolves their quantity checks;
already verified counts and all identity/tracking decisions are preserved. Each affected
copy has a saved `quantity` audit event identifying the assumption and its previous value.
Quantity warnings disappear when resolved. Saved ownership still changes only through a
fresh diff and explicit Apply, with exports using the assumed counts afterward.

Eight added tests cover separate-copy preservation and exact verified counts, other-scan
isolation, identity/tracking blockers, idempotent retries, stale diff refusal, Apply/export
counts, applied-history protection, capture-state guards, rollback of mid-batch failures,
HTTP authentication/origin/body validation, Streamlit interaction and browser queue cleanup,
warning removal, reload persistence and request failure. All tests use temporary homes;
no real player draft or saved box was changed and no permanent server was started.

**Final full suite: 177 tests passed in 77.70 seconds.** Ruff lint/format and browser
JavaScript syntax checks passed. Restart the foreground server and refresh the page,
load the saved draft, then click the bulk quantity button. No source connection is required.

## Keyboard review queue — 2026-11-04

The live page now keeps manual approvals in an ordered queue. Accept/reject advances to the
next entry by default; next/previous skips without saving. Box order and ascending/descending
similarity are available, with filters for unresolved identities, unmatched entries, copy
tracking, stack quantities and all active copies. The default queue retains an entry while
stack quantity is unresolved; an identity-only pass leaves that work pending for later.
Alternatives require a separate accept action. Explicit separate-copy confirmation, merge
safeguards, persisted review events and the final diff/Apply workflow remain in effect.

Ten additional Chromium cases exercise alternative selection and automatic advancement;
navigation/wrapping and reload without a feed; typing, modifiers and held-key suppression;
stack identity/quantity sequencing; sorting/filtering and explicit copy verification;
rejection/completion; disabling advancement; rapid input saving exactly one decision;
applied-history edit refusal; failed requests preserving choices; an identity-only stack
pass; and merge navigation retaining separate-copy semantics. Tests use temporary homes.

Desktop/mobile render checks used a read-only SQLite snapshot of the latest actual player
scan. Its selected unresolved queue contained **219 entries**. Keyboard navigation worked
without a source connection, with **zero saved decisions**, no JavaScript page errors and
no horizontal overflow at a 430px viewport. Images remain ignored under
`data/keyboard-review/`. This verifies review usability, not recognition accuracy. No actual
player entries or applied ownership were changed, and no permanent server was launched.

**Final full suite: 169 tests passed in 71.09 seconds.** Ruff lint/format and JavaScript
syntax checks pass. The built wheel includes all five local
frontend assets, including `review-queue.js`, and excludes private data. Restart the foreground
server and refresh the page to load the new module. Load the saved scan, press **R** to open
review, **A** to accept, **X** to reject, **J/K** to skip and **1–3** to choose alternatives.

## Threshold-based batch identity acceptance — 2026-11-04

Follow-up: removed the artificial 0.60 minimum from explicit batch review. Backend policy,
browser validation/HTML and Streamlit now allow **0–1** for both score and margin. Automatic
recognition/proposal gates remain unchanged, so unmatched entries without a proposed ID stay
excluded even at zero. Added boundary/low-proposal tests and exercised zero in both review
interfaces and 0.50 through HTTP: **34 targeted tests passed in 9.59 seconds**, with Ruff
lint/format and browser JavaScript syntax checks passing. No player entries were changed.

Added an explicit batch-review service, authenticated local HTTP preview/accept endpoints,
and matching controls in the live page and Streamlit. Paused/completed snapshot drafts can
be reviewed without a source connection or rescan. Users choose minimum similarity and lead
over alternatives (defaults 0.80 and 0.04); preview reports exact qualifying counts, exclusion
reasons, cumulative threshold counts and remaining identity/stack-quantity work. The browser
shows weakest qualifying capture/reference pairs first, 24 at a time, with individual review
and clickable threshold bands. A single acceptance action confirms the exact preview in the draft.

Copy ambiguities, unmatched entries, exact ties, missing ranked evidence/references and previous
identity decisions remain excluded. Stack identities can qualify without certifying their xN
quantity. Preview binds policy, scan state, evidence, copy rows, audit events and references;
acceptance recomputes inside an immediate transaction and rejects stale previews. Browser
threshold/session changes invalidate the displayed approval. Streamlit retains the displayed
token across reruns, so another tab changing evidence cannot silently replace its preview on
the acceptance click. All per-copy writes/audits commit together or roll back. Confidence flags,
copy IDs, duplicate counts, quantities, tracking, saved exports and applied history are preserved.
The automatic matching rules are unchanged: explicit bulk acceptance is a user decision,
and similarity thresholds are not calibrated accuracy probabilities.

Automated coverage includes thresholds/margins, weakest-first order, band counts, duplicate-copy
preservation, reference/evidence gaps, ties, prior decisions, stack quantity blockers, stale
policy/evidence/reference/quantity edits, atomic rollback on mid-batch failure, paused/completed
versus running/applied captures, replay protection and HTTP token/origin validation. Chromium
exercises capture/finish, threshold invalidation, paired images, clickable bands, acceptance,
reload without a source and stale-preview rejection. Streamlit exercises acceptance and an
evidence edit between display and click. The final full suite passed **154 tests in 57.09 seconds**.
Ruff lint/format and JavaScript syntax checks passed. Desktop/mobile
renders of the actual completed scan were inspected on a temporary database copy with no
JavaScript errors. A built wheel includes the batch service and all local UI assets, excluding
private data.

Actual eligibility on a read-only snapshot of the user's completed scan (**766 uncertain IDs**),
with minimum alternative lead **0.04**:

| Minimum similarity | Identities qualifying |
| --- | ---: |
| 0.90 | 1 |
| 0.85 | 40 |
| 0.80 | 251 |
| 0.75 | 419 |
| 0.70 | 569 |
| 0.65 | 693 |
| 0.60 | 734 |

At lead 0.02 / score 0.75, **423 identities** qualified and were accepted on a temporary copy,
leaving **376 uncertain/unmatched identities** and **93 newly accepted stack quantities** to
verify. The saved collection, slot count, per-slot quantities and tracking stayed identical;
this preview/accept validation took **2.87 seconds**. At lead 0, exact ties still stay excluded
(761 of 766 uncertain proposals qualified at similarity 0.60). These are measured policy counts,
not measured recognition accuracy. The actual player scan was not accepted/applied during
implementation. Ignored local artifacts are under `data/batch-validation/`.

Restart the foreground server, refresh the page, load the completed scan, and use
**Batch accept identities → Preview qualifying matches → Accept N qualifying identities**.
The full-box diff and Apply remain a separate final step.

## Liberal ordered-row joining and saved-map repair — 2026-11-04

The two-row/six-near-exact-anchor relocation gate still fragmented faster live scrolling. It also treated several overlapping saved fragments as competing destinations and blocked whole-map joining when old tracking had inserted duplicate rows at identical positions.

Added shared ordered-overlap consensus for adjacent tracking and map relocation: four positions, three distinct ≥0.90 artwork anchors across three columns, median ≥0.88 and robust support across the remaining positions. One distinctive row is sufficient, including one obscured icon in a five-column row. The existing ≥0.025 rival-placement separation remains. The default now assumes ordered rows are distinctive in the observed box; repeated rows with competing placements and completely unobserved gaps remain unresolved. These thresholds concern copy tracking, not monster recognition acceptance.

Resume and newly mapped positions reconcile the complete saved map, including off-screen overlaps, preferring the anchored map and then older fragments. Repair composes local-copy aliases, preserves physical viewport coordinates, keeps original observation/review history, and never deduplicates by monster ID. Legacy duplicates at the same positions can be resolved by ordered artwork or stable neighboring copy IDs observed alongside both appearances in the same screen row. Reviewed source decisions, conflicting confirmed IDs, same-frame co-occurrence (including merged descendants), and applied history remain protected. Uniquely known returning positions survive overlay changes without becoming new copies. Failed resume repair rolls back and reloads durable state.

**Actual automated run: 125 passed in 51.78 seconds.** Thirteen additional cases cover single-row bridges; off-screen repair on resume with unchanged observation counters and correct viewport translation; five-row jumps through six-row views with one overlapping row, an obscured icon and a restart; full/partial same-position revisit repair; independent same-row neighbor evidence; simultaneous-copy and manual-decision preservation; resume-repair rollback/retry; applied-history immutability; canonical visible IDs when legacy sections compete; and changed overlays on historical rows. Existing recognition, snapshot diff/apply/export, database, live HTTP, Chromium and Streamlit tests also pass.

**Actual saved-scan copy:** the user's latest captured map had **329 active tracks across five sections**. A temporary SQLite snapshot repaired to **176 mapped entries in one section**, archiving **153 repeated detections**, with **one individual copy ambiguity left**. Four sections joined in **1.28 seconds**, with referential integrity and the saved applied-box document unchanged. An older separate scan repaired from four sections/160 tracks to two sections/120 entries; its disjoint remaining section had no matching captured overlap and stayed unresolved. These are measured reconciliation outcomes under the row-distinctiveness assumption, not independently labeled collection counts. Source SQLite files were opened read-only; actual player drafts/applied ownership were not changed during validation. Private scripts/results remain ignored under `data/resilient-joining/`.

**Supplied recording replay:** 86 JPEG samples with 17 simulated capture delays retained **100 mapped slots in one section**, zero flagged copy ambiguities, one sequence-rule auto-acceptance and three unmatched IDs. Restart restored the identical map, and returning to the first frame retained 100 slots. Runtime **56.38 seconds**. No independent identity/count labels or physical live-feed permission path were verified. To activate in the foreground server, restart it, refresh the page, load the existing draft and resume; saved overlap is repaired before new frames are needed.

## Tracking continuity, fragment joins and readable tiles — 2026-11-04

A real live scan exposed disconnected map fragments, pervasive copy-tracking warnings and labels obscuring the reconstructed artwork. Wall-clock upload/rendering delays over five seconds no longer discard the previous visual evidence. Explicit feed changes, missing grids, skipped sequences and restarts retain their existing relocalization checks. Crop thumbnail requests now read only the requested copy's evidence. A read-only three-run benchmark on the local saved database measured a median **99.15 ms / 1,760 entries loaded** for the previous all-inventory read versus **0.58 ms / one entry** for the scoped lookup; these are local lookup timings, not promised end-to-end frame rates.

Continuing through an unplaced section now searches other sections for a unique multi-row bridge. Before joining, all saved overlapping positions are validated, and manual decisions, conflicting artwork/confirmed IDs and recursive same-frame co-occurrence block automatic merging. Repeated source slots become audited merged entries with their original observations retained; genuinely separate positions keep their IDs. Resolved section-placement uncertainty clears, while unrelated conflicts remain reviewable. Joins and map updates roll back with a failed frame, including frame counters. The slot count is explicitly provisional while copy ambiguity remains. Compact container-scaled labels and corner status symbols keep the art visible; reference images are not reloaded solely because a similarity score improves.

Automatic acceptance retains the single-frame ≥0.94 / ≥0.055-margin path and adds a sequence path: the last three saved observations from distinct frames must agree on the proposed ID, each ≥0.85 with ≥0.055 separation and three returned candidates. Clear tracking and a non-stackable reference remain mandatory; human decisions take precedence. Exact skipped repeat frames do not accumulate evidence. Audit events distinguish the rules and retain supporting frame indices. The sequence rule is heuristic consistency evidence, **not a calibrated probability or a measured real false-acceptance rate**.

**Final automated run: 112 passed in 50.22 seconds.** Added regressions cover delayed uploads with preserved adjacent overlap; fragment bridging, duplicate-copy preservation and persisted joins; manual-copy and artwork/co-occurrence conflicts; joined-frame rollback and retry; scoped crop lookup; repeated-acceptance agreement, close alternatives, insufficient/distinct frames, low scores, stackable items, manual reviews and applied-history protection; and narrow-tile artwork visibility/image stability in Chromium. Ruff lint/format and both JavaScript module syntax checks passed. Desktop, narrow-pane and mobile captures using the supplied recording's first frame were rendered and inspected in an isolated temporary home; private artifacts remain under ignored `data/tracking-fix/`.

**Actual supplied-recording replay:** 86 quality-90 JPEG samples, with **17 simulated eight-second capture delays**, produced **100 mapped slots in one section**, **zero tracker-flagged copy ambiguities**, **one automatically accepted identity** via the repeated-evidence rule and **three unmatched entries**. A restart restored the identical map; returning to the initial frame relocalized and retained 100 slots. Runtime: **68.99 seconds**. No player inventory was opened for writing or applied, and no permanent/background server was started. This replay verifies the regression path; slot counts, IDs, full-box completeness and the physical iPhone capture path still lack independent ground truth. Acceptance coverage remains conservative.

To use the changes, run the foreground server and refresh the page. Existing unjoined sections require newly captured bridging overlap or manual reconciliation; the fix does not declare disconnected historical evidence correct merely because the code was updated. Keep the same sort/filter and scroll through overlapping rows. Applied snapshots remain untouched.

## Persistent box map and minimap — 2026-11-04

Added a persistent per-scan spatial map with global row coordinates, scroll direction, section identity and captured viewport. Forward/backward overlap reuses local copy IDs. Multi-row relocalization can reconnect a known position after a no-grid frame, sequence gap or server restart. The first and latest crop appearances are retained to handle animated overlays; matching uses observed artwork/geometry independently of proposed/confirmed monster IDs. Unlocatable or repeated sections remain separate and ambiguous; direction alone cannot establish identity.

The reconstructed pane includes a canvas minimap of all scanned slots. Its cyan window tracks the captured viewport; a white dashed window marks independent browsing. Drag, wheel and arrow/Page keys browse saved rows without moving the iPhone, changing counts or pausing capture. Follow live restores frame alignment. Saved maps can be reviewed with no connected feed. Short scans retain compact thumbnails; long maps compress to the rail. Unknown joins and copy/identity review states remain visible. Inventory schema v3 adds map tables without altering earlier observations/decisions; older scans are not retroactively assigned a certified map.

**Final automated run: 93 passed in 42.35 seconds.** Ten new backend cases cover world coordinates, forward/reverse revisits, separate duplicate copies, restart persistence, missing templates, multi-artwork repeated-block ambiguity, unknown sections, rejection filtering, migration, transactional rollback and first/latest overlay evidence. Three additional Chromium cases cover the sliding window, direction, drag/keyboard navigation while scanning, Follow live, reviewing a saved map without capture, and visible unjoined sections. Earlier reconnect tests now assert proven map relocalization instead of expecting duplicate ambiguous observations. A scan-start control race caught during integration is fixed by disabling finish while a new scan is loading; browser tests wait for the new session identity before checking its counters. Ruff lint/format and both JavaScript module syntax checks pass.

**Actual real-recording replay:** 86 quality-90 JPEG samples at 0.25-second recording intervals produced **100 active mapped slots in one section**, with **zero tracker-flagged copy ambiguities**. A service restart restored the identical map. Returning to the initial recorded frame then relocalized with **30 anchors**, direction **up**, and retained **100 slots / one section** rather than appending another screenful. Runtime: **69.18 seconds**, including restart/return. The first attempt using only latest appearances failed to reconnect; preserving first/latest observed crops resolved that case, with a synthetic overlay regression test. Raw report: ignored `data/map-validation/replay.json`.

Desktop/minimap, match-review and narrow-screen renders were inspected against the real phone frame; screenshots remain under ignored `data/map-validation/`. All replay/render work used temporary inventory homes with a read-only copy of cached reference metadata. No player scan was applied or changed and no permanent server was launched. These results demonstrate replay/relocation consistency, not independently labeled inventory accuracy, completeness, or the physical QuickTime/iPhone capture path. An unseen visually identical multi-row section remains fundamentally indistinguishable using images alone.

## Continuous scrolling default — 2026-11-04

Automatic pausing on grid loss or uncertain overlap previously interrupted continuous scanning. New pages now default to continuous capture, with optional **Pause on tracking warnings** in the visible toolbar. Tracking gaps remain recorded and block Apply until reconciled; the “Track?” badge describes copy-count uncertainty separately from identity matching. Explicit review, feed loss, size changes and request failures still pause ingestion. Repeated blank frames retain their “No box grid” explanation instead of replacing it with “Unchanged view.”

**Final run: 80 tests passed in 33.23 seconds**, including 14 headless Chromium cases. New browser checks continue through a blank transition, recover into ambiguous tracking, ingest further scrolling without Resume, and verify unresolved copy counts still block Apply. Forward/reverse scrolling retains 20 synthetic entries without duplication. Optional automatic pause and feed-reconnection ambiguity remain covered. Backend validation checks that repeated blank frames retain both status and reason. Ruff lint/format and JavaScript syntax checks pass.

This changes capture flow, not tracking certainty or recognition thresholds. Existing ambiguous entries are preserved for review. Tests use temporary homes; no real player draft or applied box was changed, and no permanent server was launched.

## Side-by-side reconstruction — 2026-11-04

The desktop capture workspace now defaults to equal capture/reconstruction panes. Resolved reference icons follow the sampled grid coordinates; separate local copy IDs keep separate tiles. Accepted, proposed, unmatched, copy-ambiguous and unverified stack-count states remain distinct. Missing reference assets fall back to the captured crop with a warning outline. Clicking either pane opens review on the right; Back to box, Escape or Resume restores reconstruction and corrected icons. Small screens stack the panes.

**Final automated run: 78 tests passed in 26.07 seconds.** Five additional Chromium tests verify default pane layout, spatial alignment, separate copies, tracked-node reuse and row movement, correction/back/resume transitions, unknown/missing-reference handling and mobile overflow/stacking. The prior snapshot, export, source, tracking and lifecycle tests still pass. Ruff lint/format and JavaScript syntax checks pass.

A render check additionally uses the first supplied portrait recording frame with the cached real references, in a temporary inventory home. It displays 30 visible slots and captures desktop reconstruction, review and mobile layouts under ignored `data/ui-layout-validation/`. This verifies presentation on the real phone aspect ratio; it does not establish identification/count accuracy or exercise a physical live feed. No player collection was changed and no permanent server was launched. Reconstruction updates at the existing processed-sample rate, not on unsampled game frames.

## Current snapshot workflow — 2026-11-04

At the user's request, full-box replacement snapshots supersede the original additive importer described in the historical milestones below. New file/live captures are isolated drafts. Strong, clear-tracking matches are automatically accepted **within drafts**; lower-confidence identities, copy ambiguities and stack quantities require review. Preview changes shows quantities before/after and additions/removals, and explicit Apply replaces the saved box atomically. Applied scans remain read-only history. Export reads only the saved box.

**Actual final run: 73 tests passed in 19.98 seconds.** This includes eight snapshot service/migration/concurrency cases, seven real Chromium frontend cases and three Streamlit UI cases, alongside the vision/source/import/lifecycle tests. Verified:

- Identical full rescans yield an empty diff and preserve copy counts instead of adding to them.
- Added, removed and quantity-changed IDs are diffed correctly; absent cards are removed on Apply.
- Draft creation and automatic identity acceptance leave the current saved box/export unchanged.
- Unfinished/empty/unresolved scans, unverified stack quantities, omitted full-box attestation and mismatched expected slot counts cannot apply.
- Draft edits or another applied scan invalidate a previously previewed diff. Two concurrent applications against one baseline cannot both succeed.
- Applied evidence cannot be changed through identity, rejection, merge, addition, quantity or rematch paths. Before/after documents and diffs survive in history.
- SQLite v1 → v2 migration retains legacy observations/decisions, carrying only confirmed legacy ownership into the initial baseline.
- Actual browser UI exercises capture, manual correction, preview, completeness checkbox, expected slots, Apply, export state, identical rescan and history. The fixture uses a continuous original synthetic canvas feed in place of the OS source picker.
- Streamlit exercises manual identity correction persistence and the same preview/confirmation/apply flow.

All validation uses temporary homes; no scan was applied to the real player collection. The user reports that preliminary displayed real matches were correct apart from one or two unmatched cards. This is useful qualitative feedback, not a labeled accuracy or completeness measurement. Similarity thresholds remain unchanged and uncalibrated; automatic acceptance coverage on future real captures is not claimed. Full-box coverage still requires user verification against the game, because unshown rows and hidden/filtered content cannot be inferred.

The foreground terminal lifecycle remains the default: `padbox live`, Ctrl+C to stop. No permanent server was launched during this change.

## Initial implementation results (historical)

| Check | Measured result | Interpretation |
| --- | --- | --- |
| Automated unit/integration/UI suite | **37 passed**, 4.54 seconds on the final implementation run | Covers source validation/cache integrity, database boundaries, transforms, grid location/scale/partial rows/low contrast, repeated patterns, overlap, reverse scroll, copy preservation, manual corrections, merges, quantities, uncertainty export, CLI and Streamlit acceptance. |
| Ruff lint and formatting | Passed for `src`, `tests`, `scripts` | No static lint/format errors. No claim of a separate strict type-check run. |
| Procedural recording | 20 frames at 10 fps; 7 sampled; 4 redundant samples skipped; **45 observations → 20 inventory entries**; zero slot ambiguities in this fixture | Two identical icons in separate first-row slots remain two entries. No IDs are confirmed automatically. |
| Reverse-scroll recording test | **25 entries** after forward/backward revisits | Historical slot coordinates prevent recounting re-entering rows within continuous overlap. |
| Synthetic reference perturbations | 45 known crops + 1 unknown; **100% top-1/top-3**, 32 high-confidence predictions, **0/32 false high-confidence**, unknown rejected | Small, generated reference-derived problem; not representative of the full game. Coverage 32/46 = 69.6%. |
| Crops actually extracted from procedural video | 45 known crops; **100% top-1/top-3**, 18 high-confidence, **0/18 false high-confidence** | Includes video compression and detector crop jitter. Coverage 40%. Generated art remains easy compared with PAD variants. |
| Live public snapshot adapter | **13,894 metadata records**, **13,027 usable icons**, **867 missing/blank-looking assets** | Full public icon cache downloaded successfully; missing IDs remain searchable. This is source coverage, not recognition accuracy. |
| Full-library, reference-derived PAD icon grid | **13/15 cells detected**; 13/13 detected crops correct top-1/top-3 against **13,027 icons**; 2 high-confidence; **0/2 false high-confidence** | **Two cells were missed.** Detection recall 86.7% on this single constructed layout. End-to-end correct recovery 13/15, rather than 100%. No independent capture/label evidence. |
| Full-library timing | Approximately 5 seconds matcher initialization; approximately 0.13 seconds for 13 crops | One local smoke measurement, not a performance guarantee for a long recording. |
| Review UI | Streamlit AppTest rendered review, accepted an entry, verified SQLite persistence, opened collection and calibration; HTTP health returned `ok` | Browser screenshot inspection could not run because the browser access host was unavailable. |
| Default player inventory | Empty after source setup | Test/demo entries are isolated; no fictional collection has been inserted into the real collection. |

The first full-library montage attempt detected no cells. A conservative low-contrast contour/gap-closing fallback improved this to 13/15 while retaining the original strong-edge path for bordered synthetic video. The two remaining misses are deliberately retained as an open detection limitation, not filled with invented observations. The test script saves the original grid and annotated result for inspection.

Similarity thresholds are heuristics. The default strong-match gate is score ≥0.94, runner-up margin ≥0.055 and at least three usable references; all results still need explicit user acceptance. A best score below 0.60 remains unmatched, with alternatives retained. Repeated visual patterns or inadequate overlap cannot establish inventory-copy identity and produce ambiguous entries. None of these thresholds is calibrated on real PAD recordings.

## Reproduce

```sh
uv sync --python 3.12
uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts

# Choose a new, empty home:
uv run padbox --home data/my-demo demo
uv run padbox --home data/my-demo review

# Cached PAD assets stay local; this constructed-grid check never edits player inventory:
uv run padbox sources sync --all-icons
uv run python scripts/validate_reference_slice.py
```

An already generated review demo is available under `data/sample/`:

```sh
uv run padbox --home data/sample review
```

Local artifacts include `data/sample/evaluation.json`, `data/sample/video-evaluation.json`, `.padbox/reference-validation/results.json`, the annotated grids and extracted crops. These are ignored, including all downloaded artwork. The validation script reports independent detection and matching denominators; recognition accuracy among detected cells must not hide missed cells.

## Milestone 0 — Research

Implemented: dated source comparison, direct endpoint checks, provenance/license limitations, primary pinned PADDashFormation adapter and curated fallback. Verified GitHub metadata/commits, JSON schemas, downloadable icon assets, historical SQLite 404, PAD Index's tool/direct-HTTP difference and official iOS migration documentation.

Works: public metadata/icon acquisition without accounts or game endpoint access. Uncertain: regional timing/availability, future source maintenance and redistribution permissions. **Input needed: none to use the current local snapshot.** A future new collaboration needs another NA verification pass; source availability does not establish every mechanic as verified.

## Milestone 1 — Foundation

Implemented: uv project, typed Pydantic contracts, separate versioned reference/inventory SQLite files, skills, ordered awakenings, server availability and typed transformation/evolution edges; transactional curated imports and cached public snapshots. Tested missing icons, tampered cache, unsupported schema versions, source/version icon provenance and #13966→#13967-style explicit edges.

Works: live full snapshot load; selected or full icon caching; unknown mechanics remain source-encoded/raw. Uncertain: 867 absent/blank-looking icons, source-semantic completeness and unimplemented skill-effect/evolving-stage decoding. **Input needed: none for the importer; curated icons can fill specific gaps.**

## Milestone 2 — Video ingestion

Implemented: still/video inputs, SHA-256 identity, FFprobe metadata and OpenCV rotation-aware sequential sampling, contour lattice detection, ROI/aligned-grid calibration, annotated frames, per-slot observations, historical overlap tracking, idempotent/restartable imports and explicit cross-recording reconciliation. Tested position/scale/low contrast, repeated frames, scrolling, reverse revisits, identical monster copies, partial rows and transitions.

Works: procedural video ingestion and conservative duplicate handling; real reference-derived image detection is partial. Uncertain: current iPhone screen geometry, motion blur, overlays, missed rows, rotation/HEVC/VFR behavior in a real capture, and stack quantities. Forced grids support aligned stills, not scrolling-phase inference. **Input needed: a short original iPhone recording with slow scrolling, repeated rows and at least two known duplicate copies.**

## Milestone 3 — Recognition

Implemented: normalized masked artwork, DCT hash, HSV histogram, pixels, template correlation, vectorized shortlist, ranked candidates, explicit unmatched/uncertain states, reference/crop evidence and evaluation harness. Tested known matches, near-identical-reference ties, unknown rejection, tiny/full libraries and deliberately false labels in the metric harness.

Works: the measured synthetic and constructed-grid tests above. Uncertain: real-capture variant discrimination, shortlist recall, open-world false positives and confidence calibration. Embeddings are deferred because independent labeled evidence is needed to establish whether they improve this baseline. **Input needed: manually verified IDs for representative real crops, including similar variants and cards missing from the reference cache.**

## Milestone 4 — Review and export

Implemented: local Streamlit review/calibration, alternatives/search, accept/reject, explicit high-score batch acceptance, copy reconciliation, audited manual additions/count changes, collection statistics, persistent corrections, rematching and consistent JSON/CSV. Tested UI acceptance and persistence, reimports/failed-run resumes, direct and indirect same-frame merge safeguards, quantity changes and uncertainty export.

Works: complete local workflow on generated fixtures. Incremental additions are explicit; full-box re-recordings require manual reconciliation. Uncertain: usability on a large real box; no automatic full-box replacement or stacked-quantity OCR. **Input needed: the real recording and your corrections; no architectural or paid-service decision is pending.**

## Next real-data acceptance gate

Use the recording to calibrate ROI, independently count visible entries/duplicate copies, label representative crops against in-game IDs, and measure detection recall, top-1/top-3, false-high-confidence rate and slot/count errors. Inspect both accepted and rejected examples. Persist the corrected collection and reimport the same recording to verify the decisions survive. Expand the labeled set before trusting batch acceptance across a full box. No optimizer recommendation should be generated until collection identity and mechanics are independently qualified.


## First real-recording pilot

A user-provided portrait HEVC recording exposed failures not represented by the initial fixtures: generic contours missed the actual box UI, row alignment changed continuously during scrolling, and animated enhancement/Assisting overlays changed otherwise stationary crops. The importer now supports normalized calibrated geometry with per-frame border-phase fitting, bounded forward/backward optical-flow evidence, and later-observation improvements to unreviewed proposals. Review includes direct alternative selection, source timestamps, merge-target crops and stack-quantity warnings. Identity confidence thresholds were not lowered to promote real crops into confirmed identities.

The expanded suite includes scroll-phase tests at several row offsets, blank-screen rejection, optical-flow checks, repeated-pattern refusal and stackable batch exclusion. Private calibration, crops, import statistics and draft exports stay under ignored local data directories. These results establish that the recording can be processed, not that every recovered slot or proposed ID is correct. Ground-truth labels, independently checked duplicate counts and stack quantities are still needed before claiming a verified collection.


## Live capture implementation

Added a loopback browser capture/review UI (`padbox live`, port 8502), sharing an extracted incremental frame processor with file imports. It supports explicit window selection, phone cropping, per-session calibration, live ID/name/similarity overlays, click-to-pause review, manual ID/search/alternatives, safe copy reconciliation, persistent sessions and collection exports. The private iPhone calibration from the recording can initialize the live page; it is not bundled as a universal preset.

The expanded automated suite has **58 passing tests**, including nine live session/HTTP cases and four headless Chromium end-to-end cases. Backend cases cover copy preservation, idempotent retries, pause/restart, correction persistence, source/size changes, sequence gaps, decoder bounds, collection overlap, same-frame merge protection, request origin/token enforcement and simulated disk failure. Browser cases use an original procedural canvas stream and exercise selecting/cropping a source, starting, clickable detection review, correction, pause/resume, scrolling, finish/reload, capture denial, capture ending and auto-pause on a blank transition. Chromium source selection is substituted in these tests: they do **not** verify the macOS capture permission dialog or physical iPhone feed.

A full-page browser render was also inspected from an isolated synthetic session; no JavaScript page errors were observed. Browser test prerequisites are `uv sync` and `uv run playwright install chromium`. If that test browser is unavailable, browser tests explicitly skip with installation instructions; that skip is not evidence of a pass.

The supplied recording is additionally replayed as quality-90 JPEG samples through the live session service in an isolated data home. Results are local under `data/live-recording-validation/validation.json`; the existing player collection is not altered. This checks the live ingestion contract against the real recording, not a physical live-feed connection. Recognition/count ground truth remains unqualified, and no identities are confirmed by replay.


Final live replay result: **108 JPEG samples → 3,085 observations → 100 active entries**, with **99 uncertain identity proposals, one unmatched, zero confirmed**, and no slot ambiguities flagged by the tracker. This reproduces the earlier file-import counts in 67.77 seconds of local processing; it does not independently establish their correctness. Live capture therefore samples at the processing rate and requires slower scrolling/row pauses rather than promising 5fps recognition. The existing 100-entry player draft was checked and remained unchanged.

Final checks: **58 tests passed in 14.21 seconds**, Ruff lint/format and JavaScript syntax checks passed, and a built Python wheel was inspected to confirm inclusion of the three local frontend assets without collection data. The running default live page was opened in headless Chromium and verified to load the private five-column scrolling calibration with no JavaScript page errors or test sessions in the actual player collection.


## Live server lifetime fix

An open browser page could retain the capture UI after its foreground Python server had stopped; starting a scan then produced the browser's generic “Failed to fetch.” Added `padbox live --background`, which launches a detached process and verifies readiness before returning, with scoped `--stop`, local process/log files, and saved layout reuse. Starting again reuses a healthy server for the same collection and rejects an occupied PADBox port belonging to a different collection. The browser checks reachability before creating a session and displays restart/refresh instructions on a failed network request; stale-page authorization also explicitly requests refresh. Tests cover a child server surviving its launcher, stop/restart and calibration persistence, offline scan startup without session creation, and a stale page after server restart.
