// Scanned extent, capture viewport, and independent navigation of saved rows.
export class BoxMinimap {
  constructor(canvas, onBrowse, cachedIcon) {
    this.canvas = canvas;
    this.onBrowse = onBrowse;
    this.cachedIcon = cachedIcon;
    this.images = new Map();
    this.regions = [];
    canvas.addEventListener("pointerdown", event => {
      canvas.setPointerCapture(event.pointerId);
      this.dragging = true;
      this.point(event.clientY);
    });
    canvas.addEventListener("pointermove", event => {
      if (this.dragging) this.point(event.clientY);
    });
    for (const event of ["pointerup", "pointercancel", "lostpointercapture"])
      canvas.addEventListener(event, () => { this.dragging = false; });
    canvas.addEventListener("wheel", event => {
      if (!this.regions.length) return;
      event.preventDefault();
      this.step(Math.sign(event.deltaY) * 2);
    }, {passive: false});
    canvas.addEventListener("keydown", event => {
      const steps = {ArrowUp: -1, ArrowDown: 1, PageUp: -5, PageDown: 5};
      if (steps[event.key] === undefined) return;
      event.preventDefault();
      this.step(steps[event.key]);
    });
    new ResizeObserver(() => this.draw()).observe(canvas);
  }
  update(data, entries, viewportHeight, browse) {
    this.data = data;
    this.entries = new Map(entries.map(row => [row.id, row]));
    this.viewportHeight = viewportHeight;
    this.browse = browse;
    this.draw();
  }
  icon(mid) {
    if (!mid) return null;
    if (!this.images.has(mid)) {
      const pending = this.cachedIcon(mid);
      if (pending) {
        this.images.set(mid, null);
        pending.then(url => {
          const image = new Image();
          image.onload = () => {
            this.images.set(mid, image);
            if (!this.scheduled) {
              this.scheduled = true;
              requestAnimationFrame(() => { this.scheduled = false; this.draw(); });
            }
          };
          image.src = url;
        }).catch(() => { this.images.delete(mid); });
      }
    }
    return this.images.get(mid);
  }
  draw() {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(rect.width * ratio);
    this.canvas.height = Math.round(rect.height * ratio);
    const ctx = this.canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    const width = rect.width, height = rect.height;
    ctx.fillStyle = "#0b1319";
    ctx.fillRect(0, 0, width, height);
    const segments = this.data?.segments || [];
    this.regions = [];
    if (!segments.length) {
      this.canvas.setAttribute("aria-valuetext", "No mapped rows yet");
      this.canvas.setAttribute("aria-valuenow", "0");
      this.canvas.dataset.activeSegment = "";
      this.canvas.dataset.viewportTop = "";
      this.canvas.dataset.browseTop = "";
      return;
    }
    const slots = this.data.slots || [];
    const columns = Math.max(1, ...slots.map(s => s.column + 1));
    const cellWidth = (width - 6) / columns;
    const gap = Math.min(14, height / (segments.length * 4));
    const total = segments.reduce((sum, s) => sum + s.max_y - s.min_y, 0);
    const typicalSize = slots.length ? slots[0].rect[3] : 80;
    // Short scans stay compact rather than stretching six icon rows down the
    // entire rail. Long boxes compress to fit, like an editor's overview.
    const scale = Math.min(cellWidth / typicalSize,
      (height - 8 - gap * segments.length) / Math.max(1, total));
    let start = 4;
    for (const segment of segments) {
      if (gap >= 10) {
        ctx.fillStyle = segment.uncertain ? "#ff8399" : "#91aab8";
        ctx.font = "9px system-ui";
        ctx.fillText(`${segment.uncertain ? "? " : ""}§${segment.id + 1}`, 4, start + 9);
      }
      start += gap;
      const end = start + (segment.max_y - segment.min_y) * scale;
      this.regions.push({segment: segment.id, start, end, scale, ...segment});
      start = end;
    }
    for (const slot of slots) {
      const region = this.regions.find(r => r.segment === slot.segment);
      const row = this.entries.get(slot.inventory_id);
      if (!region || !row || ["merged", "rejected"].includes(row.status)) continue;
      const x = 3 + slot.column * cellWidth;
      const y = region.start + (slot.world_y - region.min_y) * region.scale;
      const h = Math.max(1, slot.rect[3] * region.scale);
      const mid = row.monster_id || row.proposed_id;
      const image = this.icon(mid);
      ctx.fillStyle = mid ? `hsl(${mid * 47 % 360} 38% 38%)` : "#657580";
      ctx.fillRect(x, y, cellWidth - 1, h);
      if (image) ctx.drawImage(image, x, y, cellWidth - 1, h);
      ctx.fillStyle = row.tracking === "ambiguous" ? "#ff8399"
        : row.status === "confirmed" ? "#67dbb3" : "#e2b85e";
      ctx.fillRect(x, y, 2, h);
    }
    this.window(ctx, this.data.viewport, "#8cd7e4", true, width);
    this.window(ctx, this.browse, "#ffffff", false, width);
    this.canvas.dataset.activeSegment = this.data.viewport?.segment ?? "";
    this.canvas.dataset.viewportTop = this.data.viewport?.top ?? "";
    this.canvas.dataset.browseTop = this.browse?.top ?? "";
    const region = this.regions.find(r => r.segment === this.data.viewport?.segment);
    const value = region ? Math.round(100 * (region.start +
      (this.data.viewport.top - region.min_y) * region.scale) / height) : 0;
    this.canvas.setAttribute("aria-valuenow", Math.max(0, Math.min(100, value)));
    this.canvas.setAttribute("aria-valuetext", `${slots.length} mapped slots, ${this.data.direction}, ${this.data.position}`);
  }
  window(ctx, viewport, color, live, width) {
    const region = this.regions.find(r => r.segment === viewport?.segment);
    if (!region) return;
    const top = region.start + (viewport.top - region.min_y) * region.scale;
    const h = Math.max(4, (viewport.bottom - viewport.top) * region.scale);
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.setLineDash(live ? [] : [3, 2]);
    if (live) {
      ctx.fillStyle = "#8cd7e422";
      ctx.fillRect(1, top, width - 2, h);
    }
    ctx.strokeRect(1, top + 1, width - 2, Math.max(2, h - 2));
    ctx.setLineDash([]);
  }
  point(clientY) {
    if (!this.regions.length) return;
    const y = clientY - this.canvas.getBoundingClientRect().top;
    const region = this.regions.find(r => y >= r.start && y <= r.end) ||
      [...this.regions].sort((a, b) => Math.abs((a.start+a.end)/2-y) - Math.abs((b.start+b.end)/2-y))[0];
    const center = region.min_y + (y - region.start) / region.scale;
    this.navigate(region, center - this.viewportHeight / 2);
  }
  step(rows) {
    const view = this.browse || this.data?.viewport;
    const region = this.regions.find(r => r.segment === view?.segment) || this.regions[0];
    if (region) this.navigate(region, (view?.top ?? region.min_y) + rows * region.row_pitch);
  }
  navigate(region, top) {
    const height = this.viewportHeight || (region.max_y - region.min_y);
    top = Math.max(region.min_y, Math.min(Math.max(region.min_y, region.max_y - height), top));
    this.onBrowse({segment: region.segment, top, bottom: top + height});
  }
}
