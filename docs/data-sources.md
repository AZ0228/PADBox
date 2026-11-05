# PAD data source investigation

Verified **2026-11-04** using public HTTPS requests, GitHub repository/commit APIs, and published source files. These are point-in-time checks, not availability guarantees. No game login, packet interception, or authenticated game endpoint was used. Raw research downloads are local and ignored; small verification summaries are retained in `docs/research/`.

## Decision

**Primary: a pinned PADDashFormation downloadable NA snapshot**, behind an adapter, with on-demand local icon caching. **Fallback: a user-curated JSON manifest and local reference icons.** Tsubaki's historical SQLite endpoint is not viable today. No external service is needed at runtime once references are cached. Public accessibility does not establish an unrestricted data or artwork license; no upstream data or game art is bundled in source control.

## Candidates

| Source | Access and maintenance check | NA and fields | Icons, interface, restrictions | Freshness and new collaborations |
| --- | --- | --- | --- | --- |
| [Tsubaki pipeline](https://github.com/TsubakiBotPad/pad-data-pipeline) | GitHub accessible; default-branch commit 2023-04-14; pushed 2023-05-14. The SQLite URL in [the download script](https://github.com/TsubakiBotPad/pad-data-pipeline/blob/master/utils/download_db_from_prod.sh) returned **404** to HEAD. | Historically NA/JP/KR: IDs, attributes, types, skills/CDs, awakenings, evolutions. | Published export scripts and schema. No root license detected by GitHub. Media processing code does not itself license the artwork. | Repository activity is stale; operational bot/private pipeline freshness cannot be inferred. No current collaboration coverage guarantee. |
| [Tsubaki exports](https://github.com/TsubakiBotPad/pad-data-pipeline-export) | Public tree accessible; commit 2023-04-15. | Text/textproto historical exports include monster/skill data. | Downloadable repository; no root license detected. Not a current API contract. | Stale historical fallback for investigation only, unsuitable for new cards. |
| [PADDashFormation](https://github.com/Mapaler/PADDashFormation) | Public repository and raw files returned 200. NA metadata file commit **2026-10-30**, `6d8b0698208aebef17e4f4fefe6b3c616f8d0ed8`. | `monsters-info/mon_en.json` and `skill_en.json`: IDs/names, multiple attributes/types, rarity, active/leader skills, initial cooldown and skill levels, ordered awakenings/super awakenings, evolution parents, explicit `henshinTo`/`henshinFrom`. Filter `enabled` and `isEmpty`; English file alone is not proof all rows are available. | Downloadable JSON and `images/cards_en/CARDS_*.PNG` sheets; first sheet fetched successfully. Code is GPL-3.0. Game/collaboration artwork remains third-party IP; repository license must not be treated as an artwork grant. Use local cache; preserve provenance; do not redistribute dumps. | `ckey.json` reports snapshot update time; not an independent guarantee of in-game freshness. **#13966 Rozé and #13967 Rozé & Zi-Artemis exist in enabled NA rows, with an explicit transform edge.** New collaborations still need per-release NA verification. |
| [PAD Index](https://pad.chesterip.cc/) | Web fetch returned 403; direct HTTPS later returned 200 for the SPA shell. These results are intentionally distinguished. | User-facing monster guide; complete current NA field coverage was not independently validated. | Shell advertises `pad-static.chesterip.cc` media base. No documented public bulk API or redistribution license was verified. We do not scrape private app APIs. | HTML access does not verify dataset freshness, new-card coverage, or API stability. Useful manual cross-check, not importer dependency. |
| [Ilmina](https://ilmina.com/) | Public landing page returned 200, Last-Modified 2024-01-02. | Searchable guide historically provides rich NA card/skill data. Present complete dataset not validated. | Public website; no supported bulk export/API or data license verified in this investigation. | Landing-page timestamp does not establish data age. Do not use as an automated authoritative source without further validation. |
| `padindex.com` | Returned 200 but currently serves an unrelated Korean secret-message site. | Not the PAD Index guide. | No PAD integration. | Domain name alone is insufficient evidence. |
| Official [NA site](https://www.puzzleanddragons.us/), [FAQ](https://www.puzzleanddragons.us/frequentlyaskedquestions), and in-game Monster Search | Accessible official documentation and announcements. | Strong source for NA release/availability verification; not a documented bulk monster database. | Public pages and in-game guide, copyrighted by their owners. No collection export or public database API found in reviewed documentation. | Best release-level cross-check; official news does not provide machine-ready mechanics for every card. |

Source field inspection is direct observation of downloaded files, not an assumption that code activity equals correctness. Full raw source effects (`type`, `params`) should be preserved with source version; they are **not** interpreted mechanics until an independently tested decoder is implemented. Awakening IDs without verified labels remain unknown rather than receiving invented effects. Assist evolutions are distinct cards and evolution edges, not additional owned copies.

## Other tools and iOS collection export

[PAD-Mate](https://github.com/buttwarmers/PAD-Mate) is a historical screenshot-recognition project: its public default branch last changed 2020-09-21, and its code license is GPL-3.0. It is prior art, not a maintained dependency or evidence of accuracy on today's iPhone UI. PADDashFormation is a maintained formation builder with player-data import documentation; the documented interception/emulator approaches fall outside this project's credential-free recording workflow. PADHerder/PADListener and PADDB are historical tools; no currently supported iOS export interface or maintained export contract was verified. Do not install obsolete interception tooling for this MVP.

The official [device transfer guide](https://www.puzzleanddragons.us/game-data-transfer-guide) documents account migration and backup, not a JSON/CSV monster-box export. **No supported iOS collection export was found** is a research result with limited scope, not proof that one can never exist. Screen recordings and manual additions remain the supported inputs here.

## Adapter contract and remaining checks

- Pin snapshot commit; store source URL, SHA-256, fetched time and upstream snapshot time separately. Never silently replace an existing reference set on network failure.
- Fetch only the requested icon sheets, cache once, validate image decoding and crop bounds. Missing icons leave cards searchable but unavailable to the visual matcher.
- Use already-normalized IDs in `mon_en.json`. Upstream raw internal IDs have a numbering discontinuity; do not apply the raw parser's ID conversion twice.
- Resolve transformations only from explicit edges. Preserve original source data for future structured skill decoding and evolving skill relations.
- Check new collaborations against in-game NA details before team optimization. NA release lag, buffs, unavailable placeholders, variants, and sprite-sheet numbering are material risks.
- Curated manifest accepts provenance, availability, unknown/null mechanics and local icon paths. Synthetic demo cards are explicitly labeled non-PAD fixtures and stored in a separate demo home.
- A real labeled screenshot/recording set is still required to qualify recognition and slot tracking. Synthetic success cannot establish production accuracy.

## Implemented-source smoke test

A full pinned sync on 2026-11-04 loaded **13,894 enabled/nonempty NA-file records** and cached **13,027 usable icon crops**, with **867 missing or blank-looking icons**. These counts are adapter observations, not a claim of independent in-game availability verification. A detailed missing-ID report is written locally to `.padbox/source-report.json`. Metadata plus compressed source cache occupies about 145 MiB; extracted icons about 137 MiB on this machine. Files were downloaded from 143 public sheets, not from game endpoints. No assets are tracked in Git.

The full-library reference-derived montage test matched 13/13 detected crops correctly but detected only 13/15 displayed cells. This confirms source loading and matching against a large library, while exposing incomplete detection coverage. It is not a real screenshot accuracy measurement. See [validation.md](validation.md).
