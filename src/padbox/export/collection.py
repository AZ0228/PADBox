"""Aggregated quantities plus individual unresolved evidence; no silent unknown IDs."""

import csv
import io
from collections import Counter

from padbox.database.store import Store
from padbox.inventory.service import entries
from padbox.inventory.snapshots import current_box


def collection(store: Store, confirmed_only: bool = False) -> dict:
    current = current_box(store)
    if current is not None:
        return current
    rows = [e for e in entries(store) if e["status"] not in {"merged", "rejected"}]
    counts: Counter = Counter()
    unresolved = []
    for row in rows:
        confirmed = row["status"] == "confirmed" and row["tracking"] == "clear"
        if confirmed:
            counts[(row["monster_id"], True)] += row["quantity"]
        elif not confirmed_only:
            # Proposed IDs are explicitly separate from accepted identity.
            unresolved.append(
                {
                    "inventory_id": row["id"],
                    "monster_id": None,
                    "proposed_monster_id": row["proposed_id"],
                    "quantity": 1,
                    "confirmed": False,
                    "status": row["status"],
                    "tracking": row["tracking"],
                    "confidence": row["confidence"],
                    "recording_id": row["recording_id"],
                }
            )
    return {
        "schema_version": 1,
        "server": "NA",
        "monsters": [
            {"monster_id": mid, "quantity": quantity, "confirmed": confirmed}
            for (mid, confirmed), quantity in sorted(counts.items())
        ],
        "unresolved": unresolved,
        "complete": len(unresolved) == 0
        if not confirmed_only
        else all(e["status"] == "confirmed" and e["tracking"] == "clear" for e in rows),
    }


def csv_export(store: Store, confirmed_only: bool = False) -> str:
    data = collection(store, confirmed_only)
    output = io.StringIO(newline="")
    columns = [
        "server",
        "inventory_id",
        "monster_id",
        "proposed_monster_id",
        "quantity",
        "confirmed",
        "status",
        "tracking",
        "confidence",
        "recording_id",
    ]
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in data["monsters"]:
        writer.writerow({"server": "NA", **row, "status": "confirmed", "tracking": "clear"})
    for row in data["unresolved"]:
        writer.writerow({"server": "NA", **row})
    return output.getvalue()
