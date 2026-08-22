import assert from "node:assert/strict";

class FakeNode {
  constructor(selector = "") {
    this.selector = selector;
    this.style = { setProperty() {} };
    this.classList = { toggle() {} };
    this.dataset = {};
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
  setAttribute(name, value) { this[name] = value; }
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
  }

  attachShadow() {
    this.shadowRoot = new FakeShadowRoot();
    return this.shadowRoot;
  }
}

const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;
globalThis.customElements = {
  get: (name) => registry.get(name),
  define: (name, value) => registry.set(name, value),
};
const storage = new Map();
globalThis.window = {
  customCards: [],
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
  },
  addEventListener() {},
  removeEventListener() {},
  setInterval: () => 1,
  clearInterval() {},
  confirm: () => false,
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
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
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
assert.equal(card.shadowRoot.querySelector("#controls").hidden, true);
assert.equal(card.shadowRoot.querySelector(".summary").hidden, false);
assert.equal(card.shadowRoot.querySelector('[data-summary="peak"]').hidden, true);
assert.equal(card.shadowRoot.querySelector('[data-legend="peak"]').hidden, true);
card.setConfig({ entity: "sensor.real_spectrum", show_summary: false, show_legend: false, show_actions: false });
assert.equal(card.shadowRoot.querySelector(".summary").hidden, true);
assert.equal(card.shadowRoot.querySelector(".legend").hidden, true);
assert.equal(card.shadowRoot.querySelector("#actions").hidden, true);
assert.equal(card.getCardSize(), 9);
assert.deepEqual(card.getGridOptions(), { columns: "full", min_columns: 6 });

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
let resetCleanupCalls = 0;
card._dragCleanup = () => { resetCleanupCalls += 1; };
card.setConfig({ entity: "sensor.other_spectrum" });
assert.equal(resetCleanupCalls, 1);
assert.equal(card._dragCleanup, null);
assert.equal(card._device, null);
assert.equal(card._entryId, null);
assert.equal(card._data, null);
assert.deepEqual(card._history, []);
assert.equal(card._refreshTimer, null);
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

let cleanupCalls = 0;
const lifecycle = new Card();
lifecycle.setConfig({ entity: "sensor.lifecycle" });
lifecycle._dragCleanup = () => { cleanupCalls += 1; };
lifecycle.disconnectedCallback();
assert.equal(cleanupCalls, 1);
assert.equal(lifecycle._dragCleanup, null);

console.log("frontend card contract: ok");
