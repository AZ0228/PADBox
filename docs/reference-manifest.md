# Local reference and evaluation formats

## Curated references

`padbox sources load references.json` accepts a JSON **list**. Icon paths are local, absolute or relative to the manifest. Missing/unreadable icons fail validation before database mutation; omit `icon` to load searchable metadata without a visual reference. Duplicate IDs in a manifest are rejected. Unknown metadata is omitted or null; do not invent mechanics.

```json
[
  {
    "monster_id": 13966,
    "name": "Rozé",
    "servers": ["NA"],
    "attributes": [3],
    "types": [1],
    "rarity": 8,
    "icon": "icons/13966.png",
    "provenance": {
      "source": "Personally verified in-game NA detail screen",
      "version": "2026-11-04",
      "updated_at": "2026-11-04T00:00:00Z"
    },
    "relationships": [{"target_id": 13967, "kind": "transform", "evidence": "verified"}]
  }
]
```

The values above illustrate an interchange shape using the source-checked Rozé identity; a real curated manifest should cite the observation you personally verified and use your own local icon. Optional fields include `active_skill`, `leader_skill`, ordered `awakenings`, `super_awakenings`, typed `relationships`, and `raw`. The Pydantic models in `src/padbox/models.py` define the contract.

Skills preserve `skill_id`, name, description, base/minimum cooldown, raw source fields, typed effects, and evidence status (`unknown`, `source_encoded`, `verified`, `heuristic`). The snapshot adapter preserves raw skill parameters and explicit monster transformations; it does not claim to decode all skill effects or evolving-skill stages. Future typed effects need a tested decoder, source version, and evidence of semantic correctness.

Evolution edges point parent → child and distinguish `evolution`, `assist_evolution` and `transform`. Transforming in battle never creates a second owned copy. No unknown awakening name or effect is synthesized from an ID.

## Labeled evaluation

```json
{
  "dataset": "NA iPhone model / game version / capture date / manually labeled",
  "examples": [
    {"image": "crops/slot-001.png", "monster_id": 13966},
    {"image": "crops/slot-002.png", "monster_id": null}
  ]
}
```

Image paths are relative to the labels file unless absolute. A null label means outside the cached reference set or an intentional non-monster/unknown example. Label known cards against the in-game ID, not the importer's proposal. Include visually similar evolutions, distinct collaborations, overlays, compressed frames, partial crops and unknown references. Split reference-derived perturbations from independent real captures; do not report them as the same dataset.

```sh
uv run padbox evaluate labels.json --output data/evaluation.json
```

Top-1 and top-3 accuracy use known labeled examples as their denominator. False-high-confidence rate is incorrect high-confidence predictions divided by all high-confidence predictions (null if there are none). `false_high_per_example` uses all examples. Unknown rejection is the fraction of unknown examples that do not receive a high-confidence result. The harness also reports coverage, counts and failures. No claim about general PAD recognition quality is warranted from a small or synthetic set.
