import assert from "node:assert/strict";

class FakeNode {
  constructor(selector = "") {
    this.selector = selector;
    this.style = { setProperty() {} };
    this.classList = {
      values: new Set(),
      toggle: (name, force) => {
        const next = force === undefined ? !this.classList.values.has(name) : Boolean(force);
        if (next) this.classList.values.add(name);
        else this.classList.values.delete(name);
        return next;
      },
      contains: (name) => this.classList.values.has(name),
    };
    this.dataset = {};
    this.attributes = new Map();
    this.options = [];
    this.value = "";
    this.hidden = false;
    this.clientHeight = 430;
    this.clientWidth = 640;
    this.offsetLeft = 0;
    this.clientLeft = 1;
    this.listeners = new Map();
  }

  addEventListener(type) { this.listeners.set(type, (this.listeners.get(type) || 0) + 1); }
  removeEventListener() {}
  listenerCount(type) { return this.listeners.get(type) || 0; }
  setPointerCapture() {}
  setAttribute(name, value) { this.attributes.set(name, String(value)); this[name] = String(value); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  querySelector(selector) { return selector === ".caption" ? new FakeNode(selector) : new FakeNode(selector); }
  querySelectorAll() { return []; }
  replaceChildren(...items) { this.options = [...items]; }
  append(...items) { this.options.push(...items); }
  getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight }; }
}

class FakeShadowRoot extends FakeNode {
  constructor() {
    super("shadow-root");
    this.nodes = new Map();
  }

  querySelector(selector) {
    if (!this.nodes.has(selector)) {
      const node = new FakeNode(selector);
      if (selector === "#history") node.value = "latest";
      this.nodes.set(selector, node);
    }
    return this.nodes.get(selector);
  }
}

class FakeHTMLElement {
  constructor() {
    this.style = { setProperty() {} };
    this.isConnected = false;
    this.parentElement = null;
  }

  attachShadow() {
    this.shadowRoot = new FakeShadowRoot();
    return this.shadowRoot;
  }

  dispatchEvent(event) {
    this.lastEvent = event;
    return true;
  }
}

const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;
globalThis.customElements = {
  get: (name) => registry.get(name),
  define: (name, value) => registry.set(name, value),
};
const storage = new Map();
const storageApi = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};
globalThis.window = {
  customCards: [],
  sessionStorage: storageApi,
  localStorage: storageApi,
  addEventListener() {},
  removeEventListener() {},
  setInterval: () => 1,
  clearInterval() {},
  confirm: () => false,
  location: {
    href: "http://ha.local/dashboard-kc761/0",
    assign: (value) => { globalThis.window.location.assigned = value; },
  },
};
const scheduledTimers = new Map();
let nextTimerId = 0;
globalThis.setTimeout = (callback) => {
  const id = ++nextTimerId;
  scheduledTimers.set(id, callback);
  return id;
};
globalThis.clearTimeout = (id) => scheduledTimers.delete(id);
const flushTimers = () => {
  const callbacks = [...scheduledTimers.values()];
  scheduledTimers.clear();
  callbacks.forEach((callback) => callback());
};
globalThis.CustomEvent = class {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
    this.bubbles = init.bubbles;
    this.composed = init.composed;
  }
};
globalThis.document = {
  createElement: (selector) => new FakeNode(selector),
};
globalThis.Option = class extends FakeNode {
  constructor(text, value) {
    super("option");
    this.textContent = text;
    this.value = value;
  }
};
globalThis.ResizeObserver = class {
  constructor(callback) {
    this.callback = callback;
    this.targets = [];
    this.disconnectCalls = 0;
  }

  observe(target) { this.targets.push(target); }
  disconnect() { this.disconnectCalls += 1; }
  trigger() { this.callback(); }
};
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });

await import(new URL("../custom_components/kc761/frontend/kc761-spectrum-card.js", import.meta.url));
const Card = customElements.get("kc761-spectrum-card");
assert.ok(Card);
assert.equal(Card.getStubConfig().entity, undefined);
assert.equal(Card.getStubConfig().show_peak, true);

const form = Card.getConfigForm();
const schemaNames = [];
const collect = (schema) => schema.forEach((item) => {
  if (item.name) schemaNames.push(item.name);
  if (item.schema) collect(item.schema);
});
collect(form.schema);
for (const name of ["entity", "title", "x_axis", "y_axis", "refresh_interval", "show_controls", "show_summary", "show_legend", "show_actions", "show_peak"]) {
  assert.ok(schemaNames.includes(name), `missing form field ${name}`);
}
assert.doesNotThrow(() => form.assertConfig({ title: "picker placeholder" }));
assert.throws(() => Card.validateConfig({ entity: "sensor.kc761_spectrum", refresh_interval: 4 }), /refresh_interval/);
assert.throws(() => Card.validateConfig({ entity: "sensor.kc761_spectrum", y_axis: "sqrt" }), /y_axis/);
assert.throws(() => Card.validateConfig({ entity: "sensor.kc761_spectrum", show_peak: "yes" }), /show_peak/);

const state = { attributes: { config_entry_id: "entry", codec_profile: "manual_v1_8_candidate", points: Array.from({ length: 2048 }, (_, i) => [i, i / 2, i]) } };
const suggestion = Card.getEntitySuggestion({ states: { "sensor.real_spectrum": state } }, "sensor.real_spectrum");
assert.deepEqual(Object.keys(suggestion), ["config"]);
assert.deepEqual(Object.keys(suggestion.config).sort(), ["entity", "refresh_interval", "show_actions", "show_controls", "show_legend", "show_peak", "show_summary", "title", "type", "x_axis", "y_axis"].sort());
assert.equal(suggestion.config.entity, "sensor.real_spectrum");
assert.equal(Card.getEntitySuggestion({ states: { "sensor.other": { attributes: { points: [[0, 0, 1]] } } } }, "sensor.other"), null);

const card = new Card();
assert.doesNotThrow(() => {
  const placeholder = new Card();
  placeholder.setConfig(Card.getStubConfig());
});
card.setConfig({ entity: "sensor.real_spectrum", future_option: { keep: true }, visibility: [{ condition: "state" }], show_controls: false, show_peak: false });
assert.deepEqual(card._config.future_option, { keep: true });
assert.deepEqual(card._config.visibility, [{ condition: "state" }]);
assert.equal(card.shadowRoot.querySelector("#source").listenerCount("change"), 1);
assert.equal(card.shadowRoot.querySelector("#roi-start").listenerCount("input"), 1);
assert.equal(card.shadowRoot.querySelector("#roi-end").listenerCount("input"), 1);
assert.equal(card.shadowRoot.querySelector("#data-cursor-slider").listenerCount("input"), 1);
assert.equal(card.shadowRoot.querySelector("#data-cursor-slider").getAttribute?.("role") ?? "slider", "slider");
assert.equal(card.shadowRoot.querySelector("#data-cursor-slider").listenerCount("pointerdown"), 0);
assert.equal(card.shadowRoot.querySelector("#reset-zoom").listenerCount("click"), 1);
assert.equal(card.shadowRoot.querySelector("#reset-zoom").disabled, true);
assert.match(card.shadowRoot.querySelector("#zoom-readout").getAttribute("aria-label"), /^View CH /);
assert.equal(card.shadowRoot.querySelector("#controls").hidden, true);
assert.equal(card.shadowRoot.querySelector("#editor-controls").hidden, true);
assert.equal(card.shadowRoot.querySelector("#configure").hidden, false);
assert.equal(card.shadowRoot.querySelector(".summary").hidden, false);
assert.equal(card.shadowRoot.querySelector('[data-summary="peak"]').hidden, true);
assert.equal(card.shadowRoot.querySelector('[data-legend="peak"]').hidden, true);
card.setConfig({ entity: "sensor.real_spectrum", show_summary: false, show_legend: false, show_actions: false });
assert.equal(card.shadowRoot.querySelector(".summary").hidden, true);
assert.equal(card.shadowRoot.querySelector(".legend").hidden, true);
assert.equal(card.shadowRoot.querySelector("#actions").hidden, true);
assert.equal(card.getCardSize(), 9);
assert.deepEqual(card.getGridOptions(), { columns: "full", min_columns: 6 });
const feedbackSizeCard = Object.create(Card.prototype);
feedbackSizeCard.shadowRoot = { querySelector: () => ({ getBoundingClientRect: () => ({ height: 2400 }) }) };
assert.equal(feedbackSizeCard.getCardSize(), 9, "Masonry size must not feed rendered height back into row allocation");

card.preview = true;
assert.equal(card.shadowRoot.querySelector("#editor-controls").hidden, false, "dialog preview shows editor-only controls");
assert.equal(card.shadowRoot.querySelector("#configure").hidden, true, "dialog preview has no card path");
const pathAncestor = { path: [0, 1, 2], parentElement: null };
const shadowHost = { parentElement: null, getRootNode: () => ({ host: pathAncestor }) };
card.parentElement = null;
card.getRootNode = () => ({ host: shadowHost });
card.connectedCallback();
assert.equal(card.shadowRoot.querySelector("#editor-controls").hidden, true, "dashboard edit wrapper hides editor-only controls");
assert.equal(card.shadowRoot.querySelector("#configure").hidden, false, "edit-mode card with path keeps Configure visible");
card.preview = false;
assert.equal(card.shadowRoot.querySelector("#editor-controls").hidden, true);
assert.equal(card.shadowRoot.querySelector("#configure").hidden, false);
card.parentElement = null;
assert.equal(card.shadowRoot.querySelector("#roi-start")["aria-valuenow"], "0");
assert.equal(card.shadowRoot.querySelector("#roi-end")["aria-valuenow"], "2047");
card._setCursorChannel("roiStart", 321);
assert.equal(card.shadowRoot.querySelector("#roi-start").value, "321");
assert.equal(card.shadowRoot.querySelector("#roi-start-value").textContent, "321");

const layoutCard = card.shadowRoot.querySelector("ha-card");
layoutCard.clientHeight = 500;
card._updateCompactHeight();
assert.equal(layoutCard.classList.contains("compact-height"), true);
layoutCard.clientHeight = 900;
card._updateCompactHeight();
assert.equal(layoutCard.classList.contains("compact-height"), false);
layoutCard.clientHeight = 430;
  card._updateCompactHeight();

  // A normal-view card must use the URL handoff even if a host shell exposes
  // an incidental path-bearing ancestor.  ll-edit-card is only meaningful for
  // the dashboard edit wrapper/preview.
  globalThis.window.location.href = "http://ha.local/dashboard-kc761/0";
  globalThis.window.location.assigned = null;
  card._openEditor();
  assert.equal(card.lastEvent, undefined, "normal view does not dispatch an edit event");
  assert.match(globalThis.window.location.assigned, /[?&]edit=1(?:&|$)/);

  // In dashboard edit mode the same path is dispatched directly to HA's
  // native card editor listener.
  card.preview = true;
  card.lastEvent = undefined;
  card._openEditor();
  assert.equal(card.lastEvent.type, "ll-edit-card");
  assert.deepEqual(card.lastEvent.detail, { path: [0, 1, 2] });
  card.preview = false;
  card.parentElement = null;
  card.getRootNode = undefined;
  globalThis.window.location.assigned = null;
card._openEditor();
assert.match(globalThis.window.location.assigned, /[?&]edit=1(?:&|$)/);

// A normal-view Configure click leaves a scoped one-time marker.  The card
// recreated by HA in edit mode must wait for a real path and then dispatch
// the native event exactly once.
const handoffKey = "kc761-spectrum-card:edit-handoff:v1";
const handoffConfig = { entity: "sensor.handoff_spectrum", title: "Handoff" };
const normalHandoffCard = new Card();
normalHandoffCard.isConnected = true;
normalHandoffCard.setConfig(handoffConfig);
normalHandoffCard.parentElement = null;
normalHandoffCard.getRootNode = undefined;
globalThis.window.location.href = "http://ha.local/dashboard-kc761/0";
normalHandoffCard._openEditor();
assert.ok(storage.get(handoffKey), "normal Configure writes a pending handoff");
assert.match(globalThis.window.location.assigned, /[?&]edit=1(?:&|$)/);

const recreatedHandoffCard = new Card();
recreatedHandoffCard.isConnected = true;
recreatedHandoffCard.setConfig(handoffConfig);
recreatedHandoffCard.parentElement = null;
recreatedHandoffCard.getRootNode = () => ({ host: { parentElement: null } });
recreatedHandoffCard.preview = true;
// The path is deliberately attached after the first preview pass.
recreatedHandoffCard.getRootNode = () => ({ host: { path: [0, 4, 1] } });
let handoffDispatches = 0;
recreatedHandoffCard.dispatchEvent = (event) => {
  handoffDispatches += 1;
  recreatedHandoffCard.lastEvent = event;
  return true;
};
flushTimers();
flushTimers();
assert.equal(handoffDispatches, 1, "unique matching edit card opens once");
assert.deepEqual(recreatedHandoffCard.lastEvent.detail, { path: [0, 4, 1] });
assert.equal(storage.has(handoffKey), false, "consumed handoff is removed");
recreatedHandoffCard._applyPresentationMode();
flushTimers();
assert.equal(handoffDispatches, 1, "consumed handoff cannot reopen");

// Wrong fingerprints do not consume another card's marker; expired markers
// are removed without navigation or event dispatch.
storage.set(handoffKey, JSON.stringify({
  version: 1,
  token: "wrong-fingerprint",
  createdAt: Date.now(),
  urlKey: "/dashboard-kc761/0",
  fingerprint: "not-this-card",
}));
const mismatchCard = new Card();
mismatchCard.isConnected = true;
mismatchCard.setConfig(handoffConfig);
mismatchCard.parentElement = { path: [0, 4, 1], parentElement: null };
mismatchCard.preview = true;
flushTimers();
assert.equal(mismatchCard.lastEvent, undefined, "mismatched marker does not open a card");
assert.ok(storage.get(handoffKey), "mismatched marker is not consumed by the wrong card");
storage.set(handoffKey, JSON.stringify({
  version: 1,
  token: "expired",
  createdAt: Date.now() - 60000,
  urlKey: "/dashboard-kc761/0",
  fingerprint: mismatchCard._configFingerprint(),
}));
mismatchCard._applyPresentationMode();
assert.equal(storage.has(handoffKey), false, "expired marker is removed");

// Two identical cards are ambiguous: consume the marker but do not guess an
// array index or open either card.
normalHandoffCard._openEditor();
const duplicateA = new Card();
const duplicateB = new Card();
for (const duplicate of [duplicateA, duplicateB]) {
  duplicate.isConnected = true;
  duplicate.setConfig(handoffConfig);
  duplicate.parentElement = { path: [0, 4, 1], parentElement: null };
  duplicate.preview = true;
}
flushTimers();
flushTimers();
assert.equal(duplicateA.lastEvent, undefined, "ambiguous card A is not guessed");
assert.equal(duplicateB.lastEvent, undefined, "ambiguous card B is not guessed");
assert.equal(storage.has(handoffKey), false, "ambiguous marker is discarded safely");

card._data = { points: [[0, 1, 5], [321, 100, 50], [2047, 200, 4]] };
card._cursor.data = 321;
const chartOption = card._option();
assert.equal(chartOption.series[0].markLine.data.length, 1, "ROI boundaries are markArea-only");
assert.equal(chartOption.series[0].markPoint.label.distance, 28, "peak label is staggered from a nearby data cursor");
assert.equal(chartOption.grid.bottom, 58, "the zoom slider reserves only internal chart space");
assert.equal(chartOption.dataZoom.length, 2, "inside and visible slider zoom are both present");
assert.equal(chartOption.dataZoom[0].type, "inside");
assert.deepEqual(chartOption.dataZoom[0].xAxisIndex, [0]);
assert.equal(chartOption.dataZoom[0].filterMode, "none");
assert.equal(chartOption.dataZoom[0].zoomOnMouseWheel, "shift");
assert.equal(chartOption.dataZoom[0].moveOnMouseMove, true);
assert.equal(chartOption.dataZoom[0].moveOnMouseWheel, false);
assert.equal(chartOption.dataZoom[1].type, "slider");
assert.deepEqual(chartOption.dataZoom[1].xAxisIndex, [0]);
assert.equal(chartOption.dataZoom[1].filterMode, "none");
assert.equal(chartOption.dataZoom[1].height, 14);

const zoomCard = new Card();
zoomCard.setConfig({ entity: "sensor.zoom_spectrum" });
const fullPoints = Array.from({ length: 2048 }, (_, index) => [index, index / 2, index + 1]);
zoomCard._setData({ snapshot_id: "latest", source: 0, channel: 0, points: fullPoints });
zoomCard._setZoomRange(20, 40);
assert.deepEqual(zoomCard._zoom, { start: 20, end: 40 }, "zoom follows the ECharts datazoom range");
assert.equal(zoomCard.shadowRoot.querySelector("#reset-zoom").disabled, false);
assert.match(zoomCard.shadowRoot.querySelector("#zoom-readout").textContent, /^View CH 409–819$/);
zoomCard._onZoom({ batch: [{ start: 25, end: 75 }] });
assert.deepEqual(zoomCard._zoom, { start: 25, end: 75 }, "ECharts datazoom events update card-local state");
assert.equal(zoomCard._option().dataZoom[0].start, 25);
assert.equal(zoomCard._option().dataZoom[1].end, 75);
const preservedZoom = zoomCard._zoom;
zoomCard._setData({ snapshot_id: "latest", source: 0, channel: 0, points: fullPoints.map((point) => [point[0], point[1], point[2] + 1]) });
assert.deepEqual(zoomCard._zoom, preservedZoom, "same dataset identity preserves zoom on refresh");
zoomCard._setData({ snapshot_id: "saved-1", source: 0, channel: 0, points: fullPoints });
assert.deepEqual(zoomCard._zoom, { start: 0, end: 100 }, "history identity change resets zoom");
zoomCard._setZoomRange(50, 50);
assert.ok(zoomCard._zoom.end - zoomCard._zoom.start >= 0.25, "full spectra honor the minimum zoom span");
zoomCard._setData({ snapshot_id: "short", source: 0, channel: 0, points: [[2, 1, 5], [17, 10, 8]] });
zoomCard._setZoomRange(50, 50);
assert.deepEqual(zoomCard._zoom, { start: 0, end: 100 }, "two-point spectra retain a usable minimum span");
const dispatches = [];
zoomCard._chart = { dispatchAction: (action) => dispatches.push(action) };
zoomCard._setZoomRange(10, 60);
zoomCard._resetZoom();
assert.deepEqual(dispatches, [{ type: "dataZoom", dataZoomIndex: [0, 1], start: 0, end: 100 }]);
assert.equal(zoomCard.shadowRoot.querySelector("#reset-zoom").disabled, true);

const disposedChart = {
  offCalls: [],
  disposeCalls: 0,
  off: (name, handler) => disposedChart.offCalls.push([name, handler]),
  dispose: () => { disposedChart.disposeCalls += 1; },
};
const disposedCard = Object.create(Card.prototype);
const zoomHandler = () => {};
disposedCard._chart = disposedChart;
disposedCard._zoomHandler = zoomHandler;
disposedCard._resizeObserver = { disconnectCalls: 0, disconnect() { this.disconnectCalls += 1; } };
disposedCard._disposeChart();
assert.deepEqual(disposedChart.offCalls, [["datazoom", zoomHandler]], "datazoom listener is removed before dispose");
assert.equal(disposedChart.disposeCalls, 1);
assert.equal(disposedCard._resizeObserver, null);
assert.equal(disposedCard._zoomHandler, null);
assert.equal(disposedCard._chart, null);

// Sections writes a numeric grid_options.rows when Automatic height is off.
// The card must preserve that config for HA's wrapper while its own host/ha-card
// CSS follows the wrapper height and scrolls its content instead of overflowing
// into the next grid item.
card.setConfig({ entity: "sensor.real_spectrum", grid_options: { columns: "full", rows: 8 } });
assert.deepEqual(card._config.grid_options, { columns: "full", rows: 8 });

const preference = Object.create(Card.prototype);
preference._config = { entity: "sensor.real_spectrum" };
assert.equal(preference._initialYAxis("linear", true), "linear");
preference._yAxis = "log";
preference._persistYAxis();
const refreshed = Object.create(Card.prototype);
refreshed._config = { entity: "sensor.real_spectrum" };
assert.equal(refreshed._initialYAxis("linear", true), "log");
storage.set("kc761-spectrum:y-axis:selected:sensor.real_spectrum", "linear");
storage.set("kc761-spectrum:y-axis:sensor.real_spectrum", "linear");
const changedByEditor = Object.create(Card.prototype);
changedByEditor._config = { entity: "sensor.real_spectrum" };
assert.equal(changedByEditor._initialYAxis("log", true), "log");
assert.equal(storage.get("kc761-spectrum:y-axis:configured:sensor.real_spectrum"), "log");

card._device = { model: "KC761" };
card._entryId = "old-entry";
card._data = { points: [] };
card._history = [{ snapshot_id: "old" }];
card._refreshTimer = 1;
card.setConfig({ entity: "sensor.other_spectrum" });
assert.equal(card._device, null);
assert.equal(card._entryId, null);
assert.equal(card._data, null);
assert.deepEqual(card._history, []);
assert.equal(card._refreshTimer, null);
card._setData({ points: [[2, 1, 5], [17, 10, 8]] });
card._setCursorChannel("data", 2047);
assert.equal(card._cursor.data, 17, "short spectra clamp the data cursor to the nearest available channel");
assert.equal(card.shadowRoot.querySelector("#data-cursor-slider").value, "17");
assert.equal(card.shadowRoot.querySelector("#data-cursor-slider").getAttribute("aria-valuenow"), "17");
assert.match(card.shadowRoot.querySelector("#data-cursor-readout").getAttribute("aria-label"), /CH 17/);
assert.equal(card.shadowRoot.querySelector("#data-cursor-slider").getAttribute("aria-valuemax"), "2047");
card._hass = { callWS: () => { throw new Error("getter must not call network"); } };
assert.equal(card.getCardSize(), 9);
assert.deepEqual(card.getGridOptions(), { columns: "full", min_columns: 6 });

const liveState = { attributes: { codec_profile: "manual_v1_8_candidate", points: [] } };
const hassCard = new Card();
hassCard.setConfig({ entity: "sensor.live_spectrum" });
hassCard.hass = { states: { "sensor.live_spectrum": liveState } };
assert.ok(hassCard._data);
hassCard._data = null;
hassCard.hass = { states: { "sensor.live_spectrum": liveState } };
assert.ok(hassCard._data, "same state object must repopulate a cleared latest spectrum");

const unavailableCard = new Card();
unavailableCard.setConfig({ entity: "sensor.unavailable_spectrum" });
unavailableCard.preview = true;
unavailableCard._entryId = "entry";
unavailableCard._device = { model: "KC761" };
unavailableCard._data = { points: [[0, 1, 2]] };
unavailableCard.hass = {
  states: {
    "sensor.unavailable_spectrum": {
      state: "unavailable",
      attributes: {},
    },
  },
};
assert.equal(unavailableCard.shadowRoot.querySelector("#status").textContent, "Selected spectrum entity is unavailable");
assert.deepEqual(unavailableCard._data, { points: [[0, 1, 2]] }, "unavailable state keeps last valid spectrum");
assert.equal(unavailableCard._entryId, "entry", "unavailable state keeps entity ownership");
assert.equal(unavailableCard._device.model, "KC761", "unavailable state keeps device metadata");
assert.equal(
  Card.isSpectrumEntityState({ state: "unavailable", attributes: {} }),
  false,
  "unavailable state remains outside strict spectrum validation",
);

const lifecycle = new Card();
lifecycle.setConfig({ entity: "sensor.lifecycle" });
assert.ok(lifecycle._layoutResizeObserver);
const layoutObserver = lifecycle._layoutResizeObserver;
lifecycle.disconnectedCallback();
assert.equal(layoutObserver.disconnectCalls, 1);
assert.equal(lifecycle._layoutResizeObserver, null);

console.log("frontend card contract: ok");
