import { BoxMinimap } from "./minimap.js";
import { needsReview, matchesReview, orderedEntries, reviewQueue, nextReviewId } from "./review-queue.js";

const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="padbox-token"]').content;
const video = $("source");
const state = {
  stream: null,
  crop: [0, 0, 1, 1],
  cropping: false,
  session: null,
  scanning: false,
  request: null,
  timer: null,
  selected: null,
  reconnect: true,
  captureShape: null,
  timeOrigin: 0,
  clockOrigin: 0,
  snapshotImage: null,
  loading: false,
  offline: false,
  diff: null,
  mapBrowse: null,
  batch: null,
  batchBusy: false,
  batchShown: 0,
  reviewBusy: false,
  captureReady: null,
  referenceReady: null,
};
const imageCache = new Map();
const reconstructedNodes = new Map();
let reconstructedSession = null;
const minimap = new BoxMinimap($("box-minimap"), viewport => {
  state.mapBrowse = viewport;
  renderReconstruction();
}, mid => imageCache.get(`/api/icons/${mid}`));

async function api(path, body) {
  const options = { headers: { "X-PADBox-Token": token } };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    state.offline = true;
    $("connection").textContent = "Live server unreachable";
    throw new Error(
      `Cannot reach the PADBox live server at ${location.origin}. ` +
        "Run 'padbox live' in a terminal and keep it open, then refresh this page and select the preview again. " +
        "Saved collection entries are retained.",
    );
  }
  state.offline = false;
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}
function notice(message, kind = "") {
  $("notice").textContent = message;
  $("notice").className = `notice ${kind}`;
}
function handle(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (error) {
      notice(error.message, "error");
    }
  };
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
async function localImage(url, image) {
  if (!url) {
    image.hidden = true;
    return;
  }
  image.hidden = false;
  image.dataset.url = url;
  try {
    if (!imageCache.has(url))
      imageCache.set(
        url,
        fetch(url, { headers: { "X-PADBox-Token": token } })
          .then(async (response) => {
            if (!response.ok) throw new Error("Reference image unavailable");
            return URL.createObjectURL(await response.blob());
          })
          .catch((error) => {
            imageCache.delete(url);
            throw error;
          }),
      );
    const blob = await imageCache.get(url);
    if (image.dataset.url === url) image.src = blob;
    return image.dataset.url === url;
  } catch {
    if (image.dataset.url === url) {
      image.removeAttribute("src");
      image.alt = "Image unavailable locally";
    }
    return false;
  }
}
function download(content, name, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = element("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function controls() {
  const session = state.session;
  const open = session && session.status !== "complete";
  const busy = state.scanning || state.loading;
  $("choose").disabled = busy;
  $("disconnect").disabled = !state.stream;
  $("crop").disabled = !state.stream || Boolean(open);
  $("reset-crop").disabled = !state.stream || Boolean(open);
  $("start").disabled =
    !state.stream || busy || state.cropping || Boolean(open);
  $("load").disabled = busy;
  $("sessions").disabled = busy;
  $("resume").disabled = !state.stream || !open || busy || state.cropping;
  $("pause").disabled = !busy;
  $("finish").disabled = !open || state.loading;
  $("layout").disabled = Boolean(open);
  $("layout-file").disabled = Boolean(open);
  const editable = session && session.snapshot_status === "draft";
  for (const id of ["accept", "reject", "merge", "save-quantity", "add-entry"])
    $(id).disabled = !editable || state.reviewBusy;
  for (const id of ["review-filter", "review-order", "review-auto-next"])
    $(id).disabled = state.reviewBusy;
  for (const id of ["search", "search-results", "monster-id", "entry-quantity", "distinct", "merge-search", "merge-target"])
    $(id).disabled = !editable || state.reviewBusy;
  for (const button of $("alternatives").querySelectorAll("button"))
    button.disabled = state.reviewBusy;
  const filter = $("review-filter").value, order = $("review-order").value;
  const queue = reviewQueue(session, filter, order);
  $("review").setAttribute("aria-busy", String(state.reviewBusy));
  $("start-review").disabled = !queue.length || state.reviewBusy || state.loading || state.batchBusy;
  for (const [id, direction] of [["review-previous", -1], ["review-next", 1]])
    $(id).disabled = state.reviewBusy || !nextReviewId(session, state.selected, filter, order, direction);
  if (state.reviewBusy) {
    for (const id of ["load", "sessions", "start", "resume", "finish", "close-review"])
      $(id).disabled = true;
  } else $("close-review").disabled = false;
  $("queue-summary").textContent = `${queue.length} entries in this review queue. ` +
    `${(session?.entries || []).filter(row => matchesReview(row, "pending")).length} entries need review overall.`;
  $("preview-diff").disabled = !session || session.snapshot_status === "legacy" || busy || state.reviewBusy;
  $("apply-scan").disabled = !state.diff?.can_apply || !$("full-box").checked || busy || state.reviewBusy;
  const batchReady = editable && !busy && session.status !== "running" && !state.batchBusy && !state.reviewBusy;
  const pendingStacks = reviewQueue(session, "quantity", "box").length;
  $("stack-quantity-one").disabled = !batchReady || !pendingStacks;
  $("stack-quantity-summary").textContent = `${pendingStacks} pending stack quantities`;
  $("batch-preview").disabled = !batchReady;
  $("batch-score").disabled = !batchReady;
  $("batch-margin").disabled = !batchReady;
  $("batch-accept").disabled = !batchReady || !state.batch?.eligible_count;
  if (state.batchBusy) {
    $("load").disabled = $("sessions").disabled = $("resume").disabled = true;
  }
  $("scan-state").textContent = busy
    ? "Scanning"
    : session?.status === "complete"
      ? session.snapshot_status === "applied" ? "Applied" : "Draft ready"
      : "Paused";
  $("connection").textContent = state.offline
    ? "Live server unreachable"
    : state.stream
      ? "Preview connected · Local only"
      : "Local server connected";
}
async function refreshSessions() {
  const previous = $("sessions").value;
  const sessions = await api("/api/sessions");
  $("sessions").replaceChildren(new Option("Choose a saved session", ""));
  for (const session of sessions)
    $("sessions").append(
      new Option(
        `${session.name} · ${session.active_entries} entries · ${session.status}`,
        session.id,
      ),
    );
  $("sessions").value = state.session?.id || previous;
}
function render(session) {
  if (state.session?.id !== session.id) state.selected = null;
  state.session = session;
  clearBatch();
  $("scan-state").dataset.sessionId = session.id;
  state.diff = null;
  $("full-box").checked = false;
  $("diff-table").replaceChildren();
  $("diff-summary").textContent = session.snapshot_status === "applied"
    ? "This snapshot was applied and is read-only. Start a new full scan to update your box."
    : "Preview changes after reviewing the draft. Your saved box is unchanged.";
  const sessionOption = [...$("sessions").options].find(
    (option) => option.value === session.id,
  );
  if (sessionOption)
    sessionOption.textContent = `${session.name} · ${session.active_entries} entries · ${session.status}`;
  const counts = [
    ["Detected slots", session.active_entries],
    ["Confirmed", session.confirmed_entries],
    ["Needs identity review", session.uncertain_entries],
    ["Unmatched", session.unmatched_entries],
    ["Slot ambiguities", session.ambiguous_entries],
  ];
  $("metrics").replaceChildren(
    ...counts.map(([label, value]) => {
      const node = element("div", undefined, "metric");
      node.append(element("b", value), element("span", label));
      return node;
    }),
  );
  renderEntries();
  renderBoxes();
  renderReconstruction();
  renderSelection();
  controls();
}
function renderEntries() {
  const session = state.session;
  if (!session) return;
  const pending = $("unreviewed").checked;
  const rows = orderedEntries(session, $("review-order").value).filter(
    (row) =>
      row.status !== "merged" &&
      row.status !== "rejected" &&
      (!pending || row.status !== "confirmed" || row.tracking === "ambiguous" ||
        (row.stackable && !row.quantity_verified)),
  );
  const nodes = rows.map((row) => {
    const button = element(
      "button",
      undefined,
      row.id === state.selected ? "selected" : "",
    );
    button.dataset.inventoryId = row.id;
    const image = element("img");
    image.alt = "Inventory crop";
    localImage(`${row.crop_url}?v=${row.confidence}`, image);
    const label = element(
      "span",
      `#${row.monster_id || row.proposed_id || "?"} ${row.name} · ${row.auto_accepted ? "auto-accepted" : row.status} · x${row.quantity}`,
    );
    button.append(image, label);
    button.addEventListener(
      "click",
      handle(() => selectEntry(row.id)),
    );
    return button;
  });
  $("entry-list").replaceChildren(...nodes);
  if (!rows.length)
    $("entry-list").append(
      element("p", "No entries currently need review.", "muted"),
    );
}
function renderBoxes() {
  const session = state.session;
  const dimensions = session?.dimensions;
  $("boxes").replaceChildren();
  if (!dimensions || !state.snapshotImage) return;
  const [height, width] = dimensions;
  const byId = new Map(session.entries.map((row) => [row.id, row]));
  for (const cell of session.result?.cells || []) {
    const row = byId.get(cell.inventory_id);
    if (!row || ["rejected", "merged"].includes(row.status)) continue;
    const [x, y, w, h] = cell.rect;
    const box = element(
      "button",
      undefined,
      `box ${row.status === "confirmed" ? "confirmed" : ""} ${row.tracking === "ambiguous" ? "ambiguous" : ""}`,
    );
    Object.assign(box.style, {
      left: `${(100 * x) / width}%`,
      top: `${(100 * y) / height}%`,
      width: `${(100 * w) / width}%`,
      height: `${(100 * h) / height}%`,
    });
    const text = `#${row.monster_id || row.proposed_id || "?"} · ${row.confidence.toFixed(2)}`;
    const label = element("span", text);
    label.append(element("small", row.name));
    box.append(label);
    box.title = row.name;
    box.setAttribute("aria-label", `Review ${row.name} ${text}`);
    box.addEventListener(
      "click",
      handle(() => selectEntry(row.id)),
    );
    $("boxes").append(box);
  }
}
function placeCell(node, rect, dimensions) {
  const [height, width] = dimensions;
  const [x, y, w, h] = rect;
  Object.assign(node.style, {
    left: `${100 * x / width}%`, top: `${100 * y / height}%`,
    width: `${100 * w / width}%`, height: `${100 * h / height}%`,
  });
}
function renderReconstruction() {
  const session = state.session;
  const dimensions = session?.dimensions;
  const map = session?.box_map;
  // A proven join can remove a section while the user is browsing it.
  if (state.mapBrowse && !map?.segments.some(s => s.id === state.mapBrowse.segment))
    state.mapBrowse = null;
  let cells = dimensions && state.snapshotImage ? session.result?.cells || [] : [];
  if (reconstructedSession !== session?.id) {
    reconstructedNodes.clear();
    $("reconstructed-cells").replaceChildren();
    reconstructedSession = session?.id;
    state.mapBrowse = null;
  }
  const [height] = dimensions || [1, 1];
  const roi = session?.config.layout.roi || [0, 0, 1, 1];
  const viewHeight = (roi[3] - roi[1]) * height;
  // A loaded session can browse its persistent map without a live source or frame.
  if (!state.snapshotImage && !state.mapBrowse && !state.loading && !state.scanning &&
      map?.viewport && map.slots?.length)
    state.mapBrowse = {...map.viewport};
  const browsing = state.mapBrowse;
  if (browsing && dimensions) {
    cells = (map?.slots || []).filter(slot => slot.segment === browsing.segment &&
      slot.world_y + slot.rect[3] > browsing.top && slot.world_y < browsing.bottom)
      .map(slot => ({inventory_id: slot.inventory_id,
        rect: [slot.rect[0], slot.world_y - browsing.top + roi[1] * height,
          slot.rect[2], slot.rect[3]]}));
  }
  if (dimensions) {
    $("workspace").style.setProperty("--phone-ratio", dimensions[1] / dimensions[0]);
  }
  $("reconstructed-empty").hidden = cells.length > 0;
  $("reconstructed-empty").textContent = session?.result?.status === "no_grid"
    ? "No box grid in this frame. Check the phone crop before resuming."
    : "Start or resume capture to show resolved icons alongside the phone.";
  if (browsing) $("reconstructed-empty").textContent = "No mapped slots in this window.";
  $("follow-live").hidden = !browsing;
  $("follow-live").disabled = !state.snapshotImage;
  const direction = {up: "↑ Toward earlier rows", down: "↓ Toward later rows",
    stationary: "• Stationary", unknown: "Direction unknown"}[map?.direction] || "";
  const position = {relocalized: "Known section found", revisited: "Revisiting mapped rows",
    extended: "Mapping new rows", unlocated: "Unjoined section — counts need review",
    no_grid: "Waiting for grid"}[map?.position] || "Map grows as you scan";
  $("map-position").textContent = browsing ? "Browsing saved rows · capture position stays cyan"
    : [direction, position].filter(Boolean).join(" · ");
  $("map-position").dataset.direction = map?.direction || "unknown";
  $("reconstructed-wrap").classList.toggle("browsing-map", Boolean(browsing));
  $("reconstructed-meta").textContent = session
    ? `${session.active_entries} ${session.ambiguous_entries ? "tracked slots (count provisional)" : "slots scanned"} · ${session.confirmed_entries} accepted`
    : "Waiting for capture";
  $("reconstructed-timing").textContent = browsing
    ? `Section ${browsing.segment + 1} · browsing ${cells.length} mapped slots · ${map?.known_slots || 0} total mapped slots. This does not move the iPhone.`
    : cells.length
    ? `${cells.length} visible slots · frame ${session.last_sequence + 1} · ${session.last_timestamp.toFixed(2)}s · ${session.result.processing_seconds ?? 0}s processing`
    : "Aligned to the latest processed frame; no rows are invented or filled in.";
  const byId = new Map((session?.entries || []).map(row => [row.id, row]));
  const visible = new Set();
  $("reconstructed-wrap").classList.toggle("tracking-gap", session?.result?.status === "tracking_warning");
  const firstY = cells.length ? Math.min(...cells.map(cell => cell.rect[1])) : 0;
  document.querySelector(".box-context").style.height = dimensions
    ? `${100 * firstY / dimensions[0]}%` : "0";
  for (const cell of cells) {
    const row = byId.get(cell.inventory_id);
    if (!row || ["rejected", "merged"].includes(row.status)) continue;
    visible.add(row.id);
    let tile = reconstructedNodes.get(row.id);
    if (!tile) {
      tile = element("button", undefined, "reconstructed-card");
      tile.type = "button";
      tile.dataset.inventoryId = row.id;
      const image = element("img");
      const label = element("span", undefined, "card-id");
      const badge = element("span", undefined, "card-status");
      tile.append(image, label, badge);
      tile.addEventListener("click", handle(() => selectEntry(row.id)));
      reconstructedNodes.set(row.id, tile);
      $("reconstructed-cells").append(tile);
    }
    const mid = row.monster_id || row.proposed_id;
    const accepted = row.status === "confirmed";
    tile.classList.toggle("accepted", accepted);
    tile.classList.toggle("proposed", !accepted && Boolean(mid));
    tile.classList.toggle("unmatched", !mid);
    tile.classList.toggle("ambiguous", row.tracking === "ambiguous");
    const needsQuantity = row.stackable && !row.quantity_verified;
    tile.classList.toggle("needs-quantity", needsQuantity);
    tile.dataset.monsterId = mid || "";
    const status = row.tracking === "ambiguous" ? "Copy tracking needs review"
      : !mid ? "Unmatched" : needsQuantity ? "Stack quantity needs review"
      : accepted ? "Accepted" : "Identity needs review";
    tile.setAttribute("aria-label", `Review ${mid ? `#${mid} ${row.name}` : "unmatched slot"} · ${status}`);
    tile.title = `${mid ? `#${mid} ${row.name}` : "Unmatched"} · ${status} · similarity ${row.confidence.toFixed(3)} · x${row.quantity}`;
    tile.querySelector(".card-id").textContent = mid ? `#${mid}${row.quantity > 1 ? ` ×${row.quantity}` : ""}` : "Unmatched";
    tile.querySelector(".card-status").textContent = row.tracking === "ambiguous" ? "↔?"
      : !mid ? "?" : needsQuantity ? "×?" : accepted ? "✓" : "?";
    tile.querySelector(".card-status").title = status;
    const source = mid ? `/api/icons/${mid}` : `${row.crop_url}?v=${row.confidence}`;
    const imageKey = source;
    if (tile.dataset.imageKey !== imageKey) {
      tile.dataset.imageKey = imageKey;
      tile.classList.remove("missing-reference");
      const image = tile.querySelector("img");
      image.removeAttribute("src");
      image.alt = mid ? `Reference #${mid} ${row.name}` : "Unmatched capture";
      localImage(source, image).then(loaded => {
        if (!loaded && mid && tile.dataset.imageKey === imageKey) {
          tile.classList.add("missing-reference");
          image.alt = `Captured slot; reference #${mid} unavailable`;
          localImage(`${row.crop_url}?v=${row.confidence}`, image);
        }
      });
    }
    placeCell(tile, cell.rect, dimensions);
  }
  for (const [id, tile] of reconstructedNodes) {
    if (!visible.has(id)) {
      tile.remove();
      reconstructedNodes.delete(id);
    }
  }
  minimap.update(map, session?.entries || [], viewHeight, browsing);
}
async function selectEntry(id) {
  if (state.reviewBusy || state.batchBusy || state.loading) return;
  state.reviewBusy = true;
  controls();
  try {
    await pauseScan();
    const row = state.session?.entries.find(row => row.id === id);
    if (!row || ["merged", "rejected"].includes(row.status)) return;
    state.selected = id;
    renderEntries();
    renderSelection();
    focusReview();
  } finally {
    state.reviewBusy = false;
    controls();
  }
}
function focusReview() {
  if (!state.selected) return;
  $("review").focus({preventScroll: true});
  $("review-panel-title").scrollIntoView({block: "nearest"});
}
async function moveReview(direction) {
  const id = nextReviewId(state.session, state.selected, $("review-filter").value,
    $("review-order").value, direction);
  if (id) await selectEntry(id);
}
function currentEntry() {
  return state.session?.entries.find((row) => row.id === state.selected);
}
function setIdentity(id, url) {
  $("monster-id").value = id || "";
  state.referenceReady = comparisonImage(url || (id ? `/api/icons/${id}` : null), $("entry-reference"));
  highlightAlternative();
}
async function comparisonImage(url, image) {
  // Never show the previous entry's artwork while a new comparison is loading.
  image.removeAttribute("src");
  if (await localImage(url, image)) {
    try { await image.decode(); } catch {}
  }
}
function highlightAlternative() {
  for (const button of $("alternatives").querySelectorAll("button")) {
    const chosen = button.dataset.monsterId === $("monster-id").value;
    button.classList.toggle("chosen", chosen);
    button.setAttribute("aria-pressed", String(chosen));
  }
}
function renderSelection() {
  clearTimeout(searchTimer);
  clearTimeout(mergeTimer);
  const row = currentEntry();
  $("review").dataset.inventoryId = row?.id || "";
  $("review").hidden = !row;
  $("reconstruction").hidden = Boolean(row);
  $("close-review").hidden = !row;
  $("review-panel-title").textContent = row ? "Review this entry" : "Reconstructed monster box";
  $("review-help").textContent = row
    ? "Compare the capture and reference. Corrections save to this draft."
    : "Reference icons follow the captured rows. Click any icon to review.";
  if (!row) return;
  const queue = reviewQueue(state.session, $("review-filter").value, $("review-order").value);
  const position = queue.findIndex(item => item.id === row.id);
  $("review-progress").textContent = position < 0 ? `Outside queue · ${queue.length} queued`
    : `${position + 1} of ${queue.length} in queue`;
  $("review-remaining").textContent = needsReview(row)
    ? "Resolve this entry, or use Next to skip it without saving."
    : "This entry is resolved. Next opens another queued entry.";
  $("entry-title").textContent =
    `#${row.monster_id || row.proposed_id || "?"} ${row.name}`;
  $("entry-meta").textContent =
    `${row.status}${row.auto_accepted ? " (strong match accepted automatically)" : ""} · similarity ${row.confidence.toFixed(3)} · source ${row.timestamp?.toFixed(2) ?? "?"}s · ${row.id.slice(0, 8)}`;
  $("entry-quantity").value = row.quantity;
  state.captureReady = comparisonImage(`${row.crop_url}?v=${row.confidence}`, $("entry-crop"));
  const id = row.monster_id || row.proposed_id;
  setIdentity(id, id ? `/api/icons/${id}` : row.candidates[0]?.icon_url);
  $("distinct").checked = false;
  $("distinct-label").hidden = row.tracking !== "ambiguous";
  const warnings = [];
  if (row.tracking === "ambiguous")
    warnings.push(
      `Slot identity: ${row.tracking_reason || "possible duplicate view"}. Verify a separate copy or merge.`,
    );
  if (!row.high_confidence && row.status !== "confirmed")
    warnings.push(
      "Uncertain similarity: compare the actual artwork and exact form before accepting.",
    );
  if (row.stackable && !row.quantity_verified)
    warnings.push(
      "Stackable material: enter and verify its displayed xN quantity below before applying.",
    );
  $("entry-warning").textContent = warnings.join(" ");
  $("entry-warning").hidden = warnings.length === 0;
  $("alternatives").replaceChildren(
    ...row.candidates.map((candidate, index) => {
      const button = element("button");
      button.type = "button";
      button.dataset.monsterId = candidate.monster_id;
      const image = element("img");
      image.alt = candidate.name;
      localImage(candidate.icon_url, image);
      button.append(
        image,
        element(
          "span",
          `#${candidate.monster_id} · ${candidate.score.toFixed(3)}`,
        ),
      );
      button.title = `${candidate.name} · choose with ${index + 1}, then A to accept`;
      button.prepend(element("kbd", index + 1));
      button.addEventListener("click", () =>
        setIdentity(candidate.monster_id, candidate.icon_url),
      );
      return button;
    }),
  );
  highlightAlternative();
  $("search").value = "";
  $("search-results").replaceChildren(new Option("Select a search result", ""));
  $("merge-target").replaceChildren();
  $("merge-crop").hidden = true;
}
async function decision(action, extra = {}) {
  if (state.reviewBusy || state.batchBusy) return;
  const row = currentEntry();
  if (!row) throw new Error("Choose an inventory entry first");
  const previousIds = orderedEntries(state.session, $("review-order").value).map(row => row.id);
  state.reviewBusy = true;
  controls();
  try {
    if (action === "accept") await Promise.all([state.captureReady, state.referenceReady]);
    await pauseScan();
    const result = await api(`/api/sessions/${state.session.id}/review`, {
      inventory_id: row.id,
      action,
      ...extra,
    });
    const updated = result.entries.find(item => item.id === row.id);
    const removed = !updated || ["merged", "rejected"].includes(updated.status);
    const resolved = removed || !needsReview(updated);
    const leavesQueue = !matchesReview(updated, $("review-filter").value);
    if ((resolved || leavesQueue) && $("review-auto-next").checked) {
      state.selected = nextReviewId(result, row.id, $("review-filter").value,
        $("review-order").value, 1, previousIds);
    } else if (removed) state.selected = null;
    render(result);
    focusReview();
    if (!resolved && state.selected === row.id) {
      const remaining = [];
      if (updated.status !== "confirmed") remaining.push("identity");
      if (updated.tracking === "ambiguous") remaining.push("copy tracking");
      if (updated.stackable && !updated.quantity_verified) remaining.push("stack quantity");
      $("review-remaining").textContent = `Saved · still needs ${remaining.join(" and ")}.`;
    }
    const remaining = reviewQueue(result, $("review-filter").value, $("review-order").value).length;
    notice(state.selected ? "Decision saved. Review is ready for your next action."
      : remaining ? `Decision saved. ${remaining} queued entries remain; press R to continue.`
      : "Decision saved. No more entries in this queue.", "good");
  } finally {
    state.reviewBusy = false;
    controls();
  }
}
function updateCropOverlay() {
  const canvas = $("crop-overlay");
  if (!video.videoWidth) return;
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  const [x1, y1, x2, y2] = state.crop;
  context.fillStyle = "#00101955";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.clearRect(
    x1 * canvas.width,
    y1 * canvas.height,
    (x2 - x1) * canvas.width,
    (y2 - y1) * canvas.height,
  );
  context.strokeStyle = "#69efbc";
  context.lineWidth = Math.max(2, canvas.width / 250);
  context.strokeRect(
    x1 * canvas.width,
    y1 * canvas.height,
    (x2 - x1) * canvas.width,
    (y2 - y1) * canvas.height,
  );
}
async function chooseFeed() {
  if (!navigator.mediaDevices?.getDisplayMedia)
    throw new Error(
      "Window capture is unavailable. Open the displayed localhost URL in Chrome or Edge.",
    );
  // Must be invoked directly from this user click; browsers require fresh permission each time.
  const stream = await navigator.mediaDevices.getDisplayMedia({
    video: { frameRate: 10 },
    audio: false,
  });
  if (state.stream) await disconnect();
  state.stream = stream;
  state.crop =
    state.session?.status !== "complete"
      ? state.session?.config.capture_crop || [0, 0, 1, 1]
      : [0, 0, 1, 1];
  state.captureShape = null;
  state.reconnect = true;
  video.srcObject = stream;
  await video.play();
  stream.getVideoTracks()[0].addEventListener("ended", () => {
    handle(disconnect)();
  });
  $("capture-details").open = true;
  notice(
    "Preview connected. Crop around the complete iPhone screen, then start or resume a scan.",
  );
  updateCropOverlay();
  controls();
}
async function disconnect() {
  await pauseScan();
  const stream = state.stream;
  state.stream = null;
  if (stream) for (const track of stream.getTracks()) track.stop();
  video.srcObject = null;
  state.reconnect = true;
  controls();
  notice("Preview disconnected. Your session and corrections are saved.");
}
$("crop").addEventListener("click", () => {
  state.cropping = true;
  $("capture-details").open = true;
  $("crop-overlay").classList.add("selecting");
  notice("Drag around the full phone screen, excluding window borders.");
  controls();
});
let drag = null;
function pointer(event) {
  const rect = $("crop-overlay").getBoundingClientRect();
  return [
    Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
    Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
  ];
}
$("crop-overlay").addEventListener("pointerdown", (event) => {
  if (!state.cropping) return;
  drag = pointer(event);
  $("crop-overlay").setPointerCapture(event.pointerId);
});
$("crop-overlay").addEventListener("pointermove", (event) => {
  if (!drag) return;
  const point = pointer(event);
  state.crop = [
    Math.min(drag[0], point[0]),
    Math.min(drag[1], point[1]),
    Math.max(drag[0], point[0]),
    Math.max(drag[1], point[1]),
  ];
  updateCropOverlay();
});
$("crop-overlay").addEventListener("pointerup", () => {
  if (!drag) return;
  drag = null;
  if (
    state.crop[2] - state.crop[0] < 0.05 ||
    state.crop[3] - state.crop[1] < 0.05
  ) {
    state.crop = [0, 0, 1, 1];
    notice("Crop too small; drag a larger rectangle.", "error");
  } else
    notice(
      "Phone-screen crop selected. Start a scan when your box is visible.",
      "good",
    );
  state.cropping = false;
  $("crop-overlay").classList.remove("selecting");
  updateCropOverlay();
  controls();
});
$("reset-crop").addEventListener("click", () => {
  state.crop = [0, 0, 1, 1];
  updateCropOverlay();
});
function captureFrame() {
  if (!state.stream || video.readyState < 2 || !video.videoWidth)
    throw new Error("Preview is not ready. Reconnect the window.");
  const shape = `${video.videoWidth}x${video.videoHeight}`;
  if (state.captureShape && state.captureShape !== shape)
    throw new Error(
      "Preview window size changed. Pause and restore the previous framing before resuming.",
    );
  state.captureShape = shape;
  const [x1, y1, x2, y2] = state.crop;
  const sw = (x2 - x1) * video.videoWidth,
    sh = (y2 - y1) * video.videoHeight;
  const canvas = document.createElement("canvas");
  // Consistent dimensions and enough detail for the calibrated iPhone layout.
  canvas.width = Math.min(1206, Math.round(sw));
  canvas.height = Math.round((sh * canvas.width) / sw);
  canvas
    .getContext("2d")
    .drawImage(
      video,
      x1 * video.videoWidth,
      y1 * video.videoHeight,
      sw,
      sh,
      0,
      0,
      canvas.width,
      canvas.height,
    );
  return canvas.toDataURL("image/jpeg", 0.9);
}
async function scanStep() {
  if (!state.scanning || state.request) return;
  const sessionId = state.session.id;
  try {
    const image = captureFrame();
    const timestamp =
      state.timeOrigin + (performance.now() - state.clockOrigin) / 1000;
    state.request = api(`/api/sessions/${sessionId}/frame`, {
      sequence: state.session.last_sequence + 1,
      timestamp,
      jpeg: image.split(",")[1],
    });
    const session = await state.request;
    state.request = null;
    state.snapshotImage = image;
    $("processed").src = image;
    $("processed").hidden = false;
    $("empty-preview").hidden = true;
    render(session);
    const result = session.result || {};
    const warnings = [...(result.warnings || [])];
    const visibleIds = new Set(
      (result.cells || []).map((cell) => cell.inventory_id),
    );
    const uncertain = session.entries.filter(
      (row) =>
        visibleIds.has(row.id) &&
        row.status !== "confirmed" &&
        !["rejected", "merged"].includes(row.status),
    ).length;
    if (uncertain)
      warnings.push(
        `${uncertain} visible identities need acceptance; click an icon to inspect`,
      );
    const copyIssues = session.entries.filter(row => visibleIds.has(row.id) &&
      row.tracking === "ambiguous" && !["rejected", "merged"].includes(row.status)).length;
    if (copyIssues) warnings.push(`${copyIssues} visible slots need copy-count review after capture`);
    $("timing").textContent =
      `${result.cells?.length || 0} visible cells · ${result.processing_seconds ?? 0}s processing · ${session.counts.observations || 0} saved observations`;
    const problem = ["no_grid", "tracking_warning"].includes(result.status);
    if (problem && !$("auto-pause").checked)
      warnings.push("Continuing capture; review tracking gaps after finishing.");
    notice(
      [result.message, ...warnings].filter(Boolean).join(" · "),
      problem || uncertain ? "" : "good",
    );
    if (problem && $("auto-pause").checked) {
      await pauseScan();
      notice(
        `${result.message}. Scanning paused; check overlap and review before resuming.`,
      );
    }
  } catch (error) {
    state.request = null;
    state.scanning = false;
    clearTimeout(state.timer);
    controls();
    try {
      render(
        await api(`/api/sessions/${sessionId}/control`, { action: "pause" }),
      );
    } catch {}
    notice(`${error.message} Your saved entries are retained.`, "error");
  } finally {
    if (state.scanning) state.timer = setTimeout(scanStep, 300);
  }
}
async function resumeScan() {
  if (!state.stream) throw new Error("Choose the iPhone preview window first");
  state.loading = true;
  controls();
  notice("Loading local references and resuming capture…");
  try {
    const session = await api(`/api/sessions/${state.session.id}/control`, {
      action: "resume",
      discontinuity: state.reconnect && state.session.last_sequence >= 0,
    });
    state.reconnect = false;
    state.selected = null;
    state.mapBrowse = null;
    render(session);
    state.timeOrigin = Math.max(0, session.last_timestamp + 0.01);
    state.clockOrigin = performance.now();
    state.scanning = true;
    $("capture-details").open = false;
    document.body.classList.add("capturing");
  } finally {
    state.loading = false;
    controls();
  }
  $("workspace").scrollIntoView({ block: "start" });
  scanStep();
}

async function pauseScan() {
  const wasScanning = state.scanning;
  state.scanning = false;
  clearTimeout(state.timer);
  if (state.request) {
    try {
      await state.request;
    } catch {}
  }
  if (wasScanning && state.session) {
    render(
      await api(`/api/sessions/${state.session.id}/control`, {
        action: "pause",
      }),
    );
  }
  document.body.classList.remove("capturing");
  controls();
}
$("choose").addEventListener("click", handle(chooseFeed));
$("follow-live").addEventListener("click", () => {
  state.mapBrowse = null;
  renderReconstruction();
});
function closeReview() {
  if (state.reviewBusy) return;
  state.selected = null;
  renderEntries();
  renderSelection();
  controls();
  $("workspace").scrollIntoView({ block: "start" });
}
$("close-review").addEventListener("click", closeReview);
window.addEventListener("keydown", event => {
  if (event.defaultPrevented || event.isComposing || event.repeat || event.ctrlKey ||
      event.metaKey || event.altKey || state.reviewBusy || state.batchBusy || state.loading) return;
  const target = event.target instanceof Element ? event.target : null;
  const typing = target?.closest("input, textarea, select, [contenteditable]:not([contenteditable=false]), [role=textbox]");
  if (event.key === "Escape" && state.selected) {
    event.preventDefault();
    if (typing) focusReview();
    else closeReview();
    return;
  }
  if (typing || target?.closest("[role=slider]")) return;
  const key = event.key.toLowerCase();
  if (key === "r" && !state.selected) {
    event.preventDefault();
    $("start-review").click();
    return;
  }
  const row = currentEntry();
  if (!row) return;
  if (["j", "arrowright", "k", "arrowleft"].includes(key)) {
    event.preventDefault();
    handle(() => moveReview(["k", "arrowleft"].includes(key) ? -1 : 1))();
    return;
  }
  if (["1", "2", "3"].includes(key)) {
    event.preventDefault();
    const candidate = row.candidates[Number(key) - 1];
    if (candidate) setIdentity(candidate.monster_id, candidate.icon_url);
    return;
  }
  const fields = {"/": "search", i: "monster-id", q: "entry-quantity"};
  if (fields[key]) {
    event.preventDefault();
    $(fields[key]).focus();
    $(fields[key]).select?.();
    return;
  }
  if (!["a", "x", "v", "d"].includes(key)) return;
  event.preventDefault();
  if (state.session.snapshot_status !== "draft") {
    notice("Applied snapshots are read-only. Start a new draft to make changes.");
    return;
  }
  if (key === "a") handle(acceptIdentity)();
  if (key === "x") handle(() => decision("reject"))();
  if (key === "v") handle(verifyQuantity)();
  if (key === "d" && row.tracking === "ambiguous") {
    $("distinct").checked = !$("distinct").checked;
    notice($("distinct").checked ? "Separate-copy verification selected; A saves the identity."
      : "Separate-copy verification cleared.");
  }
});
$("start-review").addEventListener("click", handle(async () => {
  const row = reviewQueue(state.session, $("review-filter").value, $("review-order").value)[0];
  if (row) await selectEntry(row.id);
}));
$("review-next").addEventListener("click", handle(() => moveReview(1)));
$("review-previous").addEventListener("click", handle(() => moveReview(-1)));
for (const id of ["review-filter", "review-order"])
  $(id).addEventListener("change", handle(async () => {
    const queue = reviewQueue(state.session, $("review-filter").value, $("review-order").value);
    if (state.selected && !queue.some(row => row.id === state.selected) && queue.length)
      await selectEntry(queue[0].id);
    else {
      renderEntries();
      renderSelection();
      controls();
    }
  }));
$("disconnect").addEventListener("click", handle(disconnect));
$("pause").addEventListener(
  "click",
  handle(async () => {
    await pauseScan();
    notice(
      "Paused. Keep the phone on the same rows while correcting identities.",
    );
  }),
);
$("resume").addEventListener("click", handle(resumeScan));
$("start").addEventListener(
  "click",
  handle(async () => {
    state.loading = true;
    controls();
    try {
      await api("/health");
      await pauseScan();
      if (state.session && state.session.status !== "complete")
        throw new Error(
          "Finish the current session before starting another scan.",
        );
      const layout = JSON.parse($("layout").value);
      const session = await api("/api/sessions", {
        layout,
        capture_crop: state.crop,
        name: $("session-name").value || "Live box scan",
      });
      state.selected = null;
      state.snapshotImage = null;
      $("processed").hidden = true;
      state.captureShape = null;
      render(session);
      await refreshSessions();
      await resumeScan();
    } finally {
      state.loading = false;
      controls();
    }
  }),
);
$("finish").addEventListener(
  "click",
  handle(async () => {
    await pauseScan();
    render(
      await api(`/api/sessions/${state.session.id}/control`, {
        action: "finish",
      }),
    );
    await refreshSessions();
    notice(
      "Capture finished. Review remaining entries, then Preview changes before replacing your box.",
      "good",
    );
  }),
);
$("load").addEventListener(
  "click",
  handle(async () => {
    const id = $("sessions").value;
    if (!id) throw new Error("Choose a saved session");
    await pauseScan();
    const session = await api(`/api/sessions/${id}`);
    state.selected = null;
    state.snapshotImage = null;
    state.reconnect = true;
    state.crop = session.config.capture_crop || [0, 0, 1, 1];
    updateCropOverlay();
    $("processed").hidden = true;
    $("boxes").replaceChildren();
    $("empty-preview").hidden = false;
    $("layout").value = JSON.stringify(session.config.layout, null, 2);
    render(session);
    notice(
      "Session loaded. Reconnect and restore the original phone crop before resuming.",
    );
  }),
);
function acceptIdentity() {
  const id = Number($("monster-id").value);
  if (!Number.isInteger(id) || id < 1) throw new Error("Choose a valid monster ID");
  return decision("accept", {monster_id: id, distinct: $("distinct").checked});
}
$("accept").addEventListener("click", handle(acceptIdentity));
$("reject").addEventListener(
  "click",
  handle(() => decision("reject")),
);
function verifyQuantity() {
  const quantity = Number($("entry-quantity").value);
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > 10000)
    throw new Error("Enter a whole quantity between 1 and 10000");
  return decision("quantity", { quantity });
}
$("save-quantity").addEventListener("click", handle(verifyQuantity));
$("stack-quantity-one").addEventListener("click", handle(async () => {
  if (state.reviewBusy || state.batchBusy || state.scanning || !state.session) return;
  const sessionId = state.session.id;
  const selected = state.selected;
  const previousIds = orderedEntries(state.session, $("review-order").value).map(row => row.id);
  state.reviewBusy = true;
  controls();
  try {
    const result = await api(`/api/scans/${sessionId}/stack-quantity-one`, {});
    const session = await api(`/api/sessions/${sessionId}`);
    const updated = session.entries.find(row => row.id === selected);
    if (selected && $("review-auto-next").checked &&
        (!updated || !needsReview(updated) || !matchesReview(updated, $("review-filter").value)))
      state.selected = nextReviewId(session, selected, $("review-filter").value,
        $("review-order").value, 1, previousIds);
    render(session);
    focusReview();
    notice(`${result.updated_count} pending stack quantities set to 1. ` +
      "Quantity checks resolved; preview changes again before applying.", "good");
  } finally {
    state.reviewBusy = false;
    controls();
  }
}));
$("add-entry").addEventListener("click", handle(async () => {
  await pauseScan();
  await api(`/api/scans/${state.session.id}/add`, {
    monster_id: Number($("add-id").value), quantity: Number($("add-count").value),
  });
  render(await api(`/api/sessions/${state.session.id}`));
  notice("Missed entry added to this draft. Preview changes again before applying.", "good");
}));
function clearBatch() {
  state.batch = null;
  state.batchShown = 0;
  $("batch-samples").replaceChildren();
  $("batch-bands").replaceChildren();
  $("batch-accept").textContent = "Accept qualifying identities";
  $("batch-summary").textContent = "Choose thresholds, then preview qualifying matches.";
  $("batch-accept").disabled = true;
}
function batchPolicy() {
  const min_score = Number($("batch-score").value);
  const min_margin = Number($("batch-margin").value);
  if (!$("batch-score").value || !Number.isFinite(min_score) || min_score < 0 || min_score > 1 ||
      !$("batch-margin").value || !Number.isFinite(min_margin) || min_margin < 0 || min_margin > 1)
    throw new Error("Similarity and the lead must each be 0–1.00.");
  return {min_score, min_margin};
}
function showBatchSamples() {
  const preview = state.batch;
  if (!preview) return;
  $("batch-more")?.remove();
  const stop = Math.min(state.batchShown + 24, preview.entries.length);
  for (const row of preview.entries.slice(state.batchShown, stop)) {
    const button = element("button", undefined, "batch-sample");
    button.type = "button";
    const images = element("div", undefined, "batch-sample-images");
    const crop = element("img");
    crop.alt = `Captured #${row.monster_id}`;
    const reference = element("img");
    reference.alt = `Reference #${row.monster_id}`;
    localImage(`/api/crops/${row.inventory_id}?v=${row.score}`, crop);
    localImage(`/api/icons/${row.monster_id}`, reference);
    images.append(crop, reference);
    button.append(images, element("span", `#${row.monster_id} ${row.name}`),
      element("small", `Similarity ${row.score.toFixed(3)} · lead ${row.margin.toFixed(3)}`));
    if (row.needs_quantity) button.append(element("small", "Stack quantity still needs verification"));
    button.addEventListener("click", handle(() => selectEntry(row.inventory_id)));
    $("batch-samples").append(button);
  }
  state.batchShown = stop;
  if (stop < preview.entries.length) {
    const more = element("button", `Show more (${stop} of ${preview.entries.length} shown)`);
    more.id = "batch-more";
    more.addEventListener("click", showBatchSamples);
    $("batch-samples").append(more);
  }
}
async function previewBatch() {
  if (state.batchBusy) return;
  const policy = batchPolicy();
  const sessionId = state.session.id;
  clearBatch();
  state.batchBusy = true;
  controls();
  try {
    const preview = await api(`/api/scans/${sessionId}/batch-preview`, policy);
    state.batch = preview;
    const skipped = Object.entries(preview.skipped).map(([key, count]) =>
      `${count} ${key.replaceAll("_", " ")}`).join(" · ");
    $("batch-summary").textContent = `${preview.eligible_count} identities qualify · ` +
      `${preview.remaining_identity_count} identities remain for review after acceptance. ` +
      (preview.quantity_checks_remaining ? `${preview.quantity_checks_remaining} qualifying stack quantities still need verification. ` : "") +
      `Excluded: ${skipped || "none"}.`;
    $("batch-accept").textContent = `Accept ${preview.eligible_count} qualifying identities`;
    for (const band of preview.score_bands) {
      const button = element("button", `≥${band.min_score.toFixed(2)}: ${band.count}`);
      button.type = "button";
      button.title = "Preview at this similarity, keeping the selected alternative lead";
      button.addEventListener("click", handle(async () => {
        $("batch-score").value = band.min_score.toFixed(2);
        await previewBatch();
      }));
      $("batch-bands").append(button);
    }
    showBatchSamples();
  } finally {
    state.batchBusy = false;
    controls();
  }
}
for (const id of ["batch-score", "batch-margin"])
  $(id).addEventListener("input", () => { clearBatch(); controls(); });
$("batch-preview").addEventListener("click", handle(previewBatch));
$("batch-accept").addEventListener("click", handle(async () => {
  const preview = state.batch;
  if (!preview) throw new Error("Preview qualifying matches first");
  state.batchBusy = true;
  controls();
  try {
    const result = await api(`/api/scans/${preview.recording_id}/batch-accept`, {
      ...preview.policy, preview_token: preview.preview_token,
    });
    render(await api(`/api/sessions/${preview.recording_id}`));
    $("batch-summary").textContent = `${result.accepted_count} identities accepted in this draft. ` +
      `${result.remaining_identity_count} identities remain for review. ` +
      "Your saved box changes only after Preview changes and Apply.";
    notice(`Accepted ${result.accepted_count} qualifying identities. Copy counts and stack quantities were preserved.`, "good");
  } catch (error) {
    clearBatch();
    $("batch-summary").textContent = error.message;
    throw error;
  } finally {
    state.batchBusy = false;
    controls();
  }
}));
function changesTable(changes) {
  const table = element("table");
  const head = element("tr");
  for (const text of ["Monster", "Current", "New scan", "Change"])
    head.append(element("th", text));
  table.append(head);
  for (const row of changes) {
    const tr = element("tr");
    for (const value of [`#${row.monster_id} ${row.name || ""}`, row.before, row.after,
      `${row.delta > 0 ? "+" : ""}${row.delta}`]) tr.append(element("td", value));
    table.append(tr);
  }
  return table;
}
$("preview-diff").addEventListener("click", handle(async () => {
  await pauseScan();
  const diff = await api(`/api/scans/${state.session.id}/diff`);
  state.diff = diff;
  $("full-box").checked = false;
  $("diff-summary").textContent =
    `Revision ${diff.base_revision}: ${diff.before_total} → ${diff.after_total} copies · ` +
    `+${diff.added_copies} added · ${diff.removed_copies} removed · ${diff.active_slots} slots. ` +
    (diff.blockers.length ? diff.blockers.join(". ") : "Ready for full-box confirmation and Apply.");
  $("diff-table").replaceChildren(changesTable(diff.changes));
  if (!diff.changes.length) $("diff-table").append(element("p", "No quantity changes."));
  controls();
}));
$("full-box").addEventListener("change", controls);
$("apply-scan").addEventListener("click", handle(async () => {
  if (!state.diff) throw new Error("Preview changes first");
  state.loading = true;
  controls();
  try {
    const applied = await api(`/api/scans/${state.session.id}/apply`, {
      preview_token: state.diff.preview_token,
      full_box_verified: $("full-box").checked,
      expected_slots: $("expected-slots").value ? Number($("expected-slots").value) : null,
    });
    render(await api(`/api/sessions/${state.session.id}`));
    $("applied-state").textContent = `Saved box revision ${applied.revision}. Exports now use this snapshot.`;
    await refreshSessions();
    notice("Scan applied. The entire saved box now matches this snapshot.", "good");
  } catch (error) {
    state.diff = null;
    $("diff-summary").textContent = error.message;
    throw error;
  } finally {
    state.loading = false;
    controls();
  }
}));
$("refresh-history").addEventListener("click", handle(async () => {
  const revisions = await api("/api/history");
  $("history-list").replaceChildren(...revisions.map((row) => {
    const node = element("details");
    node.append(element("summary", `Revision ${row.revision} · ${row.applied_at}`),
      changesTable(row.diff));
    return node;
  }));
  if (!revisions.length) $("history-list").textContent = "No scans applied yet.";
}));
$("merge").addEventListener(
  "click",
  handle(() => {
    const target = $("merge-target").value;
    if (!target) throw new Error("Choose an existing inventory copy");
    return decision("merge", { target_id: target });
  }),
);
let searchTimer;
$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  const query = $("search").value;
  const selected = state.selected, sessionId = state.session?.id;
  searchTimer = setTimeout(
    handle(async () => {
      const matches = await api(`/api/search?q=${encodeURIComponent(query)}`);
      if ($("search").value !== query || state.selected !== selected || state.session?.id !== sessionId) return;
      $("search-results").replaceChildren(
        new Option("Select a search result", ""),
        ...matches.map((row) => new Option(`#${row.id} ${row.name}`, row.id)),
      );
    }),
    250,
  );
});
$("search-results").addEventListener("change", () => {
  if ($("search-results").value) setIdentity(Number($("search-results").value));
});
$("monster-id").addEventListener("change", () => {
  highlightAlternative();
  if ($("monster-id").value)
    state.referenceReady = comparisonImage(
      `/api/icons/${Number($("monster-id").value)}`,
      $("entry-reference"),
    );
});
let mergeTimer;
$("merge-search").addEventListener("input", () => {
  clearTimeout(mergeTimer);
  const query = $("merge-search").value;
  const selected = state.selected, sessionId = state.session?.id;
  mergeTimer = setTimeout(
    handle(async () => {
      const matches = await api(
        `/api/inventory?scan=${encodeURIComponent(state.session.id)}&q=${encodeURIComponent(query)}`,
      );
      if ($("merge-search").value !== query || state.selected !== selected || state.session?.id !== sessionId) return;
      $("merge-target").replaceChildren(
        new Option("Choose an existing copy", ""),
        ...matches
          .filter((row) => row.id !== state.selected)
          .map(
            (row) =>
              new Option(
                `${row.id.slice(0, 8)} · #${row.monster_id || row.proposed_id || "?"} ${row.name}`,
                row.id,
              ),
          ),
      );
    }),
    250,
  );
});
$("merge-target").addEventListener("change", () =>
  localImage(
    $("merge-target").value ? `/api/crops/${$("merge-target").value}` : null,
    $("merge-crop"),
  ),
);
$("unreviewed").addEventListener("change", renderEntries);
$("layout-file").addEventListener(
  "change",
  handle(async () => {
    const file = $("layout-file").files[0];
    if (file)
      $("layout").value = JSON.stringify(
        JSON.parse(await file.text()),
        null,
        2,
      );
  }),
);
$("save-layout").addEventListener(
  "click",
  handle(() =>
    download(
      JSON.stringify(JSON.parse($("layout").value), null, 2),
      "padbox-live-layout.json",
      "application/json",
    ),
  ),
);
for (const format of ["json", "csv"])
  $("export-" + format).addEventListener(
    "click",
    handle(async () => {
      const response = await fetch(`/api/export/${format}`, {
        headers: { "X-PADBox-Token": token },
      });
      if (!response.ok) throw new Error("Export failed");
      download(
        await response.text(),
        `padbox-collection.${format}`,
        format === "json" ? "application/json" : "text/csv",
      );
    }),
  );
window.addEventListener("pagehide", () => {
  state.scanning = false;
  state.stream?.getTracks().forEach((track) => track.stop());
});
new ResizeObserver(([entry]) => {
  document.documentElement.style.setProperty("--scan-controls-offset", `${entry.target.offsetHeight + 12}px`);
}).observe(document.querySelector(".scan-controls"));
handle(async () => {
  const bootstrap = await api("/api/bootstrap");
  $("layout").value = JSON.stringify(bootstrap.layout, null, 2);
  await refreshSessions();
  controls();
  if (!bootstrap.reference_count)
    notice(
      "No reference monsters are cached. Run padbox sources sync before scanning.",
    );
})();
