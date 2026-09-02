import assert from "node:assert/strict";

class FakeNode {
  constructor(selector = "") {
    this.selector = selector;
    this.localName = selector;
    this.tagName = selector.toUpperCase();
    this.style = {
      values: new Map(),
      setProperty: (name, value) => this.style.values.set(name, String(value)),
      getPropertyValue: (name) => this.style.values.get(name) || "",
    };
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
    this.listenerCallbacks = new Map();
    this.parentNode = null;
    this.children = [];
    this.isConnected = false;
    this.textContent = "";
  }

  addEventListener(type, callback) {
    this.listeners.set(type, (this.listeners.get(type) || 0) + 1);
    if (typeof callback === "function") this.listenerCallbacks.set(type, callback);
  }
  removeEventListener(type, callback) {
    if (!this.listeners.has(type)) return;
    this.listeners.set(type, Math.max(0, this.listeners.get(type) - 1));
    if (!callback || this.listenerCallbacks.get(type) === callback) this.listenerCallbacks.delete(type);
  }
  click() {
    this.clicked = true;
    this.listenerCallbacks.get("click")?.({ currentTarget: this, target: this, type: "click" });
  }
  emit(type, init = {}) {
    const event = { ...init, currentTarget: this, target: init.target || this, type };
    this.listenerCallbacks.get(type)?.(event);
  }
  listenerCount(type) { return this.listeners.get(type) || 0; }
  setPointerCapture() {}
  setAttribute(name, value) { this.attributes.set(name, String(value)); this[name] = String(value); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  querySelector(selector) { return selector === ".caption" ? new FakeNode(selector) : new FakeNode(selector); }
  querySelectorAll(selector) {
    const matches = [];
    const visit = (node) => {
      for (const child of node.children || []) {
        if (selector === "*" || (selector === "hui-error-card" && child.localName === "hui-error-card")) {
          matches.push(child);
        }
        visit(child);
      }
    };
    visit(this);
    return matches;
  }
  replaceChildren(...items) { this.options = [...items]; }
  append(...items) {
    this.options.push(...items);
    items.forEach((item) => {
      if (item && typeof item === "object") {
        item.parentNode = this;
        item.isConnected = true;
        if (!this.children.includes(item)) this.children.push(item);
      }
    });
  }
  appendChild(item) {
    this.append(item);
    return item;
  }
  removeChild(item) {
    this.children = this.children.filter((child) => child !== item);
    this.options = this.options.filter((child) => child !== item);
    if (item && typeof item === "object") {
      item.parentNode = null;
      item.isConnected = false;
    }
    return item;
  }
  remove() { this.parentNode?.removeChild?.(this); }
  dispatchEvent(event) {
    this.lastEvent = event;
    this.dispatchCount = (this.dispatchCount || 0) + 1;
    return true;
  }
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

  querySelectorAll(selector) {
    if (selector !== "[data-clear]") return [];
    return ["spectrum", "dose_curve", "cumulative_dose"].map((kind) => {
      const node = this.querySelector(`[data-clear=${kind}]`);
      node.dataset.clear = kind;
      return node;
    });
  }
}

class FakeHTMLElement {
  constructor() {
    this.style = {
      values: new Map(),
      setProperty: (name, value) => this.style.values.set(name, String(value)),
      getPropertyValue: (name) => this.style.values.get(name) || "",
    };
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
let storageWrites = 0;
const storageApi = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => { storageWrites += 1; storage.set(key, String(value)); },
  removeItem: (key) => storage.delete(key),
};
globalThis.window = {
  customCards: [],
  sessionStorage: storageApi,
  localStorage: storageApi,
  _listenerCounts: new Map(),
  addEventListener(type) { this._listenerCounts.set(type, (this._listenerCounts.get(type) || 0) + 1); },
  removeEventListener(type) { this._listenerCounts.set(type, Math.max(0, (this._listenerCounts.get(type) || 0) - 1)); },
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
let fakeClock = 0;
globalThis.setTimeout = (callback, delay = 0) => {
  const id = ++nextTimerId;
  scheduledTimers.set(id, { callback, due: fakeClock + Math.max(0, Number(delay) || 0) });
  return id;
};
globalThis.clearTimeout = (id) => scheduledTimers.delete(id);
const flushTimers = () => {
  const callbacks = [...scheduledTimers.values()].map((entry) => entry.callback);
  scheduledTimers.clear();
  callbacks.forEach((callback) => callback());
};
const advanceTimers = (milliseconds) => {
  fakeClock += milliseconds;
  let guard = 0;
  while (guard < 1000) {
    const ready = [...scheduledTimers.entries()].filter(([, entry]) => entry.due <= fakeClock);
    if (!ready.length) break;
    ready.forEach(([id, entry]) => {
      scheduledTimers.delete(id);
      entry.callback();
    });
    guard += ready.length;
  }
};
globalThis.CustomEvent = class {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
    this.bubbles = init.bubbles;
    this.composed = init.composed;
  }
};
const documentBody = new FakeNode("body");
documentBody.isConnected = true;
const preExistingCardHost = new FakeNode("hui-card");
preExistingCardHost.config = { type: "custom:kc761-spectrum-card" };
const preExistingErrorCard = new FakeNode("hui-error-card");
preExistingErrorCard.textContent = "Configuration error";
preExistingCardHost.append(preExistingErrorCard);
preExistingErrorCard.isConnected = false;
const documentNodes = [preExistingCardHost, preExistingErrorCard];
globalThis.document = {
  body: documentBody,
  visibilityState: "visible",
  _listeners: new Map(),
  createElement: (selector) => new FakeNode(selector),
  querySelectorAll: () => documentNodes.filter((node) => node.isConnected !== false),
  addEventListener(type, callback) {
    this._listeners.set(type, callback);
  },
  removeEventListener(type, callback) {
    if (this._listeners.get(type) === callback) this._listeners.delete(type);
  },
};
const animationFrames = new Map();
let nextAnimationFrame = 0;
globalThis.requestAnimationFrame = (callback) => {
  const id = ++nextAnimationFrame;
  animationFrames.set(id, callback);
  return id;
};
globalThis.cancelAnimationFrame = (id) => animationFrames.delete(id);
const flushAnimationFrames = () => {
  const callbacks = [...animationFrames.values()];
  animationFrames.clear();
  callbacks.forEach((callback) => callback(Date.now()));
};
globalThis.MutationObserver = class {
  static instances = [];
  constructor(callback) {
    this.callback = callback;
    this.observeCalls = [];
    this.disconnectCalls = 0;
    globalThis.MutationObserver.instances.push(this);
  }

  observe(target, options) { this.observeCalls.push({ target, options }); }
  disconnect() { this.disconnectCalls += 1; }
  trigger(records) { this.callback(records); }
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
const rebuildRegistry = window.__kc761SpectrumRebuildRegistry;
const rebuildObserver = MutationObserver.instances.at(-1);
assert.ok(rebuildRegistry.active, "late registration window remains active");
assert.equal(preExistingErrorCard.dispatchCount || 0, 0, "a detached error card waits for insertion");
advanceTimers(12000);
assert.ok(rebuildRegistry.active, "recovery remains active after the dashboard appears later");
const nonKC761RebuildCard = new FakeNode("hui-error-card");
nonKC761RebuildCard.config = { type: "markdown" };
nonKC761RebuildCard.isConnected = true;
const detachedRebuildCard = new FakeNode("hui-error-card");
detachedRebuildCard.config = { type: "error", message: "Custom element doesn't exist: kc761-spectrum-card." };
detachedRebuildCard.textContent = "Custom element doesn't exist: kc761-spectrum-card.";
detachedRebuildCard.isConnected = false;
preExistingCardHost.isConnected = true;
preExistingErrorCard.isConnected = true;
rebuildObserver.trigger([{ addedNodes: [nonKC761RebuildCard, detachedRebuildCard, preExistingCardHost] }]);
assert.equal(nonKC761RebuildCard.dispatchCount || 0, 0, "non-KC761 error card is ignored");
assert.equal(detachedRebuildCard.dispatchCount, 1, "detached KC761 error card is rebuilt through its direct listener");
assert.equal(preExistingErrorCard.dispatchCount, 1, "late registration rebuilds an attached KC761 error card once");
assert.equal(preExistingErrorCard.lastEvent?.type, "ll-rebuild");
assert.equal(preExistingErrorCard.lastEvent?.bubbles, true);
assert.equal(preExistingErrorCard.lastEvent?.composed, true);
assert.equal(rebuildRegistry.active, true, "the page-lifetime observer remains available for later cards");
rebuildObserver.trigger([{ addedNodes: [preExistingErrorCard] }]);
assert.equal(preExistingErrorCard.dispatchCount, 1, "a rebuilt error-card node is not retriggered");
documentNodes.splice(0);

// Targeted queries must find a KC761 error card after more than the old
// alpha34 96-root budget, without spending the generic node budget first.
const lateTargetCard = new FakeNode("hui-error-card");
lateTargetCard.config = { type: "custom:kc761-spectrum-card" };
lateTargetCard.isConnected = true;
const lateRootHosts = Array.from({ length: 120 }, (_, index) => {
  const host = new FakeNode(`late-root-host-${index}`);
  host.isConnected = true;
  const root = new FakeShadowRoot();
  root.isConnected = true;
  root.querySelectorAll = (selector) => selector === "hui-error-card" && index === 119 ? [lateTargetCard] : [];
  host.shadowRoot = root;
  return host;
});
documentNodes.push(...lateRootHosts);
const targetedRegistry = window.__kc761SpectrumRebuildRegistry;
targetedRegistry.active = true;
targetedRegistry.cleanupReason = "";
targetedRegistry.observedRoots = new Set([document]);
targetedRegistry.observers = [rebuildObserver];
targetedRegistry.scannedNodes = 0;
targetedRegistry.scanRoots = 1;
targetedRegistry.eventCount = 0;
targetedRegistry.dispatchedCount = 0;
targetedRegistry.targetQueryCount = 0;
targetedRegistry.targetedQueryHit = false;
targetedRegistry.candidateSeenButBlocked = false;
targetedRegistry.budgetReason = "";
targetedRegistry.truncated = false;
rebuildObserver.trigger([{ addedNodes: lateRootHosts }]);
assert.equal(lateTargetCard.dispatchCount, 1, "targeted query reaches a card after 96 roots");
assert.equal(targetedRegistry.active, true, "targeted dispatch keeps the page-lifetime observer available");
assert.equal(targetedRegistry.cleanupReason, "");
assert.equal(targetedRegistry.targetedQueryHit, true);
assert.equal(targetedRegistry.candidateSeenButBlocked, false);
assert.ok(targetedRegistry.scanRoots > 96 && targetedRegistry.scanRoots <= 160);
assert.ok(targetedRegistry.targetQueryCount > 96 && targetedRegistry.targetQueryCount <= 256);
documentNodes.splice(0);

const Card = customElements.get("kc761-spectrum-card");
assert.ok(Card);
assert.equal(Card.getStubConfig().entity, undefined);
assert.equal(Card.getStubConfig().show_peak, true);
assert.equal(
  window.customCards.find((item) => item.type === "kc761-spectrum-card")?.preview,
  true,
  "the card picker renders the real card preview",
);

const form = Card.getConfigForm();
const schemaNames = [];
const collect = (schema) => schema.forEach((item) => {
  if (item.name) schemaNames.push(item.name);
  if (item.schema) collect(item.schema);
});
collect(form.schema);
for (const name of ["entity", "title", "x_axis", "y_axis", "refresh_interval", "show_controls", "show_summary", "show_legend", "show_actions", "show_peak", "diagnostics"]) {
  assert.ok(schemaNames.includes(name), `missing form field ${name}`);
}
assert.doesNotThrow(() => form.assertConfig({ title: "picker placeholder" }));
assert.throws(() => Card.validateConfig({ entity: "sensor.kc761_spectrum", refresh_interval: 4 }), /refresh_interval/);
assert.throws(() => Card.validateConfig({ entity: "sensor.kc761_spectrum", y_axis: "sqrt" }), /y_axis/);
assert.throws(() => Card.validateConfig({ entity: "sensor.kc761_spectrum", show_peak: "yes" }), /show_peak/);
assert.throws(() => Card.validateConfig({ entity: "sensor.kc761_spectrum", diagnostics: "yes" }), /diagnostics/);

const state = { attributes: { config_entry_id: "entry", codec_profile: "manual_v1_8_candidate", points: Array.from({ length: 2048 }, (_, i) => [i, i / 2, i]) } };
const suggestion = Card.getEntitySuggestion({ states: { "sensor.real_spectrum": state } }, "sensor.real_spectrum");
assert.deepEqual(Object.keys(suggestion), ["config"]);
assert.deepEqual(Object.keys(suggestion.config).sort(), ["diagnostics", "entity", "refresh_interval", "show_actions", "show_controls", "show_legend", "show_peak", "show_summary", "title", "type", "x_axis", "y_axis"].sort());
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
assert.equal(card._sourceSelectionExplicit, false);
card.shadowRoot.querySelector("#source").emit("change");
assert.equal(card._sourceSelectionExplicit, true, "a user channel change is tracked as explicit");
assert.equal(card.shadowRoot.querySelector("#roi-start").listenerCount("input"), 1);
assert.equal(card.shadowRoot.querySelector("#roi-end").listenerCount("input"), 1);
assert.equal(card.shadowRoot.querySelector("#data-cursor-slider").listenerCount("input"), 1);
assert.equal(card.shadowRoot.querySelector("#data-cursor-slider").getAttribute?.("role") ?? "slider", "slider");
assert.equal(card.shadowRoot.querySelector("#data-cursor-slider").listenerCount("pointerdown"), 0);
assert.equal(card.shadowRoot.querySelector("#reset-zoom").listenerCount("click"), 1);
assert.equal(card.shadowRoot.querySelector("#reset-zoom").disabled, true);
assert.match(card.shadowRoot.querySelector("#zoom-readout").getAttribute("aria-label"), /^View CH /);
assert.equal(card.shadowRoot.querySelector("#roi-start-value").textContent, "Start CH 0");
assert.equal(card.shadowRoot.querySelector("#roi-end-value").textContent, "End CH 2047");
assert.equal(card.shadowRoot.querySelector("#roi-start-value").getAttribute("aria-label"), "Start channel 0");
assert.equal(card.shadowRoot.querySelector("#roi-end-value").getAttribute("aria-label"), "End channel 2047");
assert.equal(card.shadowRoot.querySelector("#controls").hidden, true);
assert.equal(card.shadowRoot.querySelector("#editor-controls").hidden, true);
assert.equal(card.shadowRoot.querySelector("#configure").hidden, false);
assert.equal(card.shadowRoot.querySelector(".summary").hidden, false);
assert.equal(card.shadowRoot.querySelector('[data-summary="peak"]').hidden, true);
assert.equal(card.shadowRoot.querySelector('[data-legend="peak"]').hidden, true);

// A latest entity can retain a spectrum captured from the previous channel
// after a device replacement.  Initial loading must ask the read-only capture
// API for the device's active channel, while a user-selected channel remains
// authoritative.
const activeSourceCard = new Card();
activeSourceCard.setConfig({ entity: "sensor.active_source_spectrum" });
activeSourceCard.isConnected = true;
activeSourceCard._entryId = "entry-active-source";
activeSourceCard._data = { source: 2, points: [[0, 1, 2]] };
activeSourceCard._draw = () => {};
activeSourceCard._updateSummary = () => {};
const activeSourceCalls = [];
activeSourceCard._hass = {
  callWS: async (message) => {
    activeSourceCalls.push(message);
    if (message.type === "kc761/info") return { sensor_types: [1, 0, 1], destructive_actions_enabled: false };
    if (message.type === "kc761/spectrum/capture") {
      assert.equal("source" in message, false, "active-source initialization omits a stale source");
      return { source: 0, points: [[0, 1, 2]], acquired_at: "2026-08-25T00:00:00Z", total_counts: 3 };
    }
    throw new Error(`unexpected active-source websocket call ${message.type}`);
  },
};
await activeSourceCard._loadInitial();
assert.deepEqual(activeSourceCalls.map((message) => message.type), ["kc761/info", "kc761/spectrum/capture"]);
assert.equal(activeSourceCard._data.source, 0, "Latest follows the device active sensor");
assert.equal(activeSourceCard.shadowRoot.querySelector("#source").value, "0");

const explicitSourceCard = new Card();
explicitSourceCard.setConfig({ entity: "sensor.explicit_source_spectrum" });
explicitSourceCard.isConnected = true;
explicitSourceCard._entryId = "entry-explicit-source";
explicitSourceCard._data = { source: 2, points: [[0, 1, 2]] };
explicitSourceCard._draw = () => {};
explicitSourceCard._updateSummary = () => {};
explicitSourceCard._sourceSelectionExplicit = true;
explicitSourceCard.shadowRoot.querySelector("#source").value = "2";
const explicitSourceCalls = [];
explicitSourceCard._hass = {
  callWS: async (message) => {
    explicitSourceCalls.push(message);
    if (message.type === "kc761/info") return { sensor_types: [1, 1, 1], destructive_actions_enabled: false };
    throw new Error(`unexpected explicit-source websocket call ${message.type}`);
  },
};
await explicitSourceCard._loadInitial();
assert.deepEqual(explicitSourceCalls.map((message) => message.type), ["kc761/info"]);
assert.equal(explicitSourceCard.shadowRoot.querySelector("#source").value, "2", "explicit channel selection is preserved");

// Diagnostics are opt-in, session-only, bounded, redacted, and must not add
// storage writes or UI when disabled.
const diagnosticsKey = "kc761-spectrum:diagnostics:v7";
storage.delete(diagnosticsKey);
storageWrites = 0;
const diagnosticsOffCard = new Card();
diagnosticsOffCard.setConfig({ entity: "sensor.diagnostics_off" });
assert.equal(storageWrites, 0, "diagnostics off must not write session storage");
assert.equal(diagnosticsOffCard.shadowRoot.querySelector("#export-diagnostics").hidden, true);

const diagnosticsCard = new Card();
diagnosticsCard.setConfig({ entity: "sensor.diagnostics_enabled", diagnostics: true });
assert.equal(diagnosticsCard.shadowRoot.querySelector("#export-diagnostics").hidden, false);
const diagnosticsRegistry = window.__kc761SpectrumDiagnosticsRegistry;
assert.ok(diagnosticsRegistry.globalButton, "diagnostics installs a body-level export button");
assert.equal(diagnosticsRegistry.globalButton.textContent, "Export KC761 diagnostics");
assert.equal(window._listenerCounts.get("error"), 1, "diagnostics installs one window error listener");
assert.equal(window._listenerCounts.get("unhandledrejection"), 1, "diagnostics installs one rejection listener");
let diagnosticsSnapshot = JSON.parse(storage.get(diagnosticsKey));
assert.equal(diagnosticsSnapshot.schema, 7);
assert.equal(diagnosticsSnapshot.enabled, true);
assert.deepEqual(Object.keys(diagnosticsSnapshot).sort(), ["coalesced_events", "dropped_events", "dropped_failures", "enabled", "events", "export_snapshot", "failures", "observer_stats", "redacted", "schema", "sequence", "version"].sort());
assert.ok(diagnosticsSnapshot.events.some((event) => event.event === "set-config-valid"));
assert.ok(diagnosticsSnapshot.events.every((event) => Number.isSafeInteger(event.page_session) && event.page_session > 0));
assert.ok(Number.isSafeInteger(JSON.parse(storage.get(diagnosticsKey)).events.at(-1).page_session));
diagnosticsCard._recordDiagnostic("registration-rebuild-dispatch", {
  dispatched: true,
  dispatch_succeeded: true,
  dispatch_outcome: "dispatched",
  found_at: "2026-08-24T00:00:00.000Z",
  targeted_query_hit: true,
  target_query_count: 121,
  target_registry_defined: false,
  truncated: false,
});
diagnosticsCard._recordDiagnostic("registration-rebuild-cleanup", {
  cleanup_reason: "timeout",
  budget_reason: "time",
  candidate_seen_but_blocked: false,
  truncated: true,
  dispatched_count: 0,
});
diagnosticsSnapshot = JSON.parse(storage.get(diagnosticsKey));
const rebuildDispatchEvent = diagnosticsSnapshot.events.find((event) => event.event === "registration-rebuild-dispatch");
assert.equal(rebuildDispatchEvent.dispatched, true);
assert.equal(rebuildDispatchEvent.dispatch_succeeded, true);
assert.equal(rebuildDispatchEvent.dispatch_outcome, "dispatched");
assert.equal(rebuildDispatchEvent.target_registry_defined, false);
assert.equal(rebuildDispatchEvent.found_at, "2026-08-24T00:00:00.000Z");
assert.equal(rebuildDispatchEvent.targeted_query_hit, true);
assert.equal(rebuildDispatchEvent.target_query_count, 121);
const rebuildCleanupEvent = diagnosticsSnapshot.events.find((event) => event.event === "registration-rebuild-cleanup");
assert.equal(rebuildCleanupEvent.cleanup_reason, "timeout");
assert.equal(rebuildCleanupEvent.budget_reason, "time");
assert.equal(rebuildCleanupEvent.candidate_seen_but_blocked, false);
assert.equal(rebuildCleanupEvent.truncated, true);
diagnosticsCard._recordDiagnostic("hass-entry", { has_hass: true });
diagnosticsCard._recordDiagnostic("hass-entry", { has_hass: true });
diagnosticsSnapshot = JSON.parse(storage.get(diagnosticsKey));
assert.ok(diagnosticsSnapshot.coalesced_events >= 1, "repeated readiness events are coalesced");
assert.ok(diagnosticsSnapshot.events.some((event) => event.event === "hass-entry" && event.repeat >= 2));
assert.equal(JSON.stringify(diagnosticsSnapshot).includes("sensor.diagnostics_enabled"), false);
assert.equal(JSON.stringify(diagnosticsSnapshot).includes("points"), false);
assert.equal(JSON.stringify(diagnosticsSnapshot).includes("192.168."), false);

for (let index = 0; index < 120; index += 1) {
  assert.throws(() => diagnosticsCard.setConfig("not-an-object"), /configuration must be an object/);
}
const recordRedactionFailure = () => diagnosticsCard._recordDiagnosticError("test-redaction", new Error('sensor.secret_spectrum at 192.0.2.127:7601 host=kc761.local token=secret "points":[1,2] device_id=abc'));
recordRedactionFailure();
recordRedactionFailure();
for (let index = 0; index < 140; index += 1) diagnosticsCard._recordDiagnostic("normal-noise", { count: index });
diagnosticsSnapshot = JSON.parse(storage.get(diagnosticsKey));
assert.ok(diagnosticsSnapshot.events.length <= 100, "diagnostic events are ring-buffer bounded");
assert.ok(diagnosticsSnapshot.failures.length <= 20, "diagnostic failures are sticky but bounded");
assert.ok(diagnosticsSnapshot.failures.some((failure) => failure.event === "test-redaction"), "failure survives normal-event churn");
assert.ok(diagnosticsSnapshot.failures.find((failure) => failure.event === "test-redaction")?.repeat >= 2, "identical failures are deduplicated");
assert.ok(JSON.stringify(diagnosticsSnapshot).length <= 60000, "diagnostic payload is size bounded");
const redactedText = JSON.stringify(diagnosticsSnapshot);
assert.equal(redactedText.includes("sensor.secret_spectrum"), false);
assert.equal(redactedText.includes("192.0.2.127"), false);
assert.equal(redactedText.includes("kc761.local"), false);
assert.equal(redactedText.includes("token=secret"), false);
assert.equal(redactedText.includes("\"points\":[1,2]"), false);
assert.equal(redactedText.includes("device_id=abc"), false);

const errorCard = new FakeNode("hui-error-card");
errorCard.textContent = "配置错误 sensor.hidden at 192.0.2.127 token=secret points=[1,2]";
const activeObserver = globalThis.MutationObserver.instances.at(-1);
assert.ok(activeObserver, "diagnostics installs an error-card observer");
activeObserver.trigger([{ addedNodes: [errorCard] }]);
diagnosticsSnapshot = JSON.parse(storage.get(diagnosticsKey));
const observedFailure = diagnosticsSnapshot.failures.find((failure) => failure.event === "lovelace-error-card");
assert.ok(observedFailure, "diagnostics records a Lovelace error card");
assert.equal(observedFailure.config_type_is_kc761, "unknown");
assert.equal(typeof observedFailure.node_path, "string");
assert.ok([true, false, "unknown"].includes(observedFailure.resource_version_match));
assert.equal(typeof observedFailure.custom_element_defined, "boolean");
assert.equal(typeof observedFailure.module_evaluated, "boolean");
assert.equal(typeof observedFailure.card_instances, "number");
assert.equal(JSON.stringify(observedFailure).includes("sensor.hidden"), false);
assert.equal(JSON.stringify(observedFailure).includes("192.0.2.127"), false);
assert.equal(JSON.stringify(observedFailure).includes("token=secret"), false);
assert.equal(JSON.stringify(observedFailure).includes("points=[1,2]"), false);

const typedErrorCard = new FakeNode("hui-error-card");
typedErrorCard.config = { type: "custom:kc761-spectrum-card" };
typedErrorCard.textContent = "KC761 configuration error";
activeObserver.trigger([{ addedNodes: [typedErrorCard] }]);
activeObserver.trigger([{ addedNodes: [typedErrorCard] }]);
diagnosticsSnapshot = JSON.parse(storage.get(diagnosticsKey));
const typedFailures = diagnosticsSnapshot.failures.filter((failure) => failure.error_message?.includes("KC761 configuration error"));
assert.equal(typedFailures.length, 1, "the same error-card DOM node is recorded once");
assert.equal(typedFailures[0].config_type_is_kc761, true);

const otherErrorCard = new FakeNode("hui-error-card");
otherErrorCard.config = { type: "markdown" };
otherErrorCard.textContent = "Other card configuration error";
activeObserver.trigger([{ addedNodes: [otherErrorCard] }]);
diagnosticsSnapshot = JSON.parse(storage.get(diagnosticsKey));
const otherFailure = diagnosticsSnapshot.failures.find((failure) => failure.error_message?.includes("Other card configuration error"));
assert.equal(otherFailure.config_type_is_kc761, false);

const nestedErrorCard = new FakeNode("hui-error-card");
nestedErrorCard.textContent = "nested configuration error sensor.nested_secret at 192.0.2.127";
const nestedShadowRoot = new FakeNode("shadow-root");
nestedShadowRoot.querySelectorAll = (selector) => selector === "hui-error-card, ha-card.error" || selector === "*" ? [nestedErrorCard] : [];
const nestedShadowHost = new FakeNode("hui-view");
nestedShadowHost.shadowRoot = nestedShadowRoot;
activeObserver.trigger([{ addedNodes: [nestedShadowHost] }]);
diagnosticsSnapshot = JSON.parse(storage.get(diagnosticsKey));
const nestedFailure = diagnosticsSnapshot.failures.find((failure) => failure.error_message?.includes("nested configuration error"));
assert.ok(nestedFailure, "diagnostics observes an error card in an open nested shadow root");
assert.equal(JSON.stringify(nestedFailure).includes("sensor.nested_secret"), false);
assert.ok(diagnosticsSnapshot.observer_stats.roots >= 2);

// The continuous observer is intentionally capped.  A deep export must still
// inspect a larger accessible tree at click time, while keeping the root and
// node budgets bounded and redacting card text.
const originalDocumentQuery = globalThis.document.querySelectorAll;
const originalBodyQuery = globalThis.document.body.querySelectorAll;
const deepHosts = Array.from({ length: 280 }, (_, index) => new FakeNode(`ha-host-${index}`));
const deepErrorCard = new FakeNode("hui-error-card");
deepErrorCard.textContent = "配置错误 sensor.deep_secret at 192.0.2.127 token=secret points=[1,2]";
const deepShadowRoot = new FakeNode("shadow-root");
deepShadowRoot.querySelectorAll = (selector) => selector === "*" || selector === "hui-error-card, ha-card.error" ? [deepErrorCard] : [];
deepHosts[79].shadowRoot = deepShadowRoot;
globalThis.document.querySelectorAll = (selector) => selector === "*" ? deepHosts : [];
globalThis.document.body.querySelectorAll = globalThis.document.querySelectorAll;
assert.equal(diagnosticsSnapshot.failures.some((failure) => failure.event === "lovelace-error-card-snapshot"), false);
let deepExportedDiagnostics = "";
const deepOriginalBlob = globalThis.Blob;
const deepOriginalUrl = globalThis.URL;
globalThis.Blob = class {
  constructor(parts) { deepExportedDiagnostics = parts.join(""); }
};
globalThis.URL = { createObjectURL: () => "blob:deep", revokeObjectURL() {} };
diagnosticsRegistry.globalButton.click();
assert.match(deepExportedDiagnostics, /"schema": 7/);
assert.match(deepExportedDiagnostics, /lovelace-error-card-snapshot/);
assert.equal(deepExportedDiagnostics.includes("sensor.deep_secret"), false);
assert.equal(deepExportedDiagnostics.includes("192.0.2.127"), false);
assert.equal(deepExportedDiagnostics.includes("token=secret"), false);
assert.equal(deepExportedDiagnostics.includes("points=[1,2]"), false);
const deepPayload = JSON.parse(deepExportedDiagnostics);
assert.ok(Number.isSafeInteger(deepPayload.page_session) && deepPayload.page_session > 0);
assert.ok(deepPayload.diagnostics_context.resource_version_match === "unknown");
assert.ok(deepPayload.export_snapshot.roots <= 256);
assert.ok(deepPayload.export_snapshot.nodes <= 10000);
assert.ok(deepPayload.export_snapshot.error_cards >= 1);
globalThis.Blob = deepOriginalBlob;
globalThis.URL = deepOriginalUrl;
globalThis.document.querySelectorAll = originalDocumentQuery;
globalThis.document.body.querySelectorAll = originalBodyQuery;

let exportedDiagnostics = "";
const originalBlob = globalThis.Blob;
const originalUrl = globalThis.URL;
globalThis.Blob = class {
  constructor(parts) { exportedDiagnostics = parts.join(""); }
};
globalThis.URL = { createObjectURL: () => "blob:kc761", revokeObjectURL() {} };
assert.doesNotThrow(() => diagnosticsCard._exportDiagnostics());
assert.match(exportedDiagnostics, /"redacted": true/);
assert.match(exportedDiagnostics, /"schema": 7/);
assert.match(exportedDiagnostics, /"failures":/);
assert.match(exportedDiagnostics, /"observer_stats":/);
assert.match(exportedDiagnostics, /"diagnostics_context":/);
assert.equal(exportedDiagnostics.includes("sensor.secret_spectrum"), false);
globalThis.Blob = originalBlob;
globalThis.URL = originalUrl;

// A second enabled instance and a repeated module import share one pair of
// global listeners; neither import nor reload can multiply them.
const secondDiagnosticsCard = new Card();
secondDiagnosticsCard.setConfig({ entity: "sensor.diagnostics_second", diagnostics: true });
assert.equal(window._listenerCounts.get("error"), 1);
await import(new URL("../custom_components/kc761/frontend/kc761-spectrum-card.js?diagnostics-repeat=1", import.meta.url));
assert.equal(window._listenerCounts.get("error"), 1);
diagnosticsCard.disconnectedCallback();
secondDiagnosticsCard.disconnectedCallback();
assert.equal(window._listenerCounts.get("error"), 1, "diagnostics listeners survive card replacement while session flag is enabled");
assert.equal(activeObserver.disconnectCalls, 0, "error-card observer survives card replacement");
assert.ok(diagnosticsRegistry.globalButton, "body export button survives card replacement");
secondDiagnosticsCard.setConfig({ entity: "sensor.diagnostics_second", diagnostics: false });
assert.equal(window._listenerCounts.get("error"), 0, "explicit diagnostics false stops global listeners");
assert.equal(activeObserver.disconnectCalls, 1, "explicit diagnostics false stops error-card observer");
assert.equal(diagnosticsRegistry.globalButton, null, "explicit diagnostics false removes body export button");
assert.ok(globalThis.MutationObserver.instances.some((observer) => observer.disconnectCalls === 1), "nested observers are cleaned");

const originalSessionDescriptor = Object.getOwnPropertyDescriptor(globalThis.window, "sessionStorage");
Object.defineProperty(globalThis.window, "sessionStorage", { configurable: true, get() { throw new Error("session storage unavailable"); } });
assert.doesNotThrow(() => {
  const blockedDiagnosticsCard = new Card();
  blockedDiagnosticsCard.setConfig({ entity: "sensor.blocked_diagnostics", diagnostics: true });
  blockedDiagnosticsCard._exportDiagnostics();
  blockedDiagnosticsCard.disconnectedCallback();
});
Object.defineProperty(globalThis.window, "sessionStorage", originalSessionDescriptor);
assert.equal(JSON.parse(storage.get(diagnosticsKey)).enabled, false, "disabled diagnostics must stop recording globally");

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
assert.equal(card.shadowRoot.querySelector("#roi-start-value").textContent, "Start CH 321");

// HA can assign hass before setConfig, and a dashboard refresh can briefly
// expose a null/incomplete state object.  These transitions must show a
// stable status instead of throwing through Lovelace's card-config wrapper.
const hassBeforeConfig = new Card();
assert.doesNotThrow(() => {
  hassBeforeConfig.hass = { states: {} };
  hassBeforeConfig.setConfig({ entity: "sensor.ordering_spectrum" });
});
const incompleteHassCard = new Card();
incompleteHassCard.setConfig({ entity: "sensor.incomplete_spectrum" });
assert.doesNotThrow(() => { incompleteHassCard.hass = null; });
assert.equal(incompleteHassCard.shadowRoot.querySelector("#status").textContent, "Waiting for Home Assistant state");
assert.doesNotThrow(() => { incompleteHassCard.hass = {}; });
assert.equal(incompleteHassCard.shadowRoot.querySelector("#status").textContent, "Waiting for Home Assistant state");
assert.doesNotThrow(() => { incompleteHassCard.hass = { states: null }; });
assert.equal(incompleteHassCard.shadowRoot.querySelector("#status").textContent, "Waiting for Home Assistant state");
assert.doesNotThrow(() => { incompleteHassCard.hass = { states: {} }; });
assert.equal(incompleteHassCard.shadowRoot.querySelector("#status").textContent, "Entity not found: sensor.incomplete_spectrum");
const recoveredState = { attributes: { config_entry_id: "entry-recovered", codec_profile: "manual_v1_8_candidate", points: [] } };
assert.doesNotThrow(() => { incompleteHassCard.hass = { states: { "sensor.incomplete_spectrum": recoveredState } }; });
assert.ok(incompleteHassCard._data, "a later complete state recovers the card after an incomplete hass update");
const originalStorageDescriptor = Object.getOwnPropertyDescriptor(globalThis.window, "localStorage");
Object.defineProperty(globalThis.window, "localStorage", { configurable: true, get() { throw new Error("storage unavailable"); } });
assert.doesNotThrow(() => {
  const storageRaceCard = new Card();
  storageRaceCard.setConfig({ entity: "sensor.storage_race" });
  storageRaceCard.hass = { states: {} };
});
Object.defineProperty(globalThis.window, "localStorage", originalStorageDescriptor);
const multiInstanceCards = [new Card(), new Card()];
assert.doesNotThrow(() => {
  multiInstanceCards.forEach((instance, index) => {
    instance.setConfig({ entity: `sensor.multi_${index}` });
    instance.hass = { states: {} };
    instance.connectedCallback();
  });
});

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
assert.equal(chartOption.dataZoom[0].zoomOnMouseWheel, "ctrl");
assert.equal(chartOption.dataZoom[0].moveOnMouseMove, true);
assert.equal(chartOption.dataZoom[0].moveOnMouseWheel, false);
assert.equal(chartOption.dataZoom[1].type, "slider");
assert.deepEqual(chartOption.dataZoom[1].xAxisIndex, [0]);
assert.equal(chartOption.dataZoom[1].filterMode, "none");
assert.equal(chartOption.dataZoom[1].height, 14);

// Spectrum transforms are normalized once per data/points identity.  The
// cached prefix makes a sorted-channel ROI total independent of the 2048-point
// length, while malformed and unsorted input retains the safe fallback path.
const cacheCard = new Card();
cacheCard.setConfig({ entity: "sensor.cache_spectrum" });
const cachePoints = [[0, 1, 2], [1, 2, 3], ["bad", 3, 4], [3, 4, 5]];
cacheCard._setData({ total_counts: 10, points: cachePoints });
const firstPointCache = cacheCard._ensurePointCache();
assert.equal(cacheCard._ensurePointCache(), firstPointCache, "unchanged spectrum reuses its point cache");
assert.deepEqual(firstPointCache.points, [[0, 1, 2], [1, 2, 3], [3, 4, 5]]);
assert.equal(cacheCard._roiTotal(0, 1), 5, "prefix ROI total includes both inclusive endpoints");
assert.equal(cacheCard._option().series[0].data.length, 3, "malformed points never reach ECharts");
let fixedSummaryWrites = 0;
const originalSetSummaryValue = cacheCard._setSummaryValue.bind(cacheCard);
cacheCard._setSummaryValue = (selector, value) => {
  if (["#acquired", "#runtime", "#total", "#peak"].includes(selector)) fixedSummaryWrites += 1;
  originalSetSummaryValue(selector, value);
};
cacheCard._updateSummary();
cacheCard._updateSummary();
assert.equal(fixedSummaryWrites, 4, "fixed summary fields are recomputed only for a new cache");
cacheCard._data = { total_counts: 20, points: [[0, 8, 9], [2, 3, 1]] };
const secondPointCache = cacheCard._ensurePointCache();
assert.notEqual(secondPointCache, firstPointCache, "new data invalidates the point cache");
assert.equal(cacheCard._roiTotal(0, 2), 10, "new data invalidates the prefix ROI result");
cacheCard._data = { points: [[3, 4, 5], [0, 1, 2], [2, 3, 1]] };
assert.equal(cacheCard._ensurePointCache().channelsSorted, false);
assert.equal(cacheCard._roiTotal(0, 2), 3, "unsorted channels use the correct bounded fallback");

// Slider/draw updates coalesce into one animation frame and a detached card
// cancels that frame without leaving a late chart update behind.
const drawCard = new Card();
drawCard.setConfig({ entity: "sensor.draw_spectrum" });
drawCard._setData({ points: [[0, 1, 2], [1, 2, 3]] });
let drawCalls = 0;
const drawOptions = [];
drawCard._chart = {
  setOption: (...args) => { drawCalls += 1; drawOptions.push(args); },
  dispose() {},
};
drawCard._ensureChart = async () => drawCard._chart;
drawCard._draw();
drawCard._draw({ updateSummary: true });
assert.notEqual(drawCard._drawRaf, null, "draw schedules a frame");
flushAnimationFrames();
await Promise.resolve();
assert.equal(drawCalls, 1, "multiple slider/draw requests share one frame");
assert.equal(drawOptions[0][1].notMerge, true, "a full draw replaces the complete option");
drawCard._draw({ interactionOnly: true });
drawCard._draw({ interactionOnly: true, updateSummary: true });
flushAnimationFrames();
await Promise.resolve();
assert.equal(drawCalls, 2, "multiple cursor updates share one incremental frame");
assert.equal(drawOptions[1][1].notMerge, false);
assert.equal(drawOptions[1][1].lazyUpdate, true);
assert.equal(drawOptions[1][0].series[0].id, "spectrum");
assert.equal("data" in drawOptions[1][0].series[0], false, "cursor updates do not resend 2048 points");
drawCard._draw();
drawCard.disconnectedCallback();
assert.equal(drawCard._drawRaf, null, "disconnect cancels the pending draw frame");
flushAnimationFrames();
await Promise.resolve();
assert.equal(drawCalls, 2, "a detached card cannot perform a late chart update");

const protectedCard = new Card();
protectedCard.setConfig({ entity: "sensor.protected_spectrum" });
protectedCard._device = { model: "KC761", serial_number: "redacted", destructive_actions_enabled: false };
protectedCard._setDestructiveActionsEnabled(false);
let destructiveCall = false;
protectedCard._call = async () => { destructiveCall = true; };
await protectedCard._clear("spectrum");
assert.equal(destructiveCall, false, "frontend never calls a destructive command when the backend gate is off");
assert.match(protectedCard.shadowRoot.querySelector("#status").textContent, /disabled/i);

const gatedCard = new Card();
gatedCard.setConfig({ entity: "sensor.gated_spectrum" });
const clearButtons = gatedCard.shadowRoot.querySelectorAll("[data-clear]");
assert.ok(clearButtons.every((button) => button.hidden), "destructive buttons start hidden");
gatedCard.hass = {
  states: {
    "sensor.gated_spectrum": {
      state: "1",
      attributes: {
        codec_profile: "manual_v1_8_candidate",
        config_entry_id: "entry-gated",
        destructive_actions_enabled: true,
        points: [],
      },
    },
  },
};
assert.ok(clearButtons.every((button) => !button.hidden), "state attributes reveal enabled destructive buttons");
gatedCard.hass = {
  states: {
    "sensor.gated_spectrum": { state: "unavailable", attributes: {} },
  },
};
assert.ok(clearButtons.every((button) => button.hidden), "unavailable state fails the destructive UI gate closed");

let resolveECharts;
let chartInitCalls = 0;
const concurrentChart = {
  on() {}, off() {}, dispose() {}, resize() {},
};
const chartCard = new Card();
chartCard.setConfig({ entity: "sensor.chart_init_spectrum" });
chartCard._echartsPromise = new Promise((resolve) => { resolveECharts = resolve; });
const firstChartInit = chartCard._ensureChart();
const secondChartInit = chartCard._ensureChart();
resolveECharts({
  init: () => {
    chartInitCalls += 1;
    return concurrentChart;
  },
});
const [firstChart, secondChart] = await Promise.all([firstChartInit, secondChartInit]);
assert.equal(chartInitCalls, 1, "concurrent chart requests share one ECharts initialization");
assert.equal(firstChart, concurrentChart);
assert.equal(secondChart, concurrentChart);

let resolveDetachedECharts;
let detachedChartInitCalls = 0;
const detachedChartCard = new Card();
detachedChartCard.setConfig({ entity: "sensor.detached_chart_spectrum" });
detachedChartCard._echartsPromise = new Promise((resolve) => { resolveDetachedECharts = resolve; });
const detachedChartInit = detachedChartCard._ensureChart();
detachedChartCard.disconnectedCallback();
resolveDetachedECharts({
  init: () => {
    detachedChartInitCalls += 1;
    return concurrentChart;
  },
});
assert.equal(await detachedChartInit, null);
assert.equal(detachedChartInitCalls, 0, "a detached card cannot initialize ECharts after import resolves");

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
const preservedZoomChannels = zoomCard._zoomChannelRange();
zoomCard._setData({ snapshot_id: "latest", source: 0, channel: 0, points: fullPoints.map((point) => [point[0], point[1], point[2] + 1]) });
assert.deepEqual(zoomCard._zoomChannelRange(), preservedZoomChannels, "same live identity preserves zoom channels on refresh");
assert.notDeepEqual(zoomCard._zoom, { start: 0, end: 100 }, "same live identity remains zoomed after refresh");
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

// ROI, Data cursor and zoom are stored as channel coordinates in a
// schema-versioned localStorage record.  Live captures deliberately use one
// stable key even when the device gives each capture a new snapshot id.
const persistentData = {
  snapshot_id: "capture-1",
  source: 0,
  channel: "Gamma",
  points: fullPoints,
};
const persistentCard = new Card();
persistentCard.setConfig({ entity: "sensor.persist_spectrum" });
persistentCard._setData(persistentData, { viewMode: "live" });
const liveViewKey = persistentCard._viewStateKey;
assert.match(liveViewKey, /^kc761-spectrum:view-state:v1:/);
assert.match(liveViewKey, /sensor\.persist_spectrum/);
storageWrites = 0;
persistentCard._setCursorChannel("roiStart", 120);
persistentCard._setCursorChannel("roiEnd", 880);
persistentCard._setCursorChannel("data", 777);
persistentCard._setZoomRange(12, 66);
assert.equal(storageWrites, 0, "view writes are coalesced until the timer flushes");
flushTimers();
assert.equal(storageWrites, 1, "one timer flushes multiple ROI/data/zoom changes");
const persistedView = JSON.parse(storage.get(liveViewKey));
assert.equal(persistedView.schema, 1);
assert.deepEqual(persistedView.roi, { start_channel: 120, end_channel: 880 });
assert.equal(persistedView.data_cursor_channel, 777);
assert.deepEqual(persistedView.zoom, { start_channel: 246, end_channel: 1351 });
assert.equal(persistedView.mode, "live");

const liveRange = persistentCard._zoomChannelRange();
persistentCard._setData({ ...persistentData, snapshot_id: "capture-2", points: fullPoints.map((point) => [point[0], point[1], point[2] + 5]) }, { viewMode: "live" });
assert.deepEqual(persistentCard._zoomChannelRange(), liveRange, "same live key preserves zoom across new snapshot content");
assert.equal(persistentCard._cursor.roiStart, 120);
assert.equal(persistentCard._cursor.data, 777);

// A new card instance is the page-reload case.  It restores the live record
// before drawing, and ignores the live snapshot id when computing its key.
const reloadedCard = new Card();
reloadedCard.setConfig({ entity: "sensor.persist_spectrum" });
reloadedCard._setData({ ...persistentData, snapshot_id: "capture-3" }, { viewMode: "live" });
assert.equal(reloadedCard._viewStateKey, liveViewKey);
assert.equal(reloadedCard._cursor.roiStart, 120);
assert.equal(reloadedCard._cursor.roiEnd, 880);
assert.equal(reloadedCard._cursor.data, 777);
assert.deepEqual(reloadedCard._zoomChannelRange(), liveRange, "page reload restores zoom channel boundaries");

// Source/channel and entity keys are isolated, and switching back restores
// the original live view instead of carrying values across channels.
const isolatedCard = new Card();
isolatedCard.setConfig({ entity: "sensor.persist_spectrum" });
isolatedCard._setData({ ...persistentData, source: 1, channel: "Neutron" }, { viewMode: "live" });
assert.notEqual(isolatedCard._viewStateKey, liveViewKey);
assert.deepEqual(isolatedCard._cursor, { roiStart: 0, roiEnd: 2047, data: 0 });
isolatedCard._setCursorChannel("roiStart", 9);
isolatedCard._setCursorChannel("roiEnd", 19);
isolatedCard._setZoomRange(40, 60);
flushTimers();
isolatedCard._setData({ ...persistentData, source: 0, channel: "Gamma" }, { viewMode: "live" });
assert.equal(isolatedCard._cursor.roiStart, 120, "switching back restores the Gamma ROI");
assert.equal(isolatedCard._cursor.data, 777, "switching back restores the Gamma data cursor");
assert.deepEqual(isolatedCard._zoomChannelRange(), liveRange, "switching back restores the Gamma zoom");

// Saved history has a stable record id and its own state; returning to live
// and then to the same record restores the respective records.
const historyData = { ...persistentData, snapshot_id: "saved-abc" };
isolatedCard._setData(historyData, { viewMode: "history:saved-abc" });
const historyViewKey = isolatedCard._viewStateKey;
assert.notEqual(historyViewKey, liveViewKey);
assert.deepEqual(isolatedCard._zoom, { start: 0, end: 100 });
isolatedCard._setCursorChannel("roiStart", 900);
isolatedCard._setCursorChannel("roiEnd", 1200);
isolatedCard._setCursorChannel("data", 1100);
isolatedCard._setZoomRange(70, 90);
flushTimers();
isolatedCard._setData({ ...persistentData, snapshot_id: "capture-4" }, { viewMode: "live" });
assert.equal(isolatedCard._cursor.roiStart, 120);
isolatedCard._setData(historyData, { viewMode: "history:saved-abc" });
assert.equal(isolatedCard._cursor.roiStart, 900);
assert.equal(isolatedCard._cursor.roiEnd, 1200);
assert.equal(isolatedCard._cursor.data, 1100);
assert.deepEqual(isolatedCard._zoomChannelRange(), [1433, 1842]);

const otherEntityCard = new Card();
otherEntityCard.setConfig({ entity: "sensor.other_persist_spectrum" });
otherEntityCard._setData(persistentData, { viewMode: "live" });
assert.deepEqual(otherEntityCard._cursor, { roiStart: 0, roiEnd: 2047, data: 0 }, "entity keys are isolated");
otherEntityCard.setConfig({ entity: "sensor.persist_spectrum" });
otherEntityCard._setData({ ...persistentData, snapshot_id: "capture-5" }, { viewMode: "live" });
assert.equal(otherEntityCard._cursor.roiStart, 120, "switching entities back restores the original record");

// Reset zoom is persisted immediately, so a fresh instance starts at the
// full range even if a previous zoom record existed.
isolatedCard._setData({ ...persistentData, snapshot_id: "capture-6" }, { viewMode: "live" });
isolatedCard._setZoomRange(20, 40);
isolatedCard._resetZoom();
const resetView = JSON.parse(storage.get(liveViewKey));
assert.deepEqual(resetView.zoom, { start_channel: 0, end_channel: 2047 });
const resetCard = new Card();
resetCard.setConfig({ entity: "sensor.persist_spectrum" });
resetCard._setData({ ...persistentData, snapshot_id: "capture-7" }, { viewMode: "live" });
assert.deepEqual(resetCard._zoom, { start: 0, end: 100 }, "reset zoom survives a new card instance");

// Corrupt/old-schema records and a throwing Storage implementation are safe
// no-ops; no getter or view-state path may turn storage failure into a throw.
storage.set(liveViewKey, "{not-json");
const corruptCard = new Card();
corruptCard.setConfig({ entity: "sensor.persist_spectrum" });
assert.doesNotThrow(() => corruptCard._setData({ ...persistentData, snapshot_id: "capture-8" }, { viewMode: "live" }));
assert.deepEqual(corruptCard._cursor, { roiStart: 0, roiEnd: 2047, data: 0 });
storage.set(liveViewKey, JSON.stringify({ schema: 99, roi: { start_channel: 900, end_channel: 1000 } }));
const oldSchemaCard = new Card();
oldSchemaCard.setConfig({ entity: "sensor.persist_spectrum" });
oldSchemaCard._setData({ ...persistentData, snapshot_id: "capture-9" }, { viewMode: "live" });
assert.deepEqual(oldSchemaCard._cursor, { roiStart: 0, roiEnd: 2047, data: 0 });
const originalLocalStorage = globalThis.window.localStorage;
globalThis.window.localStorage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
const blockedStorageCard = new Card();
blockedStorageCard.setConfig({ entity: "sensor.blocked_storage" });
assert.doesNotThrow(() => {
  blockedStorageCard._setData(persistentData, { viewMode: "live" });
  blockedStorageCard._scheduleViewStatePersist({ flush: true });
});
globalThis.window.localStorage = originalLocalStorage;

// A pending high-frequency datazoom is flushed on disconnect and its timer
// is cleared, so a detached card cannot write later or leak a callback.
const pendingCard = new Card();
pendingCard.setConfig({ entity: "sensor.pending_storage" });
pendingCard._setData(persistentData, { viewMode: "live" });
storageWrites = 0;
pendingCard._setZoomRange(30, 70);
assert.equal(storageWrites, 0);
pendingCard.disconnectedCallback();
assert.equal(storageWrites, 1, "disconnect flushes pending view state");
assert.equal(pendingCard._viewPersistTimer, null);
assert.equal(pendingCard._viewPersistPending, false);

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
unavailableCard._setDestructiveActionsEnabled(true);
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
assert.equal(unavailableCard._destructiveActionsEnabled, false, "unavailable state closes the destructive UI gate");
assert.ok(
  unavailableCard.shadowRoot.querySelectorAll("[data-clear]").every((button) => button.hidden),
  "unavailable state hides destructive buttons",
);
assert.equal(
  Card.isSpectrumEntityState({ state: "unavailable", attributes: {} }),
  false,
  "unavailable state remains outside strict spectrum validation",
);

// The normal dashboard path exposes one compact saved-spectrum selector but
// does not request history until the user opens it.  Focus/pointerdown share a
// single lazy request, and selecting a saved record or Live never mutates the
// Lovelace config.
const runtimeHistoryCard = new Card();
runtimeHistoryCard.setConfig({ entity: "sensor.runtime_history_spectrum" });
runtimeHistoryCard.isConnected = true;
runtimeHistoryCard._entryId = "entry-runtime-history";
runtimeHistoryCard._data = { source: 0, points: [[0, 1, 2]] };
runtimeHistoryCard._draw = () => {};
runtimeHistoryCard._updateSummary = () => {};
const runtimeHistoryCalls = [];
runtimeHistoryCard._hass = {
  states: {
    "sensor.runtime_history_spectrum": {
      attributes: { config_entry_id: "entry-runtime-history", codec_profile: "manual_v1_8_candidate", source: 0, points: [[0, 1, 2]] },
    },
  },
  callWS: async (message) => {
    runtimeHistoryCalls.push(message);
    if (message.type === "kc761/spectrum/list") {
      return [{ snapshot_id: "saved-runtime", channel: 0, acquired_at: "2026-08-24T00:00:00Z", total_counts: 12 }];
    }
    if (message.type === "kc761/spectrum/get") {
      return { source: 0, channel: 0, points: [[0, 4, 12]], acquired_at: "2026-08-24T00:00:00Z", total_counts: 12 };
    }
    throw new Error(`unexpected websocket call ${message.type}`);
  },
};
const runtimeHistorySelect = runtimeHistoryCard.shadowRoot.querySelector("#history");
assert.equal(runtimeHistoryCard.shadowRoot.querySelector("#history-control").hidden, false, "normal view shows saved spectrum control");
assert.equal(runtimeHistoryCard.shadowRoot.querySelector("#editor-controls").hidden, true, "normal view keeps editor controls hidden");
assert.equal(runtimeHistorySelect.listenerCount("focus"), 1, "history opens from keyboard focus");
assert.equal(runtimeHistorySelect.listenerCount("pointerdown"), 1, "history opens from pointer interaction");
assert.deepEqual(runtimeHistoryCalls, [], "normal view starts with no history websocket request");
runtimeHistorySelect.emit("focus");
const runtimeHistoryRequest = runtimeHistoryCard._historyLoadPromise;
runtimeHistorySelect.emit("pointerdown");
assert.equal(runtimeHistoryCard._historyLoadPromise, runtimeHistoryRequest, "focus and pointerdown deduplicate history loading");
await runtimeHistoryRequest;
assert.deepEqual(runtimeHistoryCalls.map((message) => message.type), ["kc761/spectrum/list"]);
const runtimeCardEvents = [];
runtimeHistoryCard.dispatchEvent = (event) => runtimeCardEvents.push(event);
runtimeHistorySelect.emit("change", { target: { value: "saved-runtime" } });
await Promise.resolve();
await Promise.resolve();
assert.equal(runtimeHistoryCard._data.snapshot_id, "saved-runtime", "runtime selector loads a saved spectrum");
assert.equal(runtimeHistoryCalls.filter((message) => message.type === "kc761/spectrum/get").length, 1);
runtimeHistorySelect.emit("change", { target: { value: "latest" } });
await Promise.resolve();
await Promise.resolve();
assert.equal(runtimeHistoryCard._data.snapshot_id, undefined, "Live returns to the current entity spectrum");
assert.deepEqual(runtimeCardEvents, [], "history selection does not dispatch config mutation events");
const historyFailureCard = new Card();
historyFailureCard.setConfig({ entity: "sensor.history_failure_spectrum" });
historyFailureCard.isConnected = true;
historyFailureCard._entryId = "entry-history-failure";
historyFailureCard._hass = { callWS: async () => { throw new Error("history unavailable"); } };
const historyFailureSelect = historyFailureCard.shadowRoot.querySelector("#history");
historyFailureSelect.emit("focus");
const historyFailureRequest = historyFailureCard._historyLoadPromise;
await historyFailureRequest.catch(() => {});
await Promise.resolve();
assert.equal(historyFailureCard.shadowRoot.querySelector("#status").textContent, "history unavailable", "history errors remain visible without breaking the card");
assert.equal(historyFailureCard.shadowRoot.querySelector("#status").classList.contains("error"), true);
const runtimeGeneration = runtimeHistoryCard._historyRequestToken;
runtimeHistoryCard._historyLoadPromise = new Promise(() => {});
runtimeHistoryCard.disconnectedCallback();
assert.equal(runtimeHistoryCard._historyLoadPromise, null, "disconnect clears the pending history request handle");
assert.ok(runtimeHistoryCard._historyRequestToken > runtimeGeneration, "disconnect invalidates late history results");

// The editor preview still requests history through the same single control;
// the existing test also covers concurrent editor transitions sharing one
// websocket request.
const historyCard = new Card();
historyCard.setConfig({ entity: "sensor.history_spectrum" });
historyCard.isConnected = true;
historyCard._entryId = "entry-history";
historyCard._data = { points: [[0, 1, 2]] };
const historyCalls = [];
historyCard._hass = {
  callWS: async (message) => {
    historyCalls.push(message.type);
    if (message.type === "kc761/info") return { sensor_types: [1, 0, 0] };
    if (message.type === "kc761/spectrum/list") return [];
    throw new Error(`unexpected websocket call ${message.type}`);
  },
};
await historyCard._loadInitial();
assert.deepEqual(historyCalls, ["kc761/info"], "normal view does not eagerly load saved history");
historyCard.preview = true;
await historyCard._historyLoadPromise;
assert.deepEqual(historyCalls, ["kc761/info", "kc761/spectrum/list"], "editor preview loads history on demand");
historyCard._historyLoaded = false;
let resolveConcurrentHistory;
historyCard._hass.callWS = async (message) => {
  historyCalls.push(message.type);
  if (message.type === "kc761/spectrum/list") return new Promise((resolve) => { resolveConcurrentHistory = resolve; });
  return { sensor_types: [1, 0, 0] };
};
const historyRequest = historyCard._requestHistoryLoad();
assert.equal(historyCard._requestHistoryLoad(), historyRequest, "concurrent history loads are deduplicated");
resolveConcurrentHistory([]);
await historyRequest;
assert.equal(historyCalls.filter((type) => type === "kc761/spectrum/list").length, 2);

// Hidden tabs stop the interval.  Returning visible performs at most one
// catch-up capture and removes the document listener on disconnect.
const visibilityCard = new Card();
visibilityCard.setConfig({ entity: "sensor.visibility_spectrum", refresh_interval: 5 });
visibilityCard.isConnected = true;
visibilityCard._hass = {};
visibilityCard._entryId = "entry-visibility";
visibilityCard._device = { model: "KC761" };
visibilityCard._data = { points: [[0, 1, 2]] };
let catchupCaptures = 0;
visibilityCard._capture = async () => { catchupCaptures += 1; };
visibilityCard._installVisibilityListener();
visibilityCard._syncAutoRefresh();
assert.notEqual(visibilityCard._refreshTimer, null);
document.visibilityState = "hidden";
document._listeners.get("visibilitychange")();
assert.equal(visibilityCard._refreshTimer, null, "hidden tabs stop automatic refresh");
assert.equal(visibilityCard._refreshAfterVisible, true);
document.visibilityState = "visible";
document._listeners.get("visibilitychange")();
await visibilityCard._visibilityRefreshPromise;
assert.equal(catchupCaptures, 1, "visible transition performs one catch-up refresh");
document._listeners.get("visibilitychange")();
assert.equal(catchupCaptures, 1, "repeated visible events do not duplicate catch-up work");
visibilityCard.disconnectedCallback();
assert.equal(document._listeners.has("visibilitychange"), false, "disconnect removes visibility listener");

// Theme variables are read once per animation frame, but the computed CSS
// values—not only the HA theme name—remain authoritative.  Same-frame hass
// updates coalesce and a disconnected card cancels the pending refresh.
const originalGetComputedStyle = globalThis.getComputedStyle;
let computedStyleCalls = 0;
const computedThemeValues = {
  "--graph-color-1": "#111111",
  "--graph-color-2": "#222222",
  "--graph-color-3": "#333333",
  "--graph-color-4": "#444444",
  "--graph-color-5": "#555555",
  "--primary-text-color": "#eeeeee",
  "--secondary-text-color": "#aaaaaa",
  "--divider-color": "#555555",
  "--card-background-color": "#1c1c1c",
};
globalThis.getComputedStyle = () => {
  computedStyleCalls += 1;
  return { getPropertyValue: (name) => computedThemeValues[name] || "" };
};
const themeCard = new Card();
themeCard.setConfig({ entity: "sensor.theme_spectrum" });
const firstThemeRead = computedStyleCalls;
themeCard._refreshColors();
assert.equal(computedStyleCalls, firstThemeRead + 1, "direct color refresh reads current CSS variables");
const firstCurve = themeCard._colors.curve;
computedThemeValues["--graph-color-1"] = "#abcdef";
themeCard._refreshColors();
assert.ok(computedStyleCalls > firstThemeRead + 1, "same-name theme variable changes are observed");
assert.notEqual(themeCard._colors.curve, firstCurve, "same-name theme variable updates the palette");
assert.equal(themeCard.style.getPropertyValue("--kc761-spectrum-curve"), "#abcdef");
themeCard.isConnected = true;
themeCard.connectedCallback();
assert.notEqual(themeCard._themeRefreshRaf, null, "connected cards schedule an initial color refresh");
flushAnimationFrames();
const beforeHassBurst = computedStyleCalls;
themeCard.hass = { themes: { theme: "same", darkMode: false }, states: {} };
themeCard.hass = { themes: { theme: "same", darkMode: false }, states: {} };
assert.notEqual(themeCard._themeRefreshRaf, null, "hass updates schedule a color refresh");
flushAnimationFrames();
assert.equal(computedStyleCalls, beforeHassBurst + 1, "same-frame hass updates share one color refresh");
themeCard.hass = { themes: { theme: "same", darkMode: false }, states: {} };
const pendingThemeRefresh = themeCard._themeRefreshRaf;
assert.notEqual(pendingThemeRefresh, null);
themeCard.disconnectedCallback();
assert.equal(themeCard._themeRefreshRaf, null, "disconnect cancels pending theme refresh");
flushAnimationFrames();
assert.equal(computedStyleCalls, beforeHassBurst + 1, "cancelled theme refresh leaves no late callback");
globalThis.getComputedStyle = originalGetComputedStyle;

const lifecycle = new Card();
lifecycle.setConfig({ entity: "sensor.lifecycle" });
assert.ok(lifecycle._layoutResizeObserver);
const layoutObserver = lifecycle._layoutResizeObserver;
let compactUpdates = 0;
lifecycle._updateCompactHeight = () => { compactUpdates += 1; };
layoutObserver.trigger();
layoutObserver.trigger();
assert.notEqual(lifecycle._layoutResizeRaf, null, "layout observer schedules an animation frame");
flushAnimationFrames();
assert.equal(compactUpdates, 1, "layout resize notifications are coalesced");
lifecycle._scheduleLayoutResize();
assert.notEqual(lifecycle._layoutResizeRaf, null);
lifecycle.disconnectedCallback();
assert.equal(layoutObserver.disconnectCalls, 1);
assert.equal(lifecycle._layoutResizeObserver, null);
assert.equal(lifecycle._layoutResizeRaf, null, "disconnect cancels pending layout frame");

let chartResizeCalls = 0;
const resizeCard = Object.create(Card.prototype);
resizeCard._detached = false;
resizeCard._chart = { resize: () => { chartResizeCalls += 1; }, dispose() {} };
resizeCard._chartResizeRaf = null;
resizeCard._scheduleChartResize();
resizeCard._scheduleChartResize();
assert.notEqual(resizeCard._chartResizeRaf, null, "chart observer schedules an animation frame");
flushAnimationFrames();
assert.equal(chartResizeCalls, 1, "chart resize notifications are coalesced");
resizeCard._scheduleChartResize();
resizeCard._disposeChart();
flushAnimationFrames();
assert.equal(chartResizeCalls, 1, "disposed chart cannot resize from a late frame");

// Reproduce the persisted Sections card config and the repeated construction
// sequence used by a dashboard refresh.  This is the browser-side path that
// HA wraps as a generic "配置错误" when setConfig/constructor throws.
const dashboardConfig = {
  entity: "sensor.kc761_2158_spectrum",
  grid_options: { columns: "full", rows: "auto" },
  refresh_interval: 60,
  title: "KC761 Spectrum",
  type: "custom:kc761-spectrum-card",
  x_axis: "energy",
  y_axis: "log",
};
const dashboardState = {
  state: "0.13",
  attributes: {
    config_entry_id: "entry-dashboard",
    codec_profile: "manual_v1_8_candidate",
    points: Array.from({ length: 2048 }, (_, index) => [index, index * 2.5, index % 17]),
  },
};
const missingConfigCard = new Card();
assert.doesNotThrow(() => {
  missingConfigCard.setConfig(undefined);
  missingConfigCard.setConfig(null);
  missingConfigCard.setConfig(dashboardConfig);
}, "transient missing config must not become a Lovelace configuration error");
assert.throws(
  () => missingConfigCard.setConfig("not-an-object"),
  /configuration must be an object/,
  "malformed non-object config remains an explicit validation error",
);
for (let attempt = 0; attempt < 12; attempt += 1) {
  const dashboardCard = new Card();
  assert.doesNotThrow(() => {
    dashboardCard.setConfig(dashboardConfig);
    dashboardCard.connectedCallback();
    dashboardCard.hass = { states: { "sensor.kc761_2158_spectrum": dashboardState } };
    dashboardCard.disconnectedCallback();
    dashboardCard.connectedCallback();
    dashboardCard.setConfig(dashboardConfig);
  }, "dashboard lifecycle attempt " + attempt);
}

console.log("frontend card contract: ok");
