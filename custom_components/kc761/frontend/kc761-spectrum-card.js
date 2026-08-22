const CARD_VERSION = "0.2.0-alpha.18";
const ECHARTS_URL = `/kc761/echarts.esm.min.js?v=${CARD_VERSION}`;
const MAX_CHANNEL = 2047;
const DEFAULT_CARD_CONFIG = Object.freeze({
  title: "KC761 Spectrum",
  x_axis: "energy",
  y_axis: "linear",
  refresh_interval: 0,
  show_controls: true,
  show_summary: true,
  show_legend: true,
  show_actions: true,
  show_peak: true,
});
const CARD_CONTENT_VISIBILITY_KEYS = Object.freeze([
  "show_controls",
  "show_summary",
  "show_legend",
  "show_actions",
  "show_peak",
]);
const CARD_DOCUMENTATION_URL = "https://github.com/GImDX/kc761-ha";
const EDIT_HANDOFF_STORAGE_KEY = "kc761-spectrum-card:edit-handoff:v1";
const EDIT_HANDOFF_TTL_MS = 15000;
const EDIT_HANDOFF_GRACE_MS = 150;
const EDIT_HANDOFF_MAX_RETRIES = 20;
const EDIT_HANDOFF_REGISTRY_KEY = "__kc761SpectrumEditHandoffRegistry";
// First-paint fallback only.  After ECharts lays out the axes, the overlay is
// positioned from its actual grid coordinate system rather than guessed CSS
// insets (which would drift when containLabel or a theme changes).
const GRID_FALLBACK = { top: 15, bottom: 28 };
const COLOR_FALLBACKS = {
  curve: "#4fc3f7",
  roiStart: "#00acc1",
  roiEnd: "#26c6da",
  peak: "#ff7043",
  dataCursor: "#e91e63",
};

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function canonicalConfigValue(value) {
  if (Array.isArray(value)) return value.map((item) => canonicalConfigValue(item));
  if (!value || typeof value !== "object") return value;
  return Object.keys(value).sort().reduce((result, key) => {
    result[key] = canonicalConfigValue(value[key]);
    return result;
  }, {});
}

function editorHandoffStorage() {
  const hostWindow = globalThis.window;
  return hostWindow?.sessionStorage || hostWindow?.localStorage || null;
}

function editorHandoffRegistry() {
  const hostWindow = globalThis.window;
  if (!hostWindow) return null;
  if (!(hostWindow[EDIT_HANDOFF_REGISTRY_KEY] instanceof Map)) {
    hostWindow[EDIT_HANDOFF_REGISTRY_KEY] = new Map();
  }
  return hostWindow[EDIT_HANDOFF_REGISTRY_KEY];
}

function removeEditorHandoffMarker() {
  const storage = editorHandoffStorage();
  try {
    storage?.removeItem(EDIT_HANDOFF_STORAGE_KEY);
  } catch (_error) {
    // Session/local storage can be blocked by browser privacy settings.
  }
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
    this._layoutResizeObserver = null;
    this._refreshTimer = null;
    this._busyState = false;
    this._entityGeneration = 0;
    this._drawToken = 0;
    this._dragCleanup = null;
    this._handoffRetryTimer = null;
    this._handoffRetryCount = 0;
    this._handoffCandidate = null;
    this._preview = false;
    this._detached = false;
    this._colors = { ...COLOR_FALLBACKS };
    this._cursor = { roiStart: 0, roiEnd: MAX_CHANNEL, data: 0 };
  }

  static getStubConfig() {
    // The card picker opens the native editor before an entity is known.  Do
    // not put a fictional entity ID in the stub; the required selector will
    // ask the user to choose a real KC761 spectrum entity.
    return { ...DEFAULT_CARD_CONFIG };
  }

  static isSpectrumEntityState(state) {
    const attributes = state?.attributes || {};
    const hasKC761Identity = typeof attributes.config_entry_id === "string" || typeof attributes.codec_profile === "string";
    const points = attributes.points;
    return hasKC761Identity && (
      Number(attributes.point_count) === 2048
      || (Array.isArray(points) && (points.length === 0 || points.length === 2048))
    );
  }

  static getEntitySuggestion(hass, entityId) {
    if (!entityId || !String(entityId).startsWith("sensor.")) return null;
    const state = hass?.states?.[entityId];
    if (!KC761SpectrumCard.isSpectrumEntityState(state)) return null;
    return {
      config: {
        type: "custom:kc761-spectrum-card",
        entity: entityId,
        ...DEFAULT_CARD_CONFIG,
      },
    };
  }

  static validateConfig(config, { allowMissingEntity = true } = {}) {
    if (!config || typeof config !== "object" || Array.isArray(config)) throw new Error("KC761 spectrum card configuration must be an object");
    const next = { ...config };
    if (next.entity === undefined || next.entity === null || next.entity === "") {
      if (!allowMissingEntity) throw new Error("KC761 spectrum card requires an entity");
      delete next.entity;
    } else if (typeof next.entity !== "string" || !next.entity.startsWith("sensor.")) {
      throw new Error("KC761 spectrum card entity must be a sensor entity");
    }
    const refreshInterval = next.refresh_interval === undefined ? 0 : Number(next.refresh_interval);
    if (!Number.isInteger(refreshInterval) || (refreshInterval !== 0 && (refreshInterval < 5 || refreshInterval > 3600))) {
      throw new Error("refresh_interval must be 0 or an integer between 5 and 3600 seconds");
    }
    next.refresh_interval = refreshInterval;
    if (next.x_axis === undefined) next.x_axis = DEFAULT_CARD_CONFIG.x_axis;
    if (!["energy", "channel"].includes(next.x_axis)) throw new Error("x_axis must be energy or channel");
    if (next.y_axis === undefined) next.y_axis = DEFAULT_CARD_CONFIG.y_axis;
    if (!["linear", "log"].includes(next.y_axis)) throw new Error("y_axis must be linear or log");
    if (next.title === undefined) next.title = DEFAULT_CARD_CONFIG.title;
    if (next.title !== undefined && typeof next.title !== "string") throw new Error("title must be text");
    CARD_CONTENT_VISIBILITY_KEYS.forEach((key) => {
      if (next[key] === undefined) next[key] = true;
      if (typeof next[key] !== "boolean") throw new Error(`${key} must be true or false`);
    });
    return next;
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { filter: { integration: "kc761", domain: "sensor" } } } },
        { name: "title", default: DEFAULT_CARD_CONFIG.title, selector: { text: {} } },
        {
          name: "display",
          title: "Display",
          type: "expandable",
          flatten: true,
          schema: [
            { name: "x_axis", default: DEFAULT_CARD_CONFIG.x_axis, selector: { select: { options: [{ value: "energy", label: "Energy (keV)" }, { value: "channel", label: "Channel address" }] } } },
            { name: "y_axis", default: DEFAULT_CARD_CONFIG.y_axis, selector: { select: { options: [{ value: "linear", label: "Linear" }, { value: "log", label: "Logarithmic" }] } } },
          ],
        },
        {
          name: "refresh",
          title: "Refresh",
          type: "expandable",
          flatten: true,
          schema: [{ name: "refresh_interval", default: DEFAULT_CARD_CONFIG.refresh_interval, selector: { number: { min: 0, max: 3600, mode: "box", step: 1 } } }],
        },
        {
          name: "content",
          title: "Card content",
          type: "expandable",
          flatten: true,
          schema: CARD_CONTENT_VISIBILITY_KEYS.map((name) => ({ name, default: true, selector: { boolean: {} } })),
        },
      ],
      computeLabel: (schema) => ({
        entity: "Spectrum entity",
        title: "Title",
        x_axis: "X axis",
        y_axis: "Y axis",
        refresh_interval: "Auto refresh interval (seconds)",
        show_controls: "Show controls",
        show_summary: "Show summary",
        show_legend: "Show legend",
        show_actions: "Show actions",
        show_peak: "Show peak",
      })[schema.name] || schema.name,
      computeHelper: (schema) => schema.name === "refresh_interval" ? "0 disables automatic capture; enabled values are 5–3600 seconds." : undefined,
      assertConfig: (config) => KC761SpectrumCard.validateConfig(config, { allowMissingEntity: true }),
    };
  }

  get preview() {
    return this._preview;
  }

  set preview(value) {
    const next = Boolean(value);
    if (next === this._preview) return;
    this._preview = next;
    this._applyPresentationMode();
  }

  setConfig(config) {
    const previous = this._config;
    const normalized = KC761SpectrumCard.validateConfig(config);
    const entityChanged = previous?.entity !== normalized.entity;
    const yAxisProvided = Object.prototype.hasOwnProperty.call(config || {}, "y_axis");
    if (entityChanged) this._resetEntityRuntime();
    this._config = normalized;
    this._refreshColors();
    this._setYAxis(this._initialYAxis(normalized.y_axis, yAxisProvided));
    this._renderBase();
    this._applyVisibility();
    if (!this._config.entity) this._setStatus("Select a KC761 spectrum entity");
    this._syncAutoRefresh();
    if (this._data) this._draw();
  }

  connectedCallback() {
    this._detached = false;
    this._ensureLayoutObserver();
    this._applyPresentationMode();
    this._syncAutoRefresh();
    if (this._data) this._draw();
  }

  disconnectedCallback() {
    this._detached = true;
    this._drawToken += 1;
    this._stopAutoRefresh();
    this._clearEditorHandoffRetry();
    this._unregisterEditorHandoffCandidate();
    if (this._dragCleanup) this._dragCleanup();
    this._dragCleanup = null;
    if (this._resizeObserver) this._resizeObserver.disconnect();
    this._resizeObserver = null;
    if (this._layoutResizeObserver) this._layoutResizeObserver.disconnect();
    this._layoutResizeObserver = null;
    if (this._chart) this._chart.dispose();
    this._chart = null;
  }

  set hass(hass) {
    const previous = this._hass?.states?.[this._config?.entity];
    this._hass = hass;
    if (!this._config) return;
    this._refreshColors();
    if (!this._config.entity) {
      this._entryId = null;
      this._device = null;
      this._stopAutoRefresh();
      this._setStatus("Select a KC761 spectrum entity");
      return;
    }
    const current = hass.states[this._config.entity];
    if (!current) {
      this._setStatus(`Entity not found: ${this._config.entity}`, true);
      this._stopAutoRefresh();
      return;
    }
    if (!KC761SpectrumCard.isSpectrumEntityState(current)) {
      if (current.state === "unavailable") {
        // HA may omit all extra attributes while an entity is unavailable.
        // Keep strict KC761 validation for every usable state, but do not
        // discard the last good spectrum or mislabel this transient state as
        // a different entity.  A later valid state will repopulate normally.
        this._stopAutoRefresh();
        this._setStatus("Selected spectrum entity is unavailable", true);
        return;
      }
      this._entryId = null;
      this._device = null;
      this._stopAutoRefresh();
      this._setStatus("Selected entity is not a KC761 spectrum entity", true);
      return;
    }
    this._entryId = current.attributes.config_entry_id;
    const historySelect = this.shadowRoot.querySelector("#history");
    if ((!historySelect || historySelect.value === "latest") && (current !== previous || !this._data)) {
      this._data = this._fromEntity(current);
      this._updateSummary();
      this._draw();
    }
    if (this._entryId && !this._device) this._loadInitial();
    this._syncAutoRefresh();
  }

  getCardSize() {
    const card = this.shadowRoot?.querySelector("ha-card");
    const height = card?.getBoundingClientRect?.().height;
    if (Number.isFinite(height) && height > 0) return Math.max(8, Math.ceil(height / 50));
    // A conservative unrendered estimate avoids clipping this card's summary,
    // chart and action rows before the browser has performed layout.
    return 24;
  }

  getGridOptions() {
    return { columns: "full", min_columns: 6 };
  }

  _renderBase() {
    if (this._rendered) {
      this.shadowRoot.querySelector("#title").textContent = this._config.title || "KC761 Spectrum";
      this.shadowRoot.querySelector("#yaxis").value = this._yAxis;
      this.shadowRoot.querySelector("#xaxis").value = this._config.x_axis || "energy";
      this._applyVisibility();
      this._ensureLayoutObserver();
      this._applyPresentationMode();
      return;
    }
    this._rendered = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;min-width:0;min-height:0;height:100%;overflow:hidden} ha-card{box-sizing:border-box;height:100%;min-height:0;padding:16px;overflow:auto;display:flex;flex-direction:column}[hidden]{display:none!important}
        h2{margin:0 0 8px;font-size:20px}.header{display:flex;align-items:center;gap:8px}.header h2{flex:1}.toolbar,.roi,.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:end}
        label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--secondary-text-color)}
        select,input,button{font:inherit;padding:6px 8px;border-radius:6px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color)}
        button{cursor:pointer}button.danger{color:var(--error-color)}button:disabled{opacity:.45;cursor:not-allowed}
        .chart-wrap{position:relative;margin-top:8px;height:430px;flex:0 1 430px;min-height:220px;border:1px solid var(--divider-color);border-radius:6px;background:var(--card-background-color);overflow:hidden}
        #chart{width:100%;height:100%}.chart-cursor{position:absolute;top:${GRID_FALLBACK.top}px;bottom:${GRID_FALLBACK.bottom}px;width:0;border-left:2px dashed;z-index:5;pointer-events:auto;touch-action:none;cursor:ew-resize}
        .chart-cursor .handle{position:absolute;top:-8px;left:-9px;width:16px;height:16px;border-radius:50%;border:2px solid var(--card-background-color);box-shadow:0 0 0 1px currentColor;background:currentColor;cursor:ew-resize}
        .chart-cursor .caption{position:absolute;top:-25px;left:5px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;font-weight:600;color:currentColor;text-shadow:0 1px var(--card-background-color);pointer-events:none}
        .chart-cursor .caption.staggered{top:-50px}
        #data-cursor{color:var(--kc761-spectrum-data,${COLOR_FALLBACKS.dataCursor})}
        .legend{display:flex;gap:12px;flex-wrap:wrap;margin-top:7px;font-size:12px;color:var(--secondary-text-color)}.legend span{display:inline-flex;gap:5px;align-items:center}.swatch{width:12px;height:3px;display:inline-block;border-radius:2px}.swatch.roi{background:var(--kc761-spectrum-roi-start,${COLOR_FALLBACKS.roiStart})}.swatch.peak{background:var(--kc761-spectrum-peak,${COLOR_FALLBACKS.peak})}.swatch.data{background:var(--kc761-spectrum-data,${COLOR_FALLBACKS.dataCursor})}.swatch.curve{background:var(--kc761-spectrum-curve,${COLOR_FALLBACKS.curve})}
        .summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));grid-auto-rows:58px;gap:6px;margin:8px 0;font-size:12px}.summary div{box-sizing:border-box;min-width:0;height:58px;padding:6px 8px;background:var(--secondary-background-color);border:1px solid var(--divider-color);border-radius:6px;overflow:hidden;display:flex;flex-direction:column}.summary b{display:block;min-width:0;height:1.3em;max-height:1.3em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px;line-height:1.3;margin-top:2px}
        .status{min-height:20px;margin-top:8px;color:var(--secondary-text-color)}.status.error{color:var(--error-color)}.actions{margin-top:10px}.spacer{flex:1}
        .help{font-size:12px;color:var(--secondary-text-color);margin-top:5px}.roi-slider{position:relative;min-width:250px;flex:1 1 300px;padding:8px 0 4px}.roi-slider-track{position:absolute;left:0;right:0;top:20px;height:4px;border-radius:4px;background:var(--divider-color)}.roi-slider-fill{position:absolute;top:0;height:4px;border-radius:4px;background:var(--kc761-spectrum-roi-start,${COLOR_FALLBACKS.roiStart});opacity:.9}.roi-slider input{position:absolute;left:0;top:8px;width:100%;height:24px;margin:0;padding:0;border:0;background:transparent;appearance:none;pointer-events:none}.roi-slider input::-webkit-slider-thumb{appearance:none;width:18px;height:18px;border:2px solid var(--card-background-color);border-radius:50%;background:currentColor;box-shadow:0 0 0 1px var(--kc761-spectrum-roi-start,${COLOR_FALLBACKS.roiStart});pointer-events:auto;cursor:ew-resize}.roi-slider input::-moz-range-thumb{width:14px;height:14px;border:2px solid var(--card-background-color);border-radius:50%;background:currentColor;box-shadow:0 0 0 1px var(--kc761-spectrum-roi-start,${COLOR_FALLBACKS.roiStart});pointer-events:auto;cursor:ew-resize}.roi-slider input:first-of-type{color:var(--kc761-spectrum-roi-start,${COLOR_FALLBACKS.roiStart});z-index:2}.roi-slider input:last-of-type{color:var(--kc761-spectrum-roi-end,${COLOR_FALLBACKS.roiEnd});z-index:3}.roi-values{display:flex;justify-content:space-between;font-variant-numeric:tabular-nums;margin-top:20px}.editor-only{display:block}.configure{font-size:12px;padding:5px 8px}
        @media (max-width:600px){.summary{grid-template-columns:repeat(2,minmax(0,1fr))}.header h2{font-size:18px}}
        ha-card.compact-height .chart-wrap{height:240px;flex-basis:240px;min-height:200px}ha-card.compact-height .summary{grid-auto-rows:52px}ha-card.compact-height .summary div{height:52px;padding:5px 7px}ha-card.compact-height .summary b{font-size:13px}ha-card.compact-height .actions{margin-top:6px}ha-card.compact-height select,ha-card.compact-height input,ha-card.compact-height button{padding:5px 7px}
      </style>
      <ha-card>
        <div class="header"><h2 id="title"></h2><button id="configure" class="configure" type="button">Configure</button></div>
        <div id="controls">
          <div id="editor-controls" class="toolbar editor-only">
            <label>Channel<select id="source"></select></label>
            <label>X axis<select id="xaxis"><option value="energy">Energy (keV)</option><option value="channel">Channel</option></select></label>
            <label>Y axis<select id="yaxis"><option value="linear">Linear</option><option value="log">Logarithmic</option></select></label>
            <label>Saved spectrum<select id="history"><option value="latest">Latest</option></select></label>
          </div>
          <div class="roi"><div class="roi-slider" role="group" aria-label="ROI channel range"><div class="roi-slider-track"><span id="roi-range-fill" class="roi-slider-fill"></span></div><input id="roi-start" type="range" min="0" max="2047" step="1" value="0" aria-label="ROI start channel" aria-valuemin="0" aria-valuemax="2047" aria-valuenow="0"><input id="roi-end" type="range" min="0" max="2047" step="1" value="2047" aria-label="ROI end channel" aria-valuemin="0" aria-valuemax="2047" aria-valuenow="2047"><div class="roi-values"><span id="roi-start-value">0</span><span aria-hidden="true">–</span><span id="roi-end-value">2047</span></div></div>
          </div>
          <div class="help">Drag the two ROI handles to select a range. Drag the pink plot handle to inspect a point.</div>
        </div>
        <div class="summary">
          <div data-summary="acquired">Acquired<b id="acquired">—</b></div><div data-summary="runtime">Accumulation<b id="runtime">—</b></div>
          <div data-summary="total">Total counts<b id="total">—</b></div><div data-summary="peak">Peak<b id="peak">—</b></div><div data-summary="roi-total">ROI counts<b id="roi-total">—</b></div>
          <div>Data cursor<b id="cursor-value">—</b></div>
        </div>
        <div id="chart-wrap" class="chart-wrap">
          <div id="chart" role="img" aria-label="KC761 spectrum chart"></div>
          <div id="data-cursor" class="chart-cursor" role="slider" aria-label="Data cursor" tabindex="0"><span class="handle"></span><span class="caption">Data</span></div>
        </div>
        <div class="legend"><span><i class="swatch curve"></i> Spectrum</span><span><i class="swatch roi"></i> ROI range</span><span data-legend="peak"><i class="swatch peak"></i> Auto peak</span><span><i class="swatch data"></i> Data cursor</span></div>
        <div id="actions" class="actions">
          <button id="capture">Refresh</button><button id="save">Save spectrum</button><button id="csv">Export CSV</button>
          <span class="spacer"></span><button class="danger" data-clear="spectrum">Clear spectrum</button>
          <button class="danger" data-clear="dose_curve">Clear dose curve</button><button class="danger" data-clear="cumulative_dose">Clear cumulative dose</button>
        </div>
        <div id="status" class="status"></div>
      </ha-card>`;
    this.shadowRoot.querySelector("#title").textContent = this._config.title || "KC761 Spectrum";
    this.shadowRoot.querySelector("#yaxis").value = this._yAxis;
    this.shadowRoot.querySelector("#xaxis").value = this._config.x_axis || "energy";
    this.shadowRoot.querySelector("#xaxis").addEventListener("change", () => this._draw());
    this.shadowRoot.querySelector("#source").addEventListener("change", () => this._capture());
    this.shadowRoot.querySelector("#yaxis").addEventListener("change", (event) => {
      this._setYAxis(event.target.value);
      this._persistYAxis();
      this._draw();
    });
    this.shadowRoot.querySelector("#roi-start").addEventListener("input", () => this._syncRoiFromInputs("start"));
    this.shadowRoot.querySelector("#roi-end").addEventListener("input", () => this._syncRoiFromInputs("end"));
    this.shadowRoot.querySelector("#history").addEventListener("change", (event) => this._selectHistory(event.target.value));
    this.shadowRoot.querySelector("#capture").addEventListener("click", () => this._capture());
    this.shadowRoot.querySelector("#save").addEventListener("click", () => this._save());
    this.shadowRoot.querySelector("#csv").addEventListener("click", () => this._csv());
    this.shadowRoot.querySelector("#configure").addEventListener("click", () => this._openEditor());
    this.shadowRoot.querySelectorAll("[data-clear]").forEach((button) => button.addEventListener("click", () => this._clear(button.dataset.clear)));
    this._attachCursor("data", "#data-cursor");
    this._updateRoiSliderUI();
    this._applyVisibility();
    this._ensureLayoutObserver();
    this._applyPresentationMode();
  }

  _applyPresentationMode() {
    if (!this._rendered) return;
    const path = this._findLovelacePath();
    const dialogPreview = this._preview && !path;
    const editorControls = this.shadowRoot.querySelector("#editor-controls");
    const configure = this.shadowRoot.querySelector("#configure");
    if (editorControls) editorControls.hidden = !dialogPreview;
    if (configure) configure.hidden = dialogPreview;
    if (path && this._preview) {
      this._clearEditorHandoffRetry();
      this._tryConsumeEditorHandoff(path);
    } else if (this._preview) {
      this._scheduleEditorHandoffRetry();
    } else {
      this._clearEditorHandoffRetry();
    }
  }

  _ensureLayoutObserver() {
    const card = this.shadowRoot?.querySelector("ha-card");
    if (!card) return;
    this._updateCompactHeight();
    if (this._layoutResizeObserver || typeof ResizeObserver !== "function") return;
    this._layoutResizeObserver = new ResizeObserver(() => this._updateCompactHeight());
    this._layoutResizeObserver.observe(card);
  }

  _updateCompactHeight() {
    const card = this.shadowRoot?.querySelector("ha-card");
    if (!card) return;
    const measured = Number(card.clientHeight) || Number(card.getBoundingClientRect?.().height);
    const compact = Number.isFinite(measured) && measured > 0 && measured < 620;
    card.classList.toggle("compact-height", compact);
    this._compactHeight = compact;
  }

  _applyVisibility() {
    if (!this._rendered) return;
    const visibility = {
      show_controls: "#controls",
      show_summary: ".summary",
      show_legend: ".legend",
      show_actions: "#actions",
    };
    Object.entries(visibility).forEach(([key, selector]) => {
      const node = this.shadowRoot.querySelector(selector);
      if (node) node.hidden = this._config?.[key] === false;
    });
    const peakVisible = this._config?.show_peak !== false;
    const peakSummary = this.shadowRoot.querySelector('[data-summary="peak"]');
    const peakLegend = this.shadowRoot.querySelector('[data-legend="peak"]');
    if (peakSummary) peakSummary.hidden = !peakVisible;
    if (peakLegend) peakLegend.hidden = !peakVisible;
  }

  _resetEntityRuntime() {
    this._entityGeneration = (this._entityGeneration || 0) + 1;
    this._drawToken += 1;
    this._stopAutoRefresh();
    if (this._dragCleanup) this._dragCleanup();
    this._dragCleanup = null;
    this._device = null;
    this._entryId = null;
    this._data = null;
    this._history = [];
    this._cursor = { roiStart: 0, roiEnd: MAX_CHANNEL, data: 0 };
    if (this._resizeObserver) this._resizeObserver.disconnect();
    this._resizeObserver = null;
    if (this._chart) this._chart.dispose();
    this._chart = null;
    if (!this._rendered) return;
    const source = this.shadowRoot.querySelector("#source");
    const history = this.shadowRoot.querySelector("#history");
    if (source) source.replaceChildren();
    if (history) {
      history.replaceChildren(new Option("Latest", "latest"));
      history.value = "latest";
    }
    const start = this.shadowRoot.querySelector("#roi-start");
    const end = this.shadowRoot.querySelector("#roi-end");
    if (start) start.value = "0";
    if (end) end.value = String(MAX_CHANNEL);
    this._updateRoiSliderUI();
  }

  _findLovelacePath() {
    let node = this;
    for (let depth = 0; node && depth < 12; depth += 1) {
      if (Array.isArray(node.path) && node.path.length >= 2) return [...node.path];
      node = node.parentElement || node.getRootNode?.()?.host || null;
    }
    return null;
  }

  _editorLocationKey() {
    const href = globalThis.window?.location?.href;
    if (!href) return null;
    try {
      const url = new URL(String(href));
      url.searchParams.delete("edit");
      return `${url.pathname}${url.search}${url.hash}`;
    } catch (_error) {
      return String(href).replace(/([?&])edit=1(?:&|$)/, "$1").replace(/[?&]$/, "");
    }
  }

  _configFingerprint() {
    return JSON.stringify(canonicalConfigValue(this._config || {}));
  }

  _readPendingEditorHandoff() {
    const storage = editorHandoffStorage();
    if (!storage) return null;
    let marker;
    try {
      const raw = storage.getItem(EDIT_HANDOFF_STORAGE_KEY);
      if (!raw) return null;
      marker = JSON.parse(raw);
    } catch (_error) {
      removeEditorHandoffMarker();
      return null;
    }
    if (!marker || marker.version !== 1 || typeof marker.token !== "string" || typeof marker.createdAt !== "number") {
      removeEditorHandoffMarker();
      return null;
    }
    if (Date.now() - marker.createdAt > EDIT_HANDOFF_TTL_MS) {
      removeEditorHandoffMarker();
      return null;
    }
    if (marker.urlKey !== this._editorLocationKey() || marker.fingerprint !== this._configFingerprint()) return null;
    return marker;
  }

  _writePendingEditorHandoff() {
    const storage = editorHandoffStorage();
    const urlKey = this._editorLocationKey();
    if (!storage || !urlKey) return false;
    const marker = {
      version: 1,
      token: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
      createdAt: Date.now(),
      urlKey,
      fingerprint: this._configFingerprint(),
    };
    try {
      storage.setItem(EDIT_HANDOFF_STORAGE_KEY, JSON.stringify(marker));
      return true;
    } catch (_error) {
      return false;
    }
  }

  _clearEditorHandoffRetry() {
    if (this._handoffRetryTimer !== null && typeof globalThis.clearTimeout === "function") {
      globalThis.clearTimeout(this._handoffRetryTimer);
    }
    this._handoffRetryTimer = null;
    this._handoffRetryCount = 0;
  }

  _scheduleEditorHandoffRetry() {
    if (!this._preview || this._handoffRetryTimer !== null || this._handoffRetryCount >= EDIT_HANDOFF_MAX_RETRIES) return;
    if (!this._readPendingEditorHandoff() || typeof globalThis.setTimeout !== "function") return;
    this._handoffRetryCount += 1;
    this._handoffRetryTimer = globalThis.setTimeout(() => {
      this._handoffRetryTimer = null;
      if (!this._detached) this._applyPresentationMode();
    }, 50);
  }

  _unregisterEditorHandoffCandidate() {
    const candidate = this._handoffCandidate;
    if (!candidate) return;
    candidate.record.candidates.delete(this);
    this._handoffCandidate = null;
    if (candidate.record.candidates.size === 0 && !candidate.record.timer) {
      editorHandoffRegistry()?.delete(candidate.token);
    }
  }

  _resolveEditorHandoff(token, record) {
    const registry = editorHandoffRegistry();
    if (!registry || registry.get(token) !== record) return;
    record.timer = null;
    registry.delete(token);
    const candidates = [...record.candidates.entries()].filter(([card]) => (
      card && card.isConnected !== false && card._preview && card._findLovelacePath()
    ));
    record.candidates.forEach((card) => {
      if (card._handoffCandidate?.record === record) card._handoffCandidate = null;
    });
    record.candidates.clear();
    if (candidates.length !== 1) {
      if (candidates.length > 1) removeEditorHandoffMarker();
      return;
    }
    const [card, path] = candidates[0];
    if (!card._consumeEditorHandoff(token, path)) return;
  }

  _tryConsumeEditorHandoff(path) {
    const marker = this._readPendingEditorHandoff();
    if (!marker || !this._preview || !path) return;
    const registry = editorHandoffRegistry();
    if (!registry) return;
    if (this._handoffCandidate?.token === marker.token) return;
    this._unregisterEditorHandoffCandidate();
    let record = registry.get(marker.token);
    if (!record) {
      record = { candidates: new Map(), timer: null };
      registry.set(marker.token, record);
    }
    record.candidates.set(this, [...path]);
    this._handoffCandidate = { token: marker.token, record };
    if (record.timer === null && typeof globalThis.setTimeout === "function") {
      record.timer = globalThis.setTimeout(() => this._resolveEditorHandoff(marker.token, record), EDIT_HANDOFF_GRACE_MS);
    }
  }

  _consumeEditorHandoff(token, path) {
    const marker = this._readPendingEditorHandoff();
    if (!marker || marker.token !== token) return false;
    if (!this._dispatchEditorEvent(path)) return false;
    removeEditorHandoffMarker();
    return true;
  }

  _dispatchEditorEvent(path) {
    const EventConstructor = globalThis.CustomEvent;
    if (typeof EventConstructor !== "function") return false;
    this.dispatchEvent(new EventConstructor("ll-edit-card", { detail: { path }, bubbles: true, composed: true }));
    return true;
  }

  _openEditor() {
    const path = this._findLovelacePath();
    // A path is authoritative only while HA has rendered this card inside its
    // dashboard edit wrapper.  Normal-view cards can still have a path-bearing
    // ancestor supplied by a host shell, but dispatching ll-edit-card there is
    // ignored by HA and prevents the URL handoff from entering edit mode.
    if (this._preview && path && this._dispatchEditorEvent(path)) return;
    this._writePendingEditorHandoff();
    const location = globalThis.window?.location;
    if (location?.href && typeof location.assign === "function") {
      try {
        const url = new URL(location.href);
        url.searchParams.set("edit", "1");
        location.assign(url.toString());
        return;
      } catch (_error) {
        // Fall through to a non-destructive status message when a host shell
        // exposes an incomplete location object.
      }
    }
    this._setStatus("Open dashboard edit mode to configure this card.");
  }

  _updateRoiSliderUI() {
    if (!this._rendered) return;
    const startValueNumber = Number(this._cursor.roiStart);
    const endValueNumber = Number(this._cursor.roiEnd);
    const start = clamp(Number.isFinite(startValueNumber) ? startValueNumber : 0, 0, MAX_CHANNEL);
    const end = clamp(Number.isFinite(endValueNumber) ? endValueNumber : MAX_CHANNEL, start, MAX_CHANNEL);
    const startInput = this.shadowRoot.querySelector("#roi-start");
    const endInput = this.shadowRoot.querySelector("#roi-end");
    const startValue = this.shadowRoot.querySelector("#roi-start-value");
    const endValue = this.shadowRoot.querySelector("#roi-end-value");
    const fill = this.shadowRoot.querySelector("#roi-range-fill");
    if (startInput) {
      startInput.value = String(start);
      startInput.setAttribute("aria-valuenow", String(start));
    }
    if (endInput) {
      endInput.value = String(end);
      endInput.setAttribute("aria-valuenow", String(end));
    }
    if (startValue) startValue.textContent = String(start);
    if (endValue) endValue.textContent = String(end);
    if (fill) {
      fill.style.left = `${(start / MAX_CHANNEL) * 100}%`;
      fill.style.right = `${100 - (end / MAX_CHANNEL) * 100}%`;
    }
  }

  _initialYAxis(value, explicitlyConfigured = false) {
    const configuredValue = value === "log" ? "log" : "linear";
    const keys = this._storageKeys();
    if (!keys) return configuredValue;
    const read = (key) => {
      try {
        const candidate = window.localStorage.getItem(key);
        return candidate === "linear" || candidate === "log" ? candidate : null;
      } catch (_error) {
        return null;
      }
    };
    const storedConfigured = read(keys.configured);
    const storedSelected = read(keys.selected) || read(keys.legacy);
    // The configured baseline identifies a deliberate editor change.  A new
    // baseline wins over an older runtime preference; once the baseline is
    // unchanged, the selected preference survives HA/Lovelace refreshes.
    if (explicitlyConfigured && storedConfigured !== configuredValue) {
      this._writeYAxis(keys.configured, configuredValue);
      this._writeYAxis(keys.selected, configuredValue);
      this._writeYAxis(keys.legacy, configuredValue);
      return configuredValue;
    }
    return storedSelected || configuredValue;
  }

  _storageKeys(entity = this._config?.entity) {
    if (!entity) return null;
    return {
      configured: `kc761-spectrum:y-axis:configured:${entity}`,
      selected: `kc761-spectrum:y-axis:selected:${entity}`,
      legacy: `kc761-spectrum:y-axis:${entity}`,
    };
  }

  _writeYAxis(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_error) {
      // Storage can be unavailable in private browsing or a locked-down HA iframe.
    }
  }

  _setYAxis(value) {
    this._yAxis = value === "log" ? "log" : "linear";
  }

  _persistYAxis() {
    const keys = this._storageKeys();
    if (!keys) return;
    this._writeYAxis(keys.selected, this._yAxis);
    this._writeYAxis(keys.legacy, this._yAxis);
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
    const chartElement = this.shadowRoot.querySelector("#chart");
    if (!chartElement || !this._chart || !this._data?.points?.length) return;
    const chartRect = chartElement.getBoundingClientRect();
    const xBounds = this._xPixelBounds();
    if (!xBounds) return;
    const pixel = clamp(clientX - chartRect.left, xBounds[0], xBounds[1]);
    // With an xAxis finder ECharts' cartesian Grid maps a scalar pixel value
    // through Axis.coordToData.  Passing an [x, y] pair would select the
    // cartesian path only when a grid/series finder is used and is therefore
    // not portable between ECharts versions.
    const converted = this._chart.convertFromPixel({ xAxisIndex: 0 }, pixel);
    const xValue = Array.isArray(converted) ? Number(converted[0]) : Number(converted);
    if (!Number.isFinite(xValue)) return;
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
    this._updateRoiSliderUI();
    this._updateSummary();
    this._draw();
  }

  _syncRoiFromInputs(changed) {
    const startInput = this.shadowRoot.querySelector("#roi-start");
    const endInput = this.shadowRoot.querySelector("#roi-end");
    const startValue = Number(startInput?.value);
    const endValue = Number(endInput?.value);
    const start = clamp(Math.round(Number.isFinite(startValue) ? startValue : 0), 0, MAX_CHANNEL);
    const end = clamp(Math.round(Number.isFinite(endValue) ? endValue : MAX_CHANNEL), 0, MAX_CHANNEL);
    if (changed === "start") this._cursor.roiStart = Math.min(start, end);
    else this._cursor.roiEnd = Math.max(end, start);
    if (startInput) startInput.value = String(this._cursor.roiStart);
    if (endInput) endInput.value = String(this._cursor.roiEnd);
    this._updateRoiSliderUI();
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
    const generation = this._entityGeneration;
    const entryId = this._entryId;
    try {
      const device = await this._call({ type: "kc761/info", entry_id: entryId });
      if (generation !== this._entityGeneration || entryId !== this._entryId) return;
      this._device = device;
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
      await this._loadHistory(generation, entryId);
      if (generation !== this._entityGeneration || entryId !== this._entryId) return;
      this._setStatus("");
      this._syncAutoRefresh();
    } catch (error) {
      this._setStatus(error.message || String(error), true);
    }
  }

  async _loadHistory(generation = this._entityGeneration, entryId = this._entryId) {
    const history = await this._call({ type: "kc761/spectrum/list", entry_id: entryId });
    if (generation !== this._entityGeneration || entryId !== this._entryId) return;
    this._history = history;
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
    const generation = this._entityGeneration;
    const entryId = this._entryId;
    if (snapshotId === "latest") {
      const state = this._hass.states[this._config.entity];
      this._data = this._fromEntity(state);
    } else {
      const data = await this._call({ type: "kc761/spectrum/get", entry_id: entryId, snapshot_id: snapshotId });
      if (generation !== this._entityGeneration || entryId !== this._entryId) return;
      this._data = data;
    }
    if (generation !== this._entityGeneration || entryId !== this._entryId) return;
    this.shadowRoot.querySelector("#source").value = String(this._data.source);
    this._updateSummary();
    this._draw();
    this._syncAutoRefresh();
  }

  async _capture({ silent = false } = {}) {
    if (this._busyState || !this._entryId) return;
    const generation = this._entityGeneration;
    const entryId = this._entryId;
    await this._busy(async () => {
      const data = await this._call({ type: "kc761/spectrum/capture", entry_id: entryId, source: Number(this.shadowRoot.querySelector("#source").value) });
      if (generation !== this._entityGeneration || entryId !== this._entryId) return;
      this._data = data;
      this.shadowRoot.querySelector("#history").value = "latest";
      this._updateSummary();
      this._draw();
    }, silent ? "" : "Spectrum refreshed");
  }

  async _save() {
    const generation = this._entityGeneration;
    const entryId = this._entryId;
    await this._busy(async () => {
      await this._call({ type: "kc761/spectrum/save", entry_id: entryId, source: Number(this.shadowRoot.querySelector("#source").value) });
      if (generation !== this._entityGeneration || entryId !== this._entryId) return;
      await this._loadHistory(generation, entryId);
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
    const generation = this._entityGeneration;
    const entryId = this._entryId;
    await this._busy(async () => {
      await this._call({ type: "kc761/clear", entry_id: entryId, source, kind });
      if (generation !== this._entityGeneration || entryId !== this._entryId) return;
      const data = await this._call({ type: "kc761/spectrum/capture", entry_id: entryId, source });
      if (generation !== this._entityGeneration || entryId !== this._entryId) return;
      this._data = data;
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

  _setSummaryValue(selector, value) {
    const element = this.shadowRoot.querySelector(selector);
    if (!element) return;
    const text = String(value);
    element.textContent = text;
    // The visible value is deliberately limited to one line so one long
    // cursor/peak value cannot resize the whole card.  Keep the full string
    // available to hover and assistive technology.
    element.title = text;
    element.setAttribute("aria-label", text);
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
    this._setSummaryValue("#acquired", new Date(data.acquired_at).toLocaleString());
    this._setSummaryValue("#runtime", data.accumulation_seconds == null ? "Unknown" : `${data.accumulation_seconds} s`);
    this._setSummaryValue("#total", Number(data.total_counts).toLocaleString());
    this._setSummaryValue("#peak", `CH ${peak[0]} · ${peak[1].toFixed(2)} keV · ${peak[2]}`);
    this._setSummaryValue("#roi-total", roiTotal.toLocaleString());
    this._setSummaryValue("#cursor-value", `CH ${cursorPoint[0]} · ${cursorPoint[1].toFixed(2)} keV · ${cursorPoint[2]}`);
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
    const cursorNearPeak = Math.abs(peak[0] - cursorPoint[0]) <= 16;
    const foreground = this._themeValue("--primary-text-color", "#ddd");
    const secondary = this._themeValue("--secondary-text-color", foreground);
    const divider = this._themeValue("--divider-color", "#555");
    const cardBackground = this._themeValue("--card-background-color", "#1c1c1c");
    const colors = this._colors || COLOR_FALLBACKS;
    return {
      animation: false,
      textStyle: { color: foreground },
      backgroundColor: "transparent",
      // HA's own statistics chart uses a minimal grid and lets containLabel
      // calculate the real plot inset.  A guessed left inset is not stable
      // across themes, label widths, or the linear/log axis switch.
      // Keep a small, explicit top reserve for the horizontal Y-axis title.
      // The native HA charts use a similarly restrained top margin, but this
      // card also exposes an axis name and must keep it away from max ticks.
      grid: { top: 30, left: 1, right: 1, bottom: 28, containLabel: true },
      tooltip: {
        trigger: "axis", renderMode: "html", backgroundColor: cardBackground, borderColor: divider, borderWidth: 1,
        textStyle: { color: foreground }, axisPointer: { type: "line", snap: true, lineStyle: { color: colors.dataCursor } },
        formatter: (items) => {
          const item = items?.[0];
          if (!item) return "";
          const point = this._nearestPointByX(Number(item.value?.[0]));
          return point ? `CH ${point[0]}<br/>${point[1].toFixed(2)} keV<br/>${point[2].toLocaleString()} counts` : "";
        },
      },
      xAxis: {
        type: "value", name: xMode === "energy" ? "Energy (keV)" : "Channel address", nameLocation: "middle", nameGap: 38,
        min: this._xRange()[0], max: this._xRange()[1], boundaryGap: false,
        nameTextStyle: { color: secondary }, axisLabel: {
          color: secondary, hideOverlap: true, showMinLabel: true, showMaxLabel: true, margin: 12,
          formatter: (value) => {
            const number = Number(value);
            if (!Number.isFinite(number)) return "";
            if (xMode === "channel") return String(Math.round(number));
            const digits = Math.abs(number) >= 1000 ? 1 : 2;
            return number.toFixed(digits).replace(/\.?(0+)$/, "");
          },
        },
        axisLine: { lineStyle: { color: divider } }, splitLine: { lineStyle: { color: divider, opacity: 0.45 } },
      },
      yAxis: {
        type: logarithmic ? "log" : "value", name: logarithmic ? "Counts (log)" : "Counts", nameLocation: "end", nameRotate: 0, nameGap: 6,
        nameTextStyle: { color: secondary, align: "left", verticalAlign: "bottom", padding: [0, 0, 4, 0] },
        min: logarithmic ? 1 : 0, axisLabel: { color: secondary, hideOverlap: true, showMinLabel: true, showMaxLabel: true, margin: 8 }, axisLine: { lineStyle: { color: divider } },
        splitLine: { lineStyle: { color: divider, opacity: 0.45 } },
      },
      series: [{
        name: "Spectrum", type: "line", showSymbol: false, sampling: "lttb", data: lineData,
        lineStyle: { color: colors.curve, width: 1.5 }, itemStyle: { color: colors.curve },
        markArea: { silent: true, itemStyle: { color: colors.roiStart, opacity: 0.16 }, data: [[{ xAxis: xFor(roiStartPoint) }, { xAxis: xFor(roiEndPoint) }]] },
        markLine: { silent: true, symbol: ["none", "none"], label: { show: false }, data: [
          { xAxis: xFor(cursorPoint), lineStyle: { color: colors.dataCursor, type: "solid", width: 1 } },
        ] },
        markPoint: this._config?.show_peak === false ? { data: [] } : { symbol: "pin", symbolSize: 34, itemStyle: { color: colors.peak }, label: { color: foreground, fontSize: 10, position: "top", distance: cursorNearPeak ? 28 : 8, offset: cursorNearPeak ? [0, -8] : [0, 0] }, data: [{ coord: [xFor(peak), logarithmic ? Math.max(1, peak[2]) : peak[2]], value: peak[2], name: `Peak CH ${peak[0]}` }] },
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

  _refreshColors() {
    let computed;
    try {
      computed = getComputedStyle(this);
    } catch (_error) {
      computed = null;
    }
    const used = new Set();
    const colors = {};
    const entries = [["curve", 1], ["roiStart", 2], ["roiEnd", 3], ["peak", 4], ["dataCursor", 5]];
    entries.forEach(([name, index]) => {
      const fallback = COLOR_FALLBACKS[name];
      const paletteIndex = ((index - 1) % 54) + 1;
      const graphColor = computed?.getPropertyValue(`--graph-color-${index}`).trim();
      const paletteColor = computed?.getPropertyValue(`--color-${paletteIndex}`).trim();
      let value = graphColor || paletteColor || fallback;
      if (used.has(value.toLowerCase())) value = fallback;
      used.add(value.toLowerCase());
      colors[name] = value;
    });
    this._colors = colors;
    Object.entries({
      "--kc761-spectrum-curve": colors.curve,
      "--kc761-spectrum-roi-start": colors.roiStart,
      "--kc761-spectrum-roi-end": colors.roiEnd,
      "--kc761-spectrum-peak": colors.peak,
      "--kc761-spectrum-data": colors.dataCursor,
    }).forEach(([name, value]) => this.style.setProperty(name, value));
  }

  _themeValue(name, fallback) {
    try {
      return getComputedStyle(this).getPropertyValue(name).trim() || fallback;
    } catch (_error) {
      return fallback;
    }
  }

  _plotRect() {
    const coordinateSystem = this._chart?.getModel?.()?.getComponent?.("grid", 0)?.coordinateSystem;
    const rect = coordinateSystem?.getRect?.();
    if (!rect || ![rect.x, rect.y, rect.width, rect.height].every(Number.isFinite)) return null;
    return rect;
  }

  _chartXPixel(value) {
    if (!this._chart) return null;
    const converted = this._chart.convertToPixel({ xAxisIndex: 0 }, value);
    const pixel = Array.isArray(converted) ? Number(converted[0]) : Number(converted);
    return Number.isFinite(pixel) ? pixel : null;
  }

  _xPixelBounds() {
    const [minX, maxX] = this._xRange();
    const minimum = this._chartXPixel(minX);
    const maximum = this._chartXPixel(maxX);
    if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return null;
    return minimum <= maximum ? [minimum, maximum] : [maximum, minimum];
  }

  _positionCursors() {
    const wrap = this.shadowRoot.querySelector("#chart-wrap");
    const chartElement = this.shadowRoot.querySelector("#chart");
    if (!wrap || !chartElement || !this._chart || !this._points().length) return;
    const wrapRect = wrap.getBoundingClientRect();
    const chartRect = chartElement.getBoundingClientRect();
    const plot = this._plotRect();
    const xBounds = this._xPixelBounds();
    if (!xBounds) return;
    const pointPixel = (channel) => {
      const point = this._pointForChannel(channel) || this._points()[0];
      const pixel = this._chartXPixel(this._xValue(point));
      return Number.isFinite(pixel) ? clamp(pixel, xBounds[0], xBounds[1]) : xBounds[0];
    };
    // Absolute children are positioned from the wrap's padding edge.  The
    // chart rect already includes the wrap border, so adding the raw
    // chartRect-left delta double-counts that border and shifts every HTML
    // cursor by about one pixel.  offsetLeft is the browser's actual offset
    // from this containing block; the geometry fallback subtracts clientLeft
    // for engines that do not expose it on a shadow-DOM element.
    const relativeLeft = Number.isFinite(chartElement.offsetLeft)
      ? chartElement.offsetLeft
      : chartRect.left - wrapRect.left - (wrap.clientLeft || 0);
    const top = plot ? plot.y : GRID_FALLBACK.top;
    const bottom = plot ? Math.max(0, chartElement.clientHeight - plot.y - plot.height) : GRID_FALLBACK.bottom;
    const points = this._points();
    let peak = points[0];
    points.forEach((point) => { if (point[2] > peak[2]) peak = point; });
    const dataPoint = this._pointForChannel(this._cursor.data) || points[0];
    const cursorValues = [["#data-cursor", this._cursor.data, "Data"]];
    cursorValues.forEach(([selector, channel, label]) => {
      const element = this.shadowRoot.querySelector(selector);
      if (!element) return;
      const pixel = pointPixel(channel);
      element.style.left = `${relativeLeft + pixel}px`;
      element.style.top = `${top}px`;
      element.style.bottom = `${bottom}px`;
      const caption = element.querySelector(".caption");
      const point = this._pointForChannel(channel);
      if (caption && point) {
        caption.textContent = `CH ${point[0]} · ${point[1].toFixed(2)} keV · ${point[2]}`;
        caption.classList.toggle("staggered", label === "Data" && dataPoint && Math.abs(peak[0] - dataPoint[0]) <= 16);
        const atRightEdge = pixel > chartElement.clientWidth - 190;
        caption.style.left = atRightEdge ? "auto" : "5px";
        caption.style.right = atRightEdge ? "5px" : "auto";
      }
    });
  }
}

// Keep registration in one idempotent function.  HA can import the module more
// than once during a frontend rebuild, and using globalThis avoids depending
// on a module's lexical global bindings while the app is bootstrapping.
function registerKC761SpectrumCard() {
  const registry = globalThis.customElements;
  if (registry && !registry.get("kc761-spectrum-card")) {
    registry.define("kc761-spectrum-card", KC761SpectrumCard);
  }

  const hostWindow = globalThis.window;
  if (!hostWindow) return;
  const cards = Array.isArray(hostWindow.customCards) ? hostWindow.customCards : [];
  if (hostWindow.customCards !== cards) hostWindow.customCards = cards;
  const metadata = {
    type: "kc761-spectrum-card",
    name: "KC761 Spectrum",
    description: "Current and saved KC761 spectra",
    documentationURL: CARD_DOCUMENTATION_URL,
    preview: false,
    getEntitySuggestion: (hass, entityId) => KC761SpectrumCard.getEntitySuggestion(hass, entityId),
  };
  const existing = cards.findIndex((card) => card.type === "kc761-spectrum-card");
  if (existing < 0) cards.push(metadata);
  else cards[existing] = { ...cards[existing], ...metadata };
}

registerKC761SpectrumCard();
