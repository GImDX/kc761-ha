class KC761SpectrumCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._data = null;
    this._history = [];
    this._device = null;
    this._entryId = null;
    this._rendered = false;
  }

  static getStubConfig() {
    return { entity: "sensor.kc761_spectrum", title: "KC761 Spectrum" };
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("KC761 spectrum card requires an entity");
    this._config = config;
    this._renderBase();
  }

  set hass(hass) {
    const previous = this._hass?.states?.[this._config?.entity];
    this._hass = hass;
    if (!this._config) return;
    const current = hass.states[this._config.entity];
    if (!current) {
      this._setStatus(`Entity not found: ${this._config.entity}`, true);
      return;
    }
    this._entryId = current.attributes.config_entry_id;
    const historySelect = this.shadowRoot.querySelector("#history");
    if ((!historySelect || historySelect.value === "latest") && current !== previous) {
      this._data = this._fromEntity(current);
      this._updateSummary();
      this._draw();
    }
    if (this._entryId && !this._device) this._loadInitial();
  }

  getCardSize() {
    return 8;
  }

  _renderBase() {
    if (this._rendered) return;
    this._rendered = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block} ha-card{padding:16px;overflow:hidden}
        h2{margin:0 0 12px;font-size:20px} .toolbar,.roi,.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:end}
        label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--secondary-text-color)}
        select,input,button{font:inherit;padding:7px 9px;border-radius:6px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color)}
        button{cursor:pointer} button.danger{color:var(--error-color)} button:disabled{opacity:.45;cursor:not-allowed}
        canvas{display:block;width:100%;height:390px;margin-top:12px;border:1px solid var(--divider-color);border-radius:6px;background:var(--primary-background-color)}
        .summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:8px;margin:12px 0;font-size:13px}
        .summary div{padding:8px;background:var(--secondary-background-color);border-radius:6px}.summary b{display:block;font-size:15px;margin-top:3px}
        .status{min-height:20px;margin-top:8px;color:var(--secondary-text-color)} .status.error{color:var(--error-color)}
        .actions{margin-top:10px}.spacer{flex:1}
      </style>
      <ha-card>
        <h2 id="title"></h2>
        <div class="toolbar">
          <label>Channel<select id="source"></select></label>
          <label>X axis<select id="xaxis"><option value="energy">Energy (keV)</option><option value="channel">Channel</option></select></label>
          <label>Y axis<select id="yaxis"><option value="linear">Linear</option><option value="log">Logarithmic</option></select></label>
          <label>Saved spectrum<select id="history"><option value="latest">Latest</option></select></label>
        </div>
        <div class="summary">
          <div>Acquired<b id="acquired">—</b></div><div>Accumulation<b id="runtime">—</b></div>
          <div>Total counts<b id="total">—</b></div><div>Peak<b id="peak">—</b></div><div>ROI counts<b id="roi-total">—</b></div>
        </div>
        <div class="roi">
          <label>ROI start channel<input id="roi-start" type="number" min="0" max="2047" value="0"></label>
          <label>ROI end channel<input id="roi-end" type="number" min="0" max="2047" value="2047"></label>
        </div>
        <canvas id="chart"></canvas>
        <div class="actions">
          <button id="capture">Refresh</button><button id="save">Save spectrum</button><button id="csv">Export CSV</button>
          <span class="spacer"></span><button class="danger" data-clear="spectrum">Clear spectrum</button>
          <button class="danger" data-clear="dose_curve">Clear dose curve</button>
          <button class="danger" data-clear="cumulative_dose">Clear cumulative dose</button>
        </div>
        <div id="status" class="status"></div>
      </ha-card>`;
    this.shadowRoot.querySelector("#title").textContent = this._config.title || "KC761 Spectrum";
    this.shadowRoot.querySelector("#xaxis").addEventListener("change", () => this._draw());
    this.shadowRoot.querySelector("#yaxis").addEventListener("change", () => this._draw());
    this.shadowRoot.querySelector("#roi-start").addEventListener("change", () => { this._updateSummary(); this._draw(); });
    this.shadowRoot.querySelector("#roi-end").addEventListener("change", () => { this._updateSummary(); this._draw(); });
    this.shadowRoot.querySelector("#history").addEventListener("change", (event) => this._selectHistory(event.target.value));
    this.shadowRoot.querySelector("#capture").addEventListener("click", () => this._capture());
    this.shadowRoot.querySelector("#save").addEventListener("click", () => this._save());
    this.shadowRoot.querySelector("#csv").addEventListener("click", () => this._csv());
    this.shadowRoot.querySelectorAll("[data-clear]").forEach((button) => button.addEventListener("click", () => this._clear(button.dataset.clear)));
  }

  async _loadInitial() {
    try {
      this._device = await this._call({ type: "kc761/info", entry_id: this._entryId });
      const source = this.shadowRoot.querySelector("#source");
      source.replaceChildren();
      ["Gamma", "Neutron", "PIN"].forEach((name, index) => {
        if (!this._device.sensor_types[index]) return;
        const option = document.createElement("option"); option.value = String(index); option.textContent = name; source.append(option);
      });
      if (this._data) source.value = String(this._data.source);
      await this._loadHistory();
      this._setStatus("");
    } catch (error) { this._setStatus(error.message || String(error), true); }
  }

  async _loadHistory() {
    this._history = await this._call({ type: "kc761/spectrum/list", entry_id: this._entryId });
    const select = this.shadowRoot.querySelector("#history");
    const selected = select.value;
    select.replaceChildren(new Option("Latest", "latest"));
    this._history.forEach((item) => {
      const label = `${item.channel} · ${new Date(item.acquired_at).toLocaleString()} · ${item.total_counts} counts`;
      select.append(new Option(label, item.snapshot_id));
    });
    select.value = [...select.options].some((option) => option.value === selected) ? selected : "latest";
  }

  async _selectHistory(snapshotId) {
    if (snapshotId === "latest") {
      const state = this._hass.states[this._config.entity]; this._data = this._fromEntity(state);
    } else {
      this._data = await this._call({ type: "kc761/spectrum/get", entry_id: this._entryId, snapshot_id: snapshotId });
    }
    this.shadowRoot.querySelector("#source").value = String(this._data.source);
    this._updateSummary(); this._draw();
  }

  async _capture() {
    await this._busy(async () => {
      this._data = await this._call({ type: "kc761/spectrum/capture", entry_id: this._entryId, source: Number(this.shadowRoot.querySelector("#source").value) });
      this.shadowRoot.querySelector("#history").value = "latest"; this._updateSummary(); this._draw();
    }, "Spectrum refreshed");
  }

  async _save() {
    await this._busy(async () => {
      await this._call({ type: "kc761/spectrum/save", entry_id: this._entryId, source: Number(this.shadowRoot.querySelector("#source").value) });
      await this._loadHistory();
    }, "Spectrum saved");
  }

  async _csv() {
    await this._busy(async () => {
      const selected = this.shadowRoot.querySelector("#history").value;
      const message = { type: "kc761/spectrum/csv", entry_id: this._entryId };
      if (selected !== "latest") message.snapshot_id = selected;
      const result = await this._call(message);
      const url = URL.createObjectURL(new Blob([result.content], { type: "text/csv;charset=utf-8" }));
      const link = document.createElement("a"); link.href = url; link.download = result.filename; link.click(); URL.revokeObjectURL(url);
    }, "CSV exported");
  }

  async _clear(kind) {
    const source = Number(this.shadowRoot.querySelector("#source").value);
    const channel = ["Gamma", "Neutron", "PIN"][source];
    const label = { spectrum: "spectrum and accumulation time", dose_curve: "dose-time curve", cumulative_dose: "cumulative dose and runtime" }[kind];
    const identity = `${this._device.model} / ${this._device.serial_number}`;
    if (!window.confirm(`Device: ${identity}\nChannel: ${channel}\n\nClear ${label}? This cannot be undone.`)) return;
    await this._busy(async () => {
      await this._call({ type: "kc761/clear", entry_id: this._entryId, source, kind });
      this._data = await this._call({ type: "kc761/spectrum/capture", entry_id: this._entryId, source });
      this.shadowRoot.querySelector("#history").value = "latest";
      this._updateSummary(); this._draw();
    }, `${label} cleared`);
  }

  async _busy(operation, success) {
    this.shadowRoot.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    this._setStatus("Working…");
    try { await operation(); this._setStatus(success); }
    catch (error) { this._setStatus(error.message || String(error), true); }
    finally { this.shadowRoot.querySelectorAll("button").forEach((button) => { button.disabled = false; }); }
  }

  _call(message) { return this._hass.callWS(message); }
  _fromEntity(state) { return state && Array.isArray(state.attributes.points) ? { ...state.attributes } : null; }
  _setStatus(text, error = false) { const node = this.shadowRoot.querySelector("#status"); if (!node) return; node.textContent = text; node.classList.toggle("error", error); }

  _roi() {
    const start = Math.max(0, Math.min(2047, Number(this.shadowRoot.querySelector("#roi-start").value) || 0));
    const end = Math.max(start, Math.min(2047, Number(this.shadowRoot.querySelector("#roi-end").value) || 2047));
    return [start, end];
  }

  _updateSummary() {
    const data = this._data; if (!data || !Array.isArray(data.points) || !data.points.length) return;
    const [start, end] = this._roi(); const points = data.points;
    let peak = points[0], roiTotal = 0;
    points.forEach((point) => { if (point[2] > peak[2]) peak = point; if (point[0] >= start && point[0] <= end) roiTotal += point[2]; });
    this.shadowRoot.querySelector("#acquired").textContent = new Date(data.acquired_at).toLocaleString();
    this.shadowRoot.querySelector("#runtime").textContent = data.accumulation_seconds == null ? "Unknown" : `${data.accumulation_seconds} s`;
    this.shadowRoot.querySelector("#total").textContent = Number(data.total_counts).toLocaleString();
    this.shadowRoot.querySelector("#peak").textContent = `CH ${peak[0]} · ${Number(peak[1]).toFixed(2)} keV · ${peak[2]}`;
    this.shadowRoot.querySelector("#roi-total").textContent = roiTotal.toLocaleString();
  }

  _draw() {
    const canvas = this.shadowRoot.querySelector("#chart"), data = this._data;
    if (!canvas || !data || !Array.isArray(data.points) || !data.points.length) return;
    const ratio = window.devicePixelRatio || 1, width = Math.max(320, canvas.clientWidth), height = 390;
    canvas.width = width * ratio; canvas.height = height * ratio;
    const context = canvas.getContext("2d"); context.scale(ratio, ratio); context.clearRect(0, 0, width, height);
    const styles = getComputedStyle(this); const foreground = styles.getPropertyValue("--primary-text-color") || "#ddd";
    const grid = styles.getPropertyValue("--divider-color") || "#555", accent = styles.getPropertyValue("--primary-color") || "#03a9f4";
    const margin = { left: 58, right: 16, top: 18, bottom: 42 }, plotW = width - margin.left - margin.right, plotH = height - margin.top - margin.bottom;
    const xMode = this.shadowRoot.querySelector("#xaxis").value, log = this.shadowRoot.querySelector("#yaxis").value === "log";
    const xs = data.points.map((point) => xMode === "energy" ? Number(point[1]) : Number(point[0]));
    const ys = data.points.map((point) => log ? Math.log10(Number(point[2]) + 1) : Number(point[2]));
    const minX = Math.min(...xs), maxX = Math.max(...xs), maxY = Math.max(1, ...ys);
    context.strokeStyle = grid; context.fillStyle = foreground; context.font = "12px sans-serif"; context.lineWidth = 1;
    for (let i = 0; i <= 5; i += 1) {
      const y = margin.top + plotH * i / 5; context.beginPath(); context.moveTo(margin.left, y); context.lineTo(width - margin.right, y); context.stroke();
      const value = maxY * (1 - i / 5); context.fillText(log ? `${(10 ** value - 1).toFixed(0)}` : value.toFixed(0), 4, y + 4);
    }
    const [roiStart, roiEnd] = this._roi();
    const mapX = (value) => margin.left + ((value - minX) / Math.max(1e-9, maxX - minX)) * plotW;
    const roiX1 = mapX(xs[roiStart]), roiX2 = mapX(xs[roiEnd]); context.fillStyle = "rgba(255,193,7,.13)"; context.fillRect(roiX1, margin.top, Math.max(1, roiX2 - roiX1), plotH);
    context.strokeStyle = accent; context.lineWidth = 1.4; context.beginPath();
    xs.forEach((x, index) => { const px = mapX(x), py = margin.top + plotH - (ys[index] / maxY) * plotH; if (index === 0) context.moveTo(px, py); else context.lineTo(px, py); }); context.stroke();
    let peakIndex = 0;
    data.points.forEach((point, index) => { if (point[2] > data.points[peakIndex][2]) peakIndex = index; });
    const peakX = mapX(xs[peakIndex]), peakY = margin.top + plotH - (ys[peakIndex] / maxY) * plotH;
    context.fillStyle = accent; context.beginPath(); context.arc(peakX, peakY, 3.5, 0, Math.PI * 2); context.fill();
    context.fillStyle = foreground; context.fillText(`Peak CH ${peakIndex}`, Math.min(width - 95, peakX + 7), Math.max(14, peakY - 7));
    context.fillStyle = foreground; context.fillText(xMode === "energy" ? "Energy (keV)" : "Channel address", width / 2 - 35, height - 10);
    context.save(); context.translate(13, height / 2 + 20); context.rotate(-Math.PI / 2); context.fillText(log ? "Counts (log)" : "Counts", 0, 0); context.restore();
  }
}

if (!customElements.get("kc761-spectrum-card")) {
  customElements.define("kc761-spectrum-card", KC761SpectrumCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "kc761-spectrum-card")) {
  window.customCards.push({ type: "kc761-spectrum-card", name: "KC761 Spectrum", description: "Current and saved KC761 spectra" });
}
