// Queue membership is independent of recognition confidence and owned-copy identity.
export function needsReview(row) {
  return row.status !== "confirmed" || row.tracking === "ambiguous" ||
    (row.stackable && !row.quantity_verified);
}

export function matchesReview(row, filter) {
  if (!row || ["merged", "rejected"].includes(row.status)) return false;
  if (filter === "identity") return row.status !== "confirmed";
  if (filter === "unmatched") return !row.monster_id && !row.proposed_id;
  if (filter === "tracking") return row.tracking === "ambiguous";
  if (filter === "quantity") return row.stackable && !row.quantity_verified;
  return filter === "all" || needsReview(row);
}

export function orderedEntries(session, order = "box") {
  const positions = new Map((session?.box_map?.slots || []).map(slot => [slot.inventory_id, slot]));
  const rows = (session?.entries || []).filter(row => !["merged", "rejected"].includes(row.status));
  const original = new Map(rows.map((row, index) => [row.id, index]));
  return rows.sort((a, b) => {
    if (order !== "box" && a.confidence !== b.confidence)
      return (a.confidence - b.confidence) * (order === "highest" ? -1 : 1);
    const left = positions.get(a.id), right = positions.get(b.id);
    if (left && right) {
      const geometry = left.segment - right.segment || left.world_y - right.world_y ||
        left.column - right.column;
      if (geometry) return geometry;
    } else if (Boolean(left) !== Boolean(right)) return left ? -1 : 1;
    return original.get(a.id) - original.get(b.id);
  });
}

export function reviewQueue(session, filter, order) {
  return orderedEntries(session, order).filter(row => matchesReview(row, filter));
}

export function nextReviewId(session, currentId, filter, order, direction = 1, previousIds = null) {
  const rows = orderedEntries(session, order);
  const byId = new Map(rows.map(row => [row.id, row]));
  const ids = previousIds || rows.map(row => row.id);
  const index = ids.indexOf(currentId);
  if (index < 0) {
    const queue = reviewQueue(session, filter, order);
    return (direction > 0 ? queue[0] : queue.at(-1))?.id || null;
  }
  for (let step = 1; step <= ids.length; step++) {
    const candidate = ids[(index + direction * step + ids.length) % ids.length];
    if (candidate !== currentId && matchesReview(byId.get(candidate), filter)) return candidate;
  }
  return null;
}
