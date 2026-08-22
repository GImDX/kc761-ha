const CARD_VERSION = "0.2.0-alpha.6";
const ECHARTS_URL = `/kc761/echarts.esm.min.js?v=${CARD_VERSION}`;
const MAX_CHANNEL = 2047;
const GRID = { left: 74, right: 28, top: 26, bottom: 62 };
const COLORS = {
  curve: "#4fc3f7",
  roiStart: "#00acc1",
  roiEnd: "#26c6da",
  roiFill: "rgba(0, 172, 193, 0.16)",
  peak: "#ff7043",
  dataCursor: "#e91e63",
};

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

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
    this._chart = null;
    this._echarts = null;
    this._echartsPromise = null;
    this._resizeObserver = null;
    this._refreshTimer = null;
    this._busyState = false;
    this._drawToken = 0;
    this._dragCleanup = null;
    this._detached = false;
    this._cursor = { roiStart: 0, roiEnd: MAX_CHANNEL, data: 0 };
  }

  static getStubConfig() {
    return { entity: "sensor.kc761_spectrum", title: "KC761 Spectrum", refresh_interval: 0, y_axis: "linear" };
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("KC761 spectrum card requires an entity");
    const refreshInterval = config.refresh_interval === undefined ? 0 : Number(config.refresh_interval);
    if (!Number.isInteger(refreshInterval) || (refreshInterval !== 0 && (refreshInterval < 5 || refreshInterval > 3600))) {
      throw new Error("refresh_interval must be 0 or an integer between 5 and 3600 seconds");
    }
    this._config = { ...config, refresh_interval: refreshInterval };
    this._setYAxis(this._initialYAxis(config.y_axis));
    this._renderBase();
    this._syncAutoRefresh();
    if (this._data) this._draw();
  }

  connectedCallback() {
    this._detached = false;
    this._syncAutoRefresh();
    if (this._data) this._draw();
  }

  disconnectedCallback() {
    this._detached = true;
    this._drawToken += 1;
    this._stopAutoRefresh();
    if (this._dragCleanup) this._dragCleanup();
    if (this._resizeObserver) this._resizeObserver.disconnect();
    this._resizeObserver = null;
    if (this._chart) this._chart.dispose();
    this._chart = null;
  }

  set hass(hass) {
    const previous = this._hass?.states?.[this._config?.entity];
    this._hass = hass;
    if (!this._config) return;
    const current = hass.states[this._config.entity];
    if (!current) {
      this._setStatus(`Entity not found: ${this._config.entity}`, true);
      this._stopAutoRefresh();
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
    this._syncAutoRefresh();
  }

  getCardSize() {
    return 10;
  }

  _renderBase() {
    if (this._rendered) {
      this.shadowRoot.querySelector("#title").textContent = this._config.title || "KC761 Spectrum";
      this.shadowRoot.querySelector("#yaxis").value = this._yAxis;
      return;
    }
    this._rendered = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block} ha-card{padding:16px;overflow:hidden}
        h2{margin:0 0 12px;font-size:20px}.toolbar,.roi,.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:end}
        label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--secondary-text-color)}
        select,input,button{font:inherit;padding:7px 9px;border-radius:6px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color)}
        button{cursor:pointer}button.danger{color:var(--error-color)}button:disabled{opacity:.45;cursor:not-allowed}
        .chart-wrap{position:relative;margin-top:12px;height:430px;border:1px solid var(--divider-color);border-radius:6px;background:var(--primary-background-color);overflow:hidden}
        #chart{width:100%;height:100%}.chart-cursor{position:absolute;top:${GRID.top}px;bottom:${GRID.bottom}px;width:0;border-left:2px dashed;z-index:5;pointer-events:auto;touch-action:none;cursor:ew-resize}
        .chart-cursor .handle{position:absolute;top:-8px;left:-9px;width:16px;height:16px;border-radius:50%;border:2px solid var(--card-background-color);box-shadow:0 0 0 1px currentColor;background:currentColor;cursor:ew-resize}
        .chart-cursor .caption{position:absolute;top:-25px;left:5px;white-space:nowrap;font-size:11px;font-weight:600;color:currentColor;text-shadow:0 1px var(--primary-background-color)}
        #roi-start-cursor{color:${COLORS.roiStart}}#roi-end-cursor{color:${COLORS.roiEnd}}#data-cursor{color:${COLORS.dataCursor}}
        .legend{display:flex;gap:12px;flex-wrap:wrap;margin-top:7px;font-size:12px;color:var(--secondary-text-color)}.legend span{display:inline-flex;gap:5px;align-items:center}.swatch{width:12px;height:3px;display:inline-block;border-radius:2px}.swatch.roi{background:${COLORS.roiStart}}.swatch.peak{background:${COLORS.peak}}.swatch.data{background:${COLORS.dataCursor}}.swatch.curve{background:${COLORS.curve}}
        .summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:8px;margin:12px 0;font-size:13px}.summary div{padding:8px;background:var(--secondary-background-color);border-radius:6px}.summary b{display:block;font-size:15px;margin-top:3px}
        .status{min-height:20px;margin-top:8px;color:var(--secondary-text-color)}.status.error{color:var(--error-color)}.actions{margin-top:10px}.spacer{flex:1}
        .help{font-size:12px;color:var(--secondary-text-color);margin-top:5px}
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
          <div>Data cursor<b id="cursor-value">—</b></div>
        </div>
        <div class="roi">
          <label>ROI start channel<input id="roi-start" type="number" min="0" max="2047" value="0"></label>
          <label>ROI end channel<input id="roi-end" type="number" min="0" max="2047" value="2047"></label>
        </div>
        <div class="help">Drag the two cyan ROI handles to select a range. Drag the pink handle to inspect a point.</div>
        <div id="chart-wrap" class="chart-wrap">
          <div id="chart" role="img" aria-label="KC761 spectrum chart"></div>
          <div id="roi-start-cursor" class="chart-cursor" role="slider" aria-label="ROI start cursor" tabindex="0"><span class="handle"></span><span class="caption">ROI start</span></div>
          <div id="roi-end-cursor" class="chart-cursor" role="slider" aria-label="ROI end cursor" tabindex="0"><span class="handle"></span><span class="caption">ROI end</span></div>
          <div id="data-cursor" class="chart-cursor" role="slider" aria-label="Data cursor" tabindex="0"><span class="handle"></span><span class="caption">Data</span></div>
        </div>
        <div class="legend"><span><i class="swatch curve"></i> Spectrum</span><span><i class="swatch roi"></i> ROI range</span><span><i class="swatch peak"></i> Auto peak</span><span><i class="swatch data"></i> Data cursor</span></div>
        <div class="actions">
          <button id="capture">Refresh</button><button id="save">Save spectrum</button><button id="csv">Export CSV</button>
          <span class="spacer"></span><button class="danger" data-clear="spectrum">Clear spectrum</button>
          <button class="danger" data-clear="dose_curve">Clear dose curve</button><button class="danger" data-clear="cumulative_dose">Clear cumulative dose</button>
        </div>
        <div id="status" class="status"></div>
      </ha-card>`;
    this.shadowRoot.querySelector("#title").textContent = this._config.title || "KC761 Spectrum";
    this.shadowRoot.querySelector("#yaxis").value = this._yAxis;
    this.shadowRoot.querySelector("#xaxis").addEventListener("change", () => this._draw());
    this.shadowRoot.querySelector("#yaxis").addEventListener("change", (event) => {
      this._setYAxis(event.target.value);
      this._persistYAxis();
      this._draw();
    });
    this.shadowRoot.querySelector("#roi-start").addEventListener("change", () => this._syncRoiFromInputs("start"));
    this.shadowRoot.querySelector("#roi-end").addEventListener("change", () => this._syncRoiFromInputs("end"));
    this.shadowRoot.querySelector("#history").addEventListener("change", (event) => this._selectHistory(event.target.value));
    this.shadowRoot.querySelector("#capture").addEventListener("click", () => this._capture());
    this.shadowRoot.querySelector("#save").addEventListener("click", () => this._save());
    this.shadowRoot.querySelector("#csv").addEventListener("click", () => this._csv());
    this.shadowRoot.querySelectorAll("[data-clear]").forEach((button) => button.addEventListener("click", () => this._clear(button.dataset.clear)));
    this._attachCursor("roiStart", "#roi-start-cursor");
    this._attachCursor("roiEnd", "#roi-end-cursor");
    this._attachCursor("data", "#data-cursor");
  }

  _initialYAxis(value) {
    try {
      const stored = window.localStorage.getItem(this._storageKey());
      if (stored === "linear" || stored === "log") return stored;
    } catch (_error) {
      // Storage can be unavailable in private browsing or a locked-down HA iframe.
    }
    if (value === "linear" || value === "log") return value;
    return "linear";
  }

  _storageKey() {
    return `kc761-spectrum:y-axis:${this._config?.entity || "unknown"}`;
  }

  _setYAxis(value) {
    this._yAxis = value === "log" ? "log" : "linear";
  }

  _persistYAxis() {
    try {
      window.localStorage.setItem(this._storageKey(), this._yAxis);
    } catch (_error) {
      // A failed preference write must never break the chart.
    }
  }

  _attachCursor(kind, selector) {
    const cursor = this.shadowRoot.querySelector(selector);
    if (!cursor) return;
    const start = (event) => {
      event.preventDefault();
      const pointerId = event.pointerId;
      cursor.setPointerCapture?.(pointerId);
      const move = (moveEvent) => {
        if (pointerId !== undefined && moveEvent.pointerId !== undefined && pointerId !== moveEvent.pointerId) return;
        this._setCursorFromClient(kind, moveEvent.clientX);
      };
      const end = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", end);
        window.removeEventListener("pointercancel", end);
        if (this._dragCleanup === end) this._dragCleanup = null;
      };
      if (this._dragCleanup) this._dragCleanup();
      this._dragCleanup = end;
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", end, { once: true });
      window.addEventListener("pointercancel", end, { once: true });
      this._setCursorFromClient(kind, event.clientX);
    };
    cursor.addEventListener("pointerdown", start);
    cursor.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const delta = event.key === "ArrowLeft" ? -1 : 1;
      this._setCursorChannel(kind, this._cursor[kind] + delta);
    });
  }

  _setCursorFromClient(kind, clientX) {
    const wrap = this.shadowRoot.querySelector("#chart-wrap");
    if (!wrap || !this._data?.points?.length) return;
    const rect = wrap.getBoundingClientRect();
    const width = Math.max(1, rect.width);
    const pixel = clamp(clientX - rect.left, GRID.left, width - GRID.right);
    const [minX, maxX] = this._xRange();
    const xValue = minX + ((pixel - GRID.left) / Math.max(1, width - GRID.left - GRID.right)) * (maxX - minX);
    const point = this._nearestPointByX(xValue);
    if (point) this._setCursorChannel(kind, point[0]);
  }

  _setCursorChannel(kind, channel) {
    const value = clamp(Math.round(Number(channel) || 0), 0, MAX_CHANNEL);
    if (kind === "roiStart") this._cursor.roiStart = Math.min(value, this._cursor.roiEnd);
    else if (kind === "roiEnd") this._cursor.roiEnd = Math.max(value, this._cursor.roiStart);
    else this._cursor.data = value;
    const startInput = this.shadowRoot.querySelector("#roi-start");
    const endInput = this.shadowRoot.querySelector("#roi-end");
    if (startInput) startInput.value = String(this._cursor.roiStart);
    if (endInput) endInput.value = String(this._cursor.roiEnd);
    this._updateSummary();
    this._draw();
  }

  _syncRoiFromInputs(changed) {
    const startInput = this.shadowRoot.querySelector("#roi-start");
    const endInput = this.shadowRoot.querySelector("#roi-end");
    const start = clamp(Math.round(Number(startInput?.value) || 0), 0, MAX_CHANNEL);
    const end = clamp(Math.round(Number(endInput?.value) || MAX_CHANNEL), 0, MAX_CHANNEL);
    if (changed === "start") this._cursor.roiStart = Math.min(start, end);
    else this._cursor.roiEnd = Math.max(end, start);
    if (startInput) startInput.value = String(this._cursor.roiStart);
    if (endInput) endInput.value = String(this._cursor.roiEnd);
    this._updateSummary();
    this._draw();
  }

  _syncAutoRefresh() {
    const interval = Number(this._config?.refresh_interval || 0);
    if (!interval || !this._hass || !this._entryId || !this.isConnected || this._busyState) {
      if (!interval || !this._hass || !this._entryId || !this.isConnected) this._stopAutoRefresh();
      return;
    }
    if (!this._refreshTimer) this._refreshTimer = window.setInterval(() => this._autoRefresh(), interval * 1000);
  }

  _stopAutoRefresh() {
    if (this._refreshTimer) window.clearInterval(this._refreshTimer);
    this._refreshTimer = null;
  }

  async _autoRefresh() {
    if (this._busyState || !this._entryId || !this._device || !this._data) return;
    const history = this.shadowRoot.querySelector("#history");
    if (history && history.value !== "latest") return;
    await this._capture({ silent: true });
  }

  async _loadInitial() {
    try {
      this._device = await this._call({ type: "kc761/info", entry_id: this._entryId });
      const source = this.shadowRoot.querySelector("#source");
      source.replaceChildren();
      ["Gamma", "Neutron", "PIN"].forEach((name, index) => {
        if (!this._device.sensor_types[index]) return;
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = name;
        source.append(option);
      });
      if (this._data) source.value = String(this._data.source);
      source.addEventListener("change", () => this._capture());
      await this._loadHistory();
      this._setStatus("");
      this._syncAutoRefresh();
    } catch (error) {
      this._setStatus(error.message || String(error), true);
    }
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
    this._syncAutoRefresh();
  }

  async _selectHistory(snapshotId) {
    if (snapshotId === "latest") {
      const state = this._hass.states[this._config.entity];
      this._data = this._fromEntity(state);
    } else {
      this._data = await this._call({ type: "kc761/spectrum/get", entry_id: this._entryId, snapshot_id: snapshotId });
    }
    this.shadowRoot.querySelector("#source").value = String(this._data.source);
    this._updateSummary();
    this._draw();
    this._syncAutoRefresh();
  }

  async _capture({ silent = false } = {}) {
    if (this._busyState || !this._entryId) return;
    await this._busy(async () => {
      this._data = await this._call({ type: "kc761/spectrum/capture", entry_id: this._entryId, source: Number(this.shadowRoot.querySelector("#source").value) });
      this.shadowRoot.querySelector("#history").value = "latest";
      this._updateSummary();
      this._draw();
    }, silent ? "" : "Spectrum refreshed");
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
      const link = document.createElement("a");
      link.href = url;
      link.download = result.filename;
      link.click();
      URL.revokeObjectURL(url);
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
      this._updateSummary();
      this._draw();
    }, `${label} cleared`);
  }

  async _busy(operation, success) {
    if (this._busyState) return;
    this._busyState = true;
    this.shadowRoot.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    if (success) this._setStatus("Working…");
    try {
      await operation();
      if (success) this._setStatus(success);
    } catch (error) {
      this._setStatus(error.message || String(error), true);
    } finally {
      this._busyState = false;
      this.shadowRoot.querySelectorAll("button").forEach((button) => { button.disabled = false; });
      this._syncAutoRefresh();
    }
  }

  _call(message) {
    return this._hass.callWS(message);
  }

  _fromEntity(state) {
    return state && Array.isArray(state.attributes.points) ? { ...state.attributes } : null;
  }

  _setStatus(text, error = false) {
    const node = this.shadowRoot.querySelector("#status");
    if (!node) return;
    node.textContent = text;
    node.classList.toggle("error", error);
  }

  _points() {
    return (this._data?.points || []).filter((point) => Array.isArray(point) && point.length >= 3 && Number.isFinite(Number(point[0])) && Number.isFinite(Number(point[1])) && Number.isFinite(Number(point[2]))).map((point) => [Number(point[0]), Number(point[1]), Number(point[2])]);
  }

  _xValue(point) {
    return this.shadowRoot.querySelector("#xaxis")?.value === "channel" ? point[0] : point[1];
  }

  _xRange() {
    const values = this._points().map((point) => this._xValue(point));
    if (!values.length) return [0, 1];
    const minimum = Math.min(...values), maximum = Math.max(...values);
    return [minimum, maximum === minimum ? minimum + 1 : maximum];
  }

  _nearestPointByX(xValue) {
    return this._points().reduce((nearest, point) => !nearest || Math.abs(this._xValue(point) - xValue) < Math.abs(this._xValue(nearest) - xValue) ? point : nearest, null);
  }

  _pointForChannel(channel) {
    return this._points().reduce((nearest, point) => !nearest || Math.abs(point[0] - channel) < Math.abs(nearest[0] - channel) ? point : nearest, null);
  }

  _roi() {
    const start = clamp(this._cursor.roiStart, 0, MAX_CHANNEL);
    const end = clamp(this._cursor.roiEnd, start, MAX_CHANNEL);
    return [start, end];
  }

  _updateSummary() {
    const points = this._points();
    const data = this._data;
    if (!data || !points.length) return;
    const [start, end] = this._roi();
    let peak = points[0], roiTotal = 0;
    points.forEach((point) => {
      if (point[2] > peak[2]) peak = point;
      if (point[0] >= start && point[0] <= end) roiTotal += point[2];
    });
    const cursorPoint = this._pointForChannel(this._cursor.data) || points[0];
    this.shadowRoot.querySelector("#acquired").textContent = new Date(data.acquired_at).toLocaleString();
    this.shadowRoot.querySelector("#runtime").textContent = data.accumulation_seconds == null ? "Unknown" : `${data.accumulation_seconds} s`;
    this.shadowRoot.querySelector("#total").textContent = Number(data.total_counts).toLocaleString();
    this.shadowRoot.querySelector("#peak").textContent = `CH ${peak[0]} · ${peak[1].toFixed(2)} keV · ${peak[2]}`;
    this.shadowRoot.querySelector("#roi-total").textContent = roiTotal.toLocaleString();
    this.shadowRoot.querySelector("#cursor-value").textContent = `CH ${cursorPoint[0]} · ${cursorPoint[1].toFixed(2)} keV · ${cursorPoint[2]}`;
  }

  _option() {
    const points = this._points();
    const xMode = this.shadowRoot.querySelector("#xaxis").value;
    const logarithmic = this._yAxis === "log";
    const lineData = points.map((point) => [this._xValue(point), logarithmic ? Math.max(1, point[2]) : point[2]]);
    const [roiStart, roiEnd] = this._roi();
    const roiStartPoint = this._pointForChannel(roiStart) || points[0];
    const roiEndPoint = this._pointForChannel(roiEnd) || points[points.length - 1];
    const cursorPoint = this._pointForChannel(this._cursor.data) || points[0];
    let peak = points[0];
    points.forEach((point) => { if (point[2] > peak[2]) peak = point; });
    const xFor = (point) => this._xValue(point);
    const foreground = getComputedStyle(this).getPropertyValue("--primary-text-color").trim() || "#ddd";
    const divider = getComputedStyle(this).getPropertyValue("--divider-color").trim() || "#555";
    return {
      animation: false,
      textStyle: { color: foreground },
      grid: { ...GRID, containLabel: true },
      tooltip: {
        trigger: "axis", axisPointer: { type: "line", snap: true },
        formatter: (items) => {
          const item = items?.[0];
          if (!item) return "";
          const point = this._nearestPointByX(Number(item.value?.[0]));
          return point ? `CH ${point[0]}<br/>${point[1].toFixed(2)} keV<br/>${point[2].toLocaleString()} counts` : "";
        },
      },
      xAxis: {
        type: "value", name: xMode === "energy" ? "Energy (keV)" : "Channel address", nameLocation: "middle", nameGap: 38,
        min: this._xRange()[0], max: this._xRange()[1], boundaryGap: false, axisLabel: { hideOverlap: true, margin: 10 },
        axisLine: { lineStyle: { color: divider } }, splitLine: { lineStyle: { color: divider, opacity: 0.45 } },
      },
      yAxis: {
        type: logarithmic ? "log" : "value", name: logarithmic ? "Counts (log)" : "Counts", nameLocation: "middle", nameGap: 54,
        min: logarithmic ? 1 : 0, axisLabel: { hideOverlap: true, margin: 12 }, axisLine: { lineStyle: { color: divider } },
        splitLine: { lineStyle: { color: divider, opacity: 0.45 } },
      },
      series: [{
        name: "Spectrum", type: "line", showSymbol: false, sampling: "lttb", data: lineData,
        lineStyle: { color: COLORS.curve, width: 1.5 }, itemStyle: { color: COLORS.curve },
        markArea: { silent: true, itemStyle: { color: COLORS.roiFill, borderColor: COLORS.roiStart, borderWidth: 1 }, data: [[{ xAxis: xFor(roiStartPoint) }, { xAxis: xFor(roiEndPoint) }]] },
        markLine: { silent: true, symbol: ["none", "none"], label: { show: false }, data: [
          { xAxis: xFor(roiStartPoint), lineStyle: { color: COLORS.roiStart, type: "dashed", width: 1 } },
          { xAxis: xFor(roiEndPoint), lineStyle: { color: COLORS.roiEnd, type: "dashed", width: 1 } },
          { xAxis: xFor(cursorPoint), lineStyle: { color: COLORS.dataCursor, type: "solid", width: 1 } },
        ] },
        markPoint: { symbol: "pin", symbolSize: 34, itemStyle: { color: COLORS.peak }, label: { color: "#fff", fontSize: 10 }, data: [{ coord: [xFor(peak), logarithmic ? Math.max(1, peak[2]) : peak[2]], value: peak[2], name: `Peak CH ${peak[0]}` }] },
      }],
    };
  }

  _draw() {
    if (!this._data || !this._points().length || !this.shadowRoot.querySelector("#chart")) return;
    const token = ++this._drawToken;
    this._ensureChart().then(() => {
      if (token !== this._drawToken || !this._chart) return;
      this._chart.setOption(this._option(), { notMerge: true, lazyUpdate: false });
      this._positionCursors();
    }).catch((error) => this._setStatus(`Chart load failed: ${error.message || error}`, true));
  }

  async _ensureChart() {
    if (this._chart) return this._chart;
    if (this._detached) return null;
    if (!this._echartsPromise) this._echartsPromise = import(ECHARTS_URL);
    const module = await this._echartsPromise;
    const chartElement = this.shadowRoot.querySelector("#chart");
    if (!chartElement) return null;
    this._echarts = module.default || module;
    this._chart = this._echarts.init(chartElement, null, { renderer: "svg", useDirtyRect: false });
    this._resizeObserver = new ResizeObserver(() => {
      if (this._chart) {
        this._chart.resize();
        this._positionCursors();
      }
    });
    this._resizeObserver.observe(chartElement);
    return this._chart;
  }

  _positionCursors() {
    const wrap = this.shadowRoot.querySelector("#chart-wrap");
    if (!wrap || !this._points().length) return;
    const width = Math.max(1, wrap.getBoundingClientRect().width || wrap.clientWidth);
    const [minX, maxX] = this._xRange();
    const pointPixel = (channel) => {
      const point = this._pointForChannel(channel) || this._points()[0];
      const ratio = (this._xValue(point) - minX) / Math.max(1e-9, maxX - minX);
      return GRID.left + clamp(ratio, 0, 1) * Math.max(1, width - GRID.left - GRID.right);
    };
    [["#roi-start-cursor", this._cursor.roiStart], ["#roi-end-cursor", this._cursor.roiEnd], ["#data-cursor", this._cursor.data]].forEach(([selector, channel]) => {
      const element = this.shadowRoot.querySelector(selector);
      if (element) element.style.left = `${pointPixel(channel)}px`;
    });
  }
}

if (!customElements.get("kc761-spectrum-card")) {
  customElements.define("kc761-spectrum-card", KC761SpectrumCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "kc761-spectrum-card")) {
  window.customCards.push({ type: "kc761-spectrum-card", name: "KC761 Spectrum", description: "Current and saved KC761 spectra" });
}
