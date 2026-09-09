const CARD_VERSION = "0.2.0";
// The loader fingerprints this file; never embed its own content hash here.
const CARD_BUILD = resourceBuildFingerprint(new URL(import.meta.url).searchParams.get("build"));
const ECHARTS_URL = `/kc761/echarts.esm.min.js?v=${CARD_VERSION}`;
const SUPPORTED_CHANNEL_COUNTS = Object.freeze([1024, 2048, 4096]);
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
  diagnostics: false,
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
const ZOOM_DEFAULT = Object.freeze({ start: 0, end: 100 });
const VIEW_STATE_SCHEMA_VERSION = 1;
const VIEW_STATE_STORAGE_PREFIX = `kc761-spectrum:view-state:v${VIEW_STATE_SCHEMA_VERSION}:`;
const VIEW_STATE_PERSIST_DELAY_MS = 120;
const DIAGNOSTICS_SCHEMA_VERSION = 7;
const DIAGNOSTICS_STORAGE_KEY = "kc761-spectrum:diagnostics:v7";
const DIAGNOSTICS_PAGE_SESSION_KEY = "__kc761SpectrumDiagnosticsPageSession";
const DIAGNOSTICS_PAGE_COUNTER_KEY = "kc761-spectrum:diagnostics-page-counter:v1";
const DIAGNOSTICS_MAX_EVENTS = 100;
const DIAGNOSTICS_MAX_FAILURES = 20;
const DIAGNOSTICS_MAX_CHARS = 60000;
const DIAGNOSTICS_MAX_OBSERVER_ROOTS = 32;
const DIAGNOSTICS_MAX_SCAN_NODES = 2000;
const DIAGNOSTICS_MAX_DEEP_ROOTS = 256;
const DIAGNOSTICS_MAX_DEEP_NODES = 10000;
const DIAGNOSTICS_DEEP_BUDGET_MS = 100;
const DIAGNOSTICS_REGISTRY_KEY = "__kc761SpectrumDiagnosticsRegistry";
const DIAGNOSTICS_MODULE_EVALUATED_KEY = "__kc761SpectrumDiagnosticsModuleEvaluated";
const REBUILD_REGISTRY_KEY = "__kc761SpectrumRebuildRegistry";
// Keep the recovery event-driven for the lifetime of the page.  The module is
// loaded as a Home Assistant extra JS resource before the first Lovelace view
// is necessarily created, so a short startup-only timer can expire before the
// dashboard inserts its hui-card.  Root, node, event, and dispatch budgets
// still bound the work and pagehide tears down every observer.
const REBUILD_MAX_ROOTS = 160;
const REBUILD_MAX_NODES = 8000;
const REBUILD_MAX_EVENTS = 128;
const REBUILD_MAX_DISPATCHES = 8;
const REBUILD_MAX_TARGET_QUERIES = 256;
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

function spectrumViewStateStorage() {
  try {
    return globalThis.window?.localStorage || null;
  } catch (_error) {
    return null;
  }
}

function diagnosticsSessionStorage() {
  try {
    return globalThis.window?.sessionStorage || null;
  } catch (_error) {
    return null;
  }
}

// This is a short-lived numeric page marker, not a user/device fingerprint.
// It is allocated only after diagnostics are enabled and is shared by all
// cache-busted module evaluations in the same Window.  A new page reload gets
// the next number, allowing sessionStorage events from different pages to be
// separated without persisting an identifying value.
function diagnosticsPageSession() {
  try {
    const hostWindow = globalThis.window;
    if (Number.isSafeInteger(hostWindow?.[DIAGNOSTICS_PAGE_SESSION_KEY])) {
      return hostWindow[DIAGNOSTICS_PAGE_SESSION_KEY];
    }
    const storage = diagnosticsSessionStorage();
    if (!storage) return 0;
    const previous = Number.parseInt(storage.getItem(DIAGNOSTICS_PAGE_COUNTER_KEY) || "0", 10);
    const next = Number.isSafeInteger(previous) && previous >= 0 ? previous + 1 : 1;
    storage.setItem(DIAGNOSTICS_PAGE_COUNTER_KEY, String(next));
    if (hostWindow) hostWindow[DIAGNOSTICS_PAGE_SESSION_KEY] = next;
    return next;
  } catch (_error) {
    return 0;
  }
}

function sanitizeDiagnosticText(value) {
  return String(value ?? "")
    .replace(/https?:\/\/[^\s)]+/gi, "[redacted-url]")
    .replace(/\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b/g, "[redacted-host]")
    .replace(/\bsensor\.[a-z0-9_]+\b/gi, "[redacted-entity]")
    .replace(/\b(?:token|password|secret|device[_ -]?id|entry[_ -]?id)\s*[=:]\s*[^\s,;]+/gi, "redacted=[redacted]")
    .replace(/(["']?host["']?\s*[=:]\s*)(?!\[redacted[^\]]*\])[^\s,;}\]]+/gi, "$1[redacted]")
    .replace(/(["']?points?["']?\s*[=:]\s*)(?!\[redacted[^\]]*\])[^\s;}]+/gi, "$1[redacted]")
    .replace(/[?#][^\s)]*/g, "[redacted-query]")
    .slice(0, 240);
}

function sanitizeDiagnosticStack(stack) {
  return String(stack ?? "")
    .split("\n")
    .slice(0, 6)
    .map((line) => sanitizeDiagnosticText(line).trim())
    .filter(Boolean)
    .join("\n")
    .slice(0, 1000);
}

function diagnosticsEmptySnapshot(enabled = false) {
  return {
    schema: DIAGNOSTICS_SCHEMA_VERSION,
    version: CARD_VERSION,
    redacted: true,
    enabled: Boolean(enabled),
    sequence: 0,
    events: [],
    failures: [],
    dropped_events: 0,
    dropped_failures: 0,
    coalesced_events: 0,
    observer_stats: { roots: 0, scans: 0, dropped_roots: 0, scan_truncated: 0 },
    export_snapshot: { runs: 0, roots: 0, nodes: 0, dropped_roots: 0, dropped_nodes: 0, budget_exceeded: 0, error_cards: 0, last_timestamp: "" },
  };
}

const DIAGNOSTICS_BOOLEAN_KEYS = Object.freeze([
  "has_config", "has_hass", "has_states", "state_object", "state_present", "valid_spectrum",
  "diagnostics_enabled", "already_defined", "defined", "has_entity", "has_refresh_interval",
  "has_y_axis", "has_type", "enabled", "observed_error_card", "deep_snapshot",
  "custom_element_defined", "module_evaluated", "dispatched", "dispatch_succeeded", "truncated",
  "target_registry_defined", "targeted_query_hit", "candidate_seen_but_blocked",
]);
const DIAGNOSTICS_NUMBER_KEYS = Object.freeze([
  "generation", "count", "repeat", "last_sequence", "page_session", "card_instances",
  "scan_nodes", "scan_roots", "event_count", "dispatched_count", "target_query_count",
]);
const DIAGNOSTICS_TRI_STATE_KEYS = Object.freeze([
  "config_type_is_kc761", "resource_version_match",
]);
const DIAGNOSTICS_TEXT_KEYS = Object.freeze(["node_path", "found_at"]);
const DIAGNOSTICS_ENUMS = Object.freeze({
  dispatch_outcome: new Set(["dispatched", "skipped", "error"]),
  cleanup_reason: new Set(["dispatched", "timeout", "budget", "unload", "replaced", "unsupported"]),
  budget_reason: new Set(["roots", "nodes", "events", "dispatches", "target_queries", "time"]),
  candidate_block_reason: new Set(["detached", "non_kc761", "unknown", "registry_undefined", "unsupported"]),
});

function diagnosticsNormalizeEvent(event, index) {
  if (!event || typeof event !== "object") return null;
  const safe = {
    timestamp: sanitizeDiagnosticText(event.timestamp).slice(0, 40),
    sequence: Number.isSafeInteger(event.sequence) ? event.sequence : index + 1,
    version: CARD_VERSION,
    event: sanitizeDiagnosticText(event.event).slice(0, 80),
  };
  DIAGNOSTICS_BOOLEAN_KEYS.forEach((key) => {
    if (typeof event[key] === "boolean") safe[key] = event[key];
  });
  DIAGNOSTICS_NUMBER_KEYS.forEach((key) => {
    if (Number.isFinite(event[key])) safe[key] = Number(event[key]);
  });
  DIAGNOSTICS_TRI_STATE_KEYS.forEach((key) => {
    if (event[key] === true || event[key] === false || event[key] === "unknown") safe[key] = event[key];
  });
  if (event.config_type_is_kc761 === true || event.config_type_is_kc761 === false || event.config_type_is_kc761 === "unknown") {
    safe.config_type_is_kc761 = event.config_type_is_kc761;
  }
  if (event.resource_version_match === true || event.resource_version_match === false || event.resource_version_match === "unknown") {
    safe.resource_version_match = event.resource_version_match;
  }
  DIAGNOSTICS_TEXT_KEYS.forEach((key) => {
    if (typeof event[key] === "string") safe[key] = sanitizeDiagnosticText(event[key]).slice(0, 180);
  });
  Object.entries(DIAGNOSTICS_ENUMS).forEach(([key, values]) => {
    if (values.has(event[key])) safe[key] = event[key];
  });
  if (event.first_timestamp) safe.first_timestamp = sanitizeDiagnosticText(event.first_timestamp).slice(0, 40);
  if (event.last_timestamp) safe.last_timestamp = sanitizeDiagnosticText(event.last_timestamp).slice(0, 40);
  return safe;
}

function diagnosticsNormalizeFailure(failure, index) {
  const safe = diagnosticsNormalizeEvent(failure, index);
  if (!safe) return null;
  if (failure.error_name) safe.error_name = sanitizeDiagnosticText(failure.error_name).slice(0, 100);
  if (failure.error_message) safe.error_message = sanitizeDiagnosticText(failure.error_message);
  if (failure.stack) safe.stack = sanitizeDiagnosticStack(failure.stack);
  return safe;
}

function diagnosticsReadSnapshot(storage = diagnosticsSessionStorage()) {
  if (!storage) return diagnosticsEmptySnapshot(false);
  try {
    const raw = storage.getItem(DIAGNOSTICS_STORAGE_KEY);
    if (!raw) return diagnosticsEmptySnapshot(false);
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.schema !== DIAGNOSTICS_SCHEMA_VERSION || !Array.isArray(parsed.events) || !Array.isArray(parsed.failures)) {
      return diagnosticsEmptySnapshot(false);
    }
    const events = parsed.events.slice(-DIAGNOSTICS_MAX_EVENTS).map(diagnosticsNormalizeEvent).filter(Boolean);
    const failures = parsed.failures.slice(-DIAGNOSTICS_MAX_FAILURES).map(diagnosticsNormalizeFailure).filter(Boolean);
    return {
      schema: DIAGNOSTICS_SCHEMA_VERSION,
      version: CARD_VERSION,
      redacted: true,
      enabled: parsed.enabled === true,
      sequence: Number.isSafeInteger(parsed.sequence) ? parsed.sequence : events.length + failures.length,
      events,
      failures,
      dropped_events: Number.isFinite(parsed.dropped_events) ? Math.max(0, Number(parsed.dropped_events)) : 0,
      dropped_failures: Number.isFinite(parsed.dropped_failures) ? Math.max(0, Number(parsed.dropped_failures)) : 0,
      coalesced_events: Number.isFinite(parsed.coalesced_events) ? Math.max(0, Number(parsed.coalesced_events)) : 0,
      observer_stats: {
        roots: Number.isFinite(parsed.observer_stats?.roots) ? Math.max(0, Number(parsed.observer_stats.roots)) : 0,
        scans: Number.isFinite(parsed.observer_stats?.scans) ? Math.max(0, Number(parsed.observer_stats.scans)) : 0,
        dropped_roots: Number.isFinite(parsed.observer_stats?.dropped_roots) ? Math.max(0, Number(parsed.observer_stats.dropped_roots)) : 0,
        scan_truncated: Number.isFinite(parsed.observer_stats?.scan_truncated) ? Math.max(0, Number(parsed.observer_stats.scan_truncated)) : 0,
      },
      export_snapshot: {
        runs: Number.isFinite(parsed.export_snapshot?.runs) ? Math.max(0, Number(parsed.export_snapshot.runs)) : 0,
        roots: Number.isFinite(parsed.export_snapshot?.roots) ? Math.max(0, Number(parsed.export_snapshot.roots)) : 0,
        nodes: Number.isFinite(parsed.export_snapshot?.nodes) ? Math.max(0, Number(parsed.export_snapshot.nodes)) : 0,
        dropped_roots: Number.isFinite(parsed.export_snapshot?.dropped_roots) ? Math.max(0, Number(parsed.export_snapshot.dropped_roots)) : 0,
        dropped_nodes: Number.isFinite(parsed.export_snapshot?.dropped_nodes) ? Math.max(0, Number(parsed.export_snapshot.dropped_nodes)) : 0,
        budget_exceeded: Number.isFinite(parsed.export_snapshot?.budget_exceeded) ? Math.max(0, Number(parsed.export_snapshot.budget_exceeded)) : 0,
        error_cards: Number.isFinite(parsed.export_snapshot?.error_cards) ? Math.max(0, Number(parsed.export_snapshot.error_cards)) : 0,
        last_timestamp: sanitizeDiagnosticText(parsed.export_snapshot?.last_timestamp).slice(0, 40),
      },
    };
  } catch (_error) {
    return diagnosticsEmptySnapshot(false);
  }
}

function diagnosticsTrim(snapshot) {
  snapshot.events = Array.isArray(snapshot.events) ? snapshot.events : [];
  snapshot.failures = Array.isArray(snapshot.failures) ? snapshot.failures : [];
  while (snapshot.events.length > DIAGNOSTICS_MAX_EVENTS) {
    snapshot.events.splice(0, 1);
    snapshot.dropped_events += 1;
  }
  while (snapshot.failures.length > DIAGNOSTICS_MAX_FAILURES) {
    snapshot.failures.splice(0, 1);
    snapshot.dropped_failures += 1;
  }
  while (JSON.stringify(snapshot).length > DIAGNOSTICS_MAX_CHARS && snapshot.events.length) {
    snapshot.events.splice(0, 1);
    snapshot.dropped_events += 1;
  }
  while (JSON.stringify(snapshot).length > DIAGNOSTICS_MAX_CHARS && snapshot.failures.length) {
    snapshot.failures.splice(0, 1);
    snapshot.dropped_failures += 1;
  }
  return snapshot;
}

function diagnosticsWriteSnapshot(snapshot, storage = diagnosticsSessionStorage()) {
  if (!storage) return false;
  try {
    storage.setItem(DIAGNOSTICS_STORAGE_KEY, JSON.stringify(diagnosticsTrim(snapshot)));
    return true;
  } catch (_error) {
    return false;
  }
}

function diagnosticsSafeFields(fields = {}) {
  const safe = {};
  DIAGNOSTICS_BOOLEAN_KEYS.forEach((key) => {
    if (typeof fields[key] === "boolean") safe[key] = fields[key];
  });
  DIAGNOSTICS_NUMBER_KEYS.forEach((key) => {
    if (Number.isFinite(fields[key])) safe[key] = Number(fields[key]);
  });
  DIAGNOSTICS_TRI_STATE_KEYS.forEach((key) => {
    if (typeof fields[key] === "boolean" || fields[key] === "string" && fields[key] === "unknown") safe[key] = fields[key];
  });
  DIAGNOSTICS_TEXT_KEYS.forEach((key) => {
    if (typeof fields[key] === "string") safe[key] = sanitizeDiagnosticText(fields[key]).slice(0, 180);
  });
  Object.entries(DIAGNOSTICS_ENUMS).forEach(([key, values]) => {
    if (values.has(fields[key])) safe[key] = fields[key];
  });
  if (fields.config_type_is_kc761 === true || fields.config_type_is_kc761 === false || fields.config_type_is_kc761 === "unknown") {
    safe.config_type_is_kc761 = fields.config_type_is_kc761;
  }
  if (fields.resource_version_match === true || fields.resource_version_match === false || fields.resource_version_match === "unknown") {
    safe.resource_version_match = fields.resource_version_match;
  }
  return safe;
}

function diagnosticsSameReadinessSignature(left, right, event) {
  if (!left || left.event !== event || !["hass-entry", "hass-readiness"].includes(event)) return false;
  if (left.page_session !== right.page_session) return false;
  return DIAGNOSTICS_BOOLEAN_KEYS.every((key) => (left[key] === undefined ? right[key] === undefined : left[key] === right[key]));
}

function diagnosticsRecord(event, fields = {}) {
  const storage = diagnosticsSessionStorage();
  if (!storage) return false;
  const snapshot = diagnosticsReadSnapshot(storage);
  if (snapshot.enabled !== true) return false;
  snapshot.sequence += 1;
  const record = {
    timestamp: new Date().toISOString(),
    sequence: snapshot.sequence,
    version: CARD_VERSION,
    event: sanitizeDiagnosticText(event).slice(0, 80),
    page_session: diagnosticsPageSession(),
    ...diagnosticsSafeFields(fields),
  };
  const previous = snapshot.events[snapshot.events.length - 1];
  if (diagnosticsSameReadinessSignature(previous, record, record.event)) {
    previous.repeat = (Number.isFinite(previous.repeat) ? previous.repeat : 1) + 1;
    previous.last_sequence = record.sequence;
    previous.last_timestamp = record.timestamp;
    snapshot.coalesced_events += 1;
  } else {
    snapshot.events.push(record);
  }
  return diagnosticsWriteSnapshot(snapshot, storage);
}

function diagnosticsFailureSource(stack) {
  const sources = String(stack ?? "").split("\n").map((line) => {
    const match = line.match(/([^/\\\s()]+\.(?:js|mjs|ts|py)):(\d+)/);
    return match ? `${match[1]}:${match[2]}` : "";
  }).filter(Boolean);
  // The first frame identifies the operation; deeper caller frames vary on
  // repeated lifecycle notifications and must not defeat failure coalescing.
  return sources.slice(0, 1).join(";").slice(0, 160);
}

function diagnosticsFailureSignature(record) {
  return [
    record?.page_session,
    record?.event,
    record?.error_name,
    record?.error_message,
    diagnosticsFailureSource(record?.stack),
  ].map((value) => sanitizeDiagnosticText(value)).join("|").slice(0, 700);
}

function diagnosticsRecordFailure(event, error = null, fields = {}) {
  const storage = diagnosticsSessionStorage();
  if (!storage) return false;
  const snapshot = diagnosticsReadSnapshot(storage);
  if (snapshot.enabled !== true) return false;
  snapshot.sequence += 1;
  const record = {
    timestamp: new Date().toISOString(),
    sequence: snapshot.sequence,
    version: CARD_VERSION,
    event: sanitizeDiagnosticText(event).slice(0, 80),
    page_session: diagnosticsPageSession(),
    ...diagnosticsSafeFields(fields),
  };
  if (error) {
    record.error_name = sanitizeDiagnosticText(error.name || "Error").slice(0, 100);
    record.error_message = sanitizeDiagnosticText(error.message || error).slice(0, 240);
    record.stack = sanitizeDiagnosticStack(error.stack);
  }
  record.first_timestamp = record.timestamp;
  record.last_timestamp = record.timestamp;
  record.repeat = 1;
  const signature = diagnosticsFailureSignature(record);
  const existing = snapshot.failures.find((failure) => diagnosticsFailureSignature(failure) === signature);
  if (existing) {
    existing.repeat = (Number.isFinite(existing.repeat) ? existing.repeat : 1) + 1;
    existing.last_sequence = record.sequence;
    existing.last_timestamp = record.timestamp;
  } else {
    snapshot.failures.push(record);
  }
  return diagnosticsWriteSnapshot(snapshot, storage);
}

function diagnosticsRecordError(event, error) {
  return diagnosticsRecordFailure(event, error);
}

function diagnosticsSetEnabled(enabled) {
  const storage = diagnosticsSessionStorage();
  if (!storage) return false;
  const snapshot = diagnosticsReadSnapshot(storage);
  snapshot.enabled = Boolean(enabled);
  if (!diagnosticsWriteSnapshot(snapshot, storage)) return false;
  if (enabled) {
    diagnosticsEnsureGlobal();
    diagnosticsRecord("diagnostics-enabled", { enabled: true });
  } else {
    diagnosticsStopGlobal();
  }
  return true;
}

function diagnosticsExportPayload() {
  const snapshot = diagnosticsReadSnapshot();
  const registry = diagnosticsListenerRegistry();
  return {
    schema: DIAGNOSTICS_SCHEMA_VERSION,
    version: CARD_VERSION,
    redacted: true,
    enabled: snapshot.enabled === true,
    events: snapshot.events,
    failures: snapshot.failures,
    dropped_events: snapshot.dropped_events,
    dropped_failures: snapshot.dropped_failures,
    coalesced_events: snapshot.coalesced_events,
    page_session: diagnosticsPageSession(),
    observer_stats: registry?.observerStats || snapshot.observer_stats,
    export_snapshot: registry?.exportSnapshot || snapshot.export_snapshot,
    diagnostics_context: diagnosticsContext(registry),
  };
}

function resourceBuildFingerprint(value) {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value) ? value : "unknown";
}

function formatCountTick(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  if (number === 0) return "0";
  const magnitude = Math.abs(number);
  if (magnitude >= 0.01 && magnitude < 100000) return String(Number(number.toPrecision(4)));
  // At most nine characters, including a sign and a three-digit exponent.
  const compact = (digits) => number.toExponential(digits).replace(/\.?0+e/, "e").replace("e+", "e");
  const label = compact(2);
  return label.length <= 9 ? label : compact(1);
}

function diagnosticsRegisteredBuild() {
  try {
    return resourceBuildFingerprint(globalThis.customElements?.get?.("kc761-spectrum-card")?.resourceBuild);
  } catch (_error) {
    return "unknown";
  }
}

function diagnosticsContext(registry = diagnosticsListenerRegistry()) {
  let customElementDefined = false;
  try {
    customElementDefined = Boolean(globalThis.customElements?.get?.("kc761-spectrum-card"));
  } catch (_error) {
    customElementDefined = false;
  }
  let moduleEvaluated = Boolean(registry?.moduleEvaluated);
  try {
    moduleEvaluated = moduleEvaluated || globalThis.window?.[DIAGNOSTICS_MODULE_EVALUATED_KEY] === true;
  } catch (_error) {
    // A restricted window must not break export.
  }
  return {
    custom_element_defined: customElementDefined,
    module_evaluated: moduleEvaluated,
    card_instances: Number(registry?.cards?.size) || 0,
    resource_version_match: diagnosticsResourceVersionMatch(),
    resource_version_match_basis: "version_only_not_build",
    resource_build: CARD_BUILD,
    registered_card_build: diagnosticsRegisteredBuild(),
    resource_build_basis: "module_url_not_content_verification",
    page_session: diagnosticsPageSession(),
    diagnostics_schema: DIAGNOSTICS_SCHEMA_VERSION,
  };
}

function diagnosticsListenerRegistry() {
  try {
    const hostWindow = globalThis.window;
    if (!hostWindow) return null;
    if (!hostWindow[DIAGNOSTICS_REGISTRY_KEY] || typeof hostWindow[DIAGNOSTICS_REGISTRY_KEY] !== "object") {
      hostWindow[DIAGNOSTICS_REGISTRY_KEY] = {
        cards: new Set(), installed: false, observer: null, observers: new Map(), observedRoots: new Set(),
        errorCards: new WeakSet(), observerStats: { roots: 0, scans: 0, dropped_roots: 0, scan_truncated: 0 },
        exportSnapshot: { runs: 0, roots: 0, nodes: 0, dropped_roots: 0, dropped_nodes: 0, budget_exceeded: 0, error_cards: 0, last_timestamp: "" },
        globalButton: null, globalButtonHandler: null, bodyReadyListener: null, moduleEvaluated: false,
        onError: null, onRejection: null,
      };
    }
    const registry = hostWindow[DIAGNOSTICS_REGISTRY_KEY];
    if (!(registry.cards instanceof Set)) registry.cards = new Set();
    if (!(registry.errorCards instanceof WeakSet)) registry.errorCards = new WeakSet();
    if (!(registry.observers instanceof Map)) registry.observers = new Map();
    if (!(registry.observedRoots instanceof Set)) registry.observedRoots = new Set();
    if (!registry.observerStats || typeof registry.observerStats !== "object") registry.observerStats = { roots: 0, scans: 0, dropped_roots: 0, scan_truncated: 0 };
    if (!registry.exportSnapshot || typeof registry.exportSnapshot !== "object") registry.exportSnapshot = diagnosticsEmptySnapshot(false).export_snapshot;
    if (!("globalButton" in registry)) registry.globalButton = null;
    if (!("globalButtonHandler" in registry)) registry.globalButtonHandler = null;
    if (!("bodyReadyListener" in registry)) registry.bodyReadyListener = null;
    if (!("moduleEvaluated" in registry)) registry.moduleEvaluated = false;
    if (!("observer" in registry)) registry.observer = null;
    return registry;
  } catch (_error) {
    return null;
  }
}

function diagnosticsSourceText(event) {
  const reason = event?.reason;
  return [event?.filename, event?.message, reason?.message, reason?.stack].filter(Boolean).join(" ");
}

function diagnosticsNodeTag(node) {
  try {
    return String(node?.localName || node?.tagName || "").toLowerCase();
  } catch (_error) {
    return "";
  }
}

function diagnosticsConfigTypeMatch(node) {
  const seen = new Set();
  let typedConfigSeen = false;
  const errorText = [];
  let current = node;
  for (let depth = 0; current && depth < 16 && !seen.has(current); depth += 1) {
    seen.add(current);
    try {
      if (current === node) {
        errorText.push(
          current.config?.message,
          current.error?.message,
          current.errorDetails?.message,
          current.shadowRoot?.textContent,
          current.textContent,
        );
      }
      const candidates = [
        current.config,
        current.config?.origConfig,
        current.config?.originalConfig,
        current._elementConfig,
        current.error?.config,
        current.error?.origConfig,
        current.errorDetails?.config,
        current.errorDetails?.origConfig,
      ];
      for (const config of candidates) {
        if (config && typeof config === "object" && typeof config.type === "string") {
          typedConfigSeen = true;
          if (config.type === "custom:kc761-spectrum-card") return true;
        }
      }
      current = current.parentElement || current.parentNode?.host || current.parentNode || null;
    } catch (_error) {
      return "unknown";
    }
  }
  // HA's custom-card fallback stores only type="error" and the generated
  // message on hui-error-card; the original card config is held by hui-card
  // and is not guaranteed to be reachable while the DOM is being rebuilt.
  // Match the stable custom-element tag, not a localized/full error sentence.
  try {
    if (errorText.some((value) => /(?:^|[^a-z0-9_-])kc761-spectrum-card(?:$|[^a-z0-9_-])/i.test(String(value || "")))) {
      return true;
    }
  } catch (_error) {
    return "unknown";
  }
  return typedConfigSeen ? false : "unknown";
}

function rebuildTargetWindow(node) {
  try {
    return node?.ownerDocument?.defaultView || globalThis.window || globalThis;
  } catch (_error) {
    return globalThis.window || globalThis;
  }
}

function rebuildTargetRegistryDefined(node) {
  try {
    const targetWindow = rebuildTargetWindow(node);
    return Boolean(targetWindow?.customElements?.get?.("kc761-spectrum-card"));
  } catch (_error) {
    return false;
  }
}

function spectrumRebuildRegistry() {
  try {
    const hostWindow = globalThis.window;
    if (!hostWindow) return null;
    const current = hostWindow[REBUILD_REGISTRY_KEY];
    if (!current || typeof current !== "object" || current.version !== CARD_VERSION) {
      if (current && typeof current === "object") rebuildCleanup(current, "replaced");
      hostWindow[REBUILD_REGISTRY_KEY] = {
        version: CARD_VERSION,
        cards: new WeakSet(),
        active: false,
        started: false,
        observedRoots: new Set(),
        observers: [],
        timers: [],
        pagehideListener: null,
        scannedNodes: 0,
        scanRoots: 0,
        eventCount: 0,
        dispatchedCount: 0,
        targetQueryCount: 0,
        targetedQueryHit: false,
        candidateSeenButBlocked: false,
        budgetReason: "",
        truncated: false,
        cleanupReason: "",
      };
    }
    const registry = hostWindow[REBUILD_REGISTRY_KEY];
    if (!(registry.cards instanceof WeakSet)) registry.cards = new WeakSet();
    if (!(registry.observedRoots instanceof Set)) registry.observedRoots = new Set();
    if (!Array.isArray(registry.observers)) registry.observers = [];
    if (!Array.isArray(registry.timers)) registry.timers = [];
    return registry;
  } catch (_error) {
    return null;
  }
}

function rebuildCleanup(registry, reason) {
  if (!registry || (!registry.active && registry.cleanupReason)) return;
  registry.active = false;
  registry.cleanupReason = reason;
  (Array.isArray(registry.timers) ? registry.timers : []).forEach((timer) => {
    try { clearTimeout(timer); } catch (_error) { /* restricted timer */ }
  });
  registry.timers = [];
  (Array.isArray(registry.observers) ? registry.observers : []).forEach((observer) => {
    try { observer.disconnect?.(); } catch (_error) { /* restricted observer */ }
  });
  registry.observers = [];
  const hostWindow = globalThis.window;
  if (registry.pagehideListener && hostWindow?.removeEventListener) {
    try { hostWindow.removeEventListener("pagehide", registry.pagehideListener); } catch (_error) { /* restricted window */ }
  }
  registry.pagehideListener = null;
  diagnosticsRecord("registration-rebuild-cleanup", {
    cleanup_reason: reason,
    budget_reason: registry.budgetReason,
    dispatched_count: registry.dispatchedCount,
    scan_nodes: registry.scannedNodes,
    scan_roots: registry.scanRoots,
    target_query_count: registry.targetQueryCount,
    targeted_query_hit: registry.targetedQueryHit,
    candidate_seen_but_blocked: registry.candidateSeenButBlocked,
    truncated: registry.truncated,
  });
}

function rebuildDispatchErrorCard(registry, node, targeted = false) {
  if (!registry?.active || !node || registry.cards.has(node)) return false;
  if (diagnosticsNodeTag(node) !== "hui-error-card" || diagnosticsConfigTypeMatch(node) !== true) return false;
  if (registry.dispatchedCount >= REBUILD_MAX_DISPATCHES) {
    registry.truncated = true;
    registry.budgetReason = "dispatches";
    rebuildCleanup(registry, "budget");
    return false;
  }
  const foundAt = new Date().toISOString();
  const targetWindow = rebuildTargetWindow(node);
  const targetRegistryDefined = rebuildTargetRegistryDefined(node);
  try {
    const EventConstructor = targetWindow?.Event || globalThis.Event;
    if (typeof EventConstructor !== "function" || typeof node.dispatchEvent !== "function") {
      diagnosticsRecord("registration-rebuild-dispatch", {
        cleanup_reason: "unsupported",
        dispatch_outcome: "skipped",
        found_at: foundAt,
        targeted_query_hit: targeted,
        target_registry_defined: targetRegistryDefined,
      });
      return false;
    }
    const result = node.dispatchEvent(new EventConstructor("ll-rebuild", { bubbles: true, composed: true }));
    registry.cards.add(node);
    registry.dispatchedCount += 1;
    diagnosticsRecord("registration-rebuild-dispatch", {
      dispatched: true,
      dispatch_succeeded: result !== false,
      dispatch_outcome: "dispatched",
      found_at: foundAt,
      targeted_query_hit: targeted,
      target_registry_defined: targetRegistryDefined,
      dispatched_count: registry.dispatchedCount,
    });
    if (registry.dispatchedCount >= REBUILD_MAX_DISPATCHES) rebuildCleanup(registry, "budget");
    return true;
  } catch (_error) {
    diagnosticsRecord("registration-rebuild-dispatch", {
      dispatch_outcome: "error",
      found_at: foundAt,
      targeted_query_hit: targeted,
      target_registry_defined: targetRegistryDefined,
    });
    return false;
  }
}

function rebuildRecordBlockedCandidate(registry, node, reason) {
  registry.candidateSeenButBlocked = true;
  diagnosticsRecord("registration-rebuild-candidate", {
    candidate_seen_but_blocked: true,
    candidate_block_reason: reason,
    target_registry_defined: rebuildTargetRegistryDefined(node),
    target_query_count: registry.targetQueryCount,
    targeted_query_hit: registry.targetedQueryHit,
  });
}

function rebuildQueryErrorCards(registry, root) {
  if (!registry?.active || !root) return false;
  if (registry.targetQueryCount >= REBUILD_MAX_TARGET_QUERIES) {
    registry.truncated = true;
    registry.budgetReason = "target_queries";
    rebuildCleanup(registry, "budget");
    return false;
  }
  registry.targetQueryCount += 1;
  const candidates = [];
  try {
    if (diagnosticsNodeTag(root) === "hui-error-card") candidates.push(root);
    root.querySelectorAll?.("hui-error-card")?.forEach?.((node) => candidates.push(node));
  } catch (_error) {
    return false;
  }
  const seen = new Set();
  for (const node of candidates) {
    if (!registry.active || !node || seen.has(node) || registry.cards.has(node)) continue;
    seen.add(node);
    if (diagnosticsNodeTag(node) !== "hui-error-card") continue;
    const configMatch = diagnosticsConfigTypeMatch(node);
    if (configMatch !== true) {
      rebuildRecordBlockedCandidate(registry, node, configMatch === false ? "non_kc761" : "unknown");
      continue;
    }
    registry.targetedQueryHit = true;
    if (rebuildDispatchErrorCard(registry, node, true)) return true;
  }
  return false;
}

function rebuildObserveRoot(registry, root) {
  if (!registry?.active || !root || registry.observedRoots.has(root)) return false;
  if (registry.observedRoots.size >= REBUILD_MAX_ROOTS) {
    registry.truncated = true;
    registry.budgetReason = "roots";
    rebuildCleanup(registry, "budget");
    return false;
  }
  registry.observedRoots.add(root);
  registry.scanRoots = registry.observedRoots.size;
  const targetWindow = rebuildTargetWindow(root);
  const Observer = targetWindow?.MutationObserver || globalThis.MutationObserver;
  if (typeof Observer !== "function") return true;
  try {
    const observer = new Observer((records) => {
      if (!registry.active) return;
      registry.eventCount += records.length;
      if (registry.eventCount > REBUILD_MAX_EVENTS) {
        registry.truncated = true;
        registry.budgetReason = "events";
        rebuildCleanup(registry, "budget");
        return;
      }
      records.forEach((record) => {
        if (!registry.active) return;
        Array.from(record?.addedNodes || []).forEach((node) => rebuildScanAddedTree(registry, node));
      });
      if (registry.active) {
        diagnosticsRecord("registration-rebuild-scan", {
          dispatched_count: registry.dispatchedCount,
          event_count: registry.eventCount,
          scan_nodes: registry.scannedNodes,
          scan_roots: registry.scanRoots,
          truncated: registry.truncated,
        });
      }
    });
    observer.observe(root, { childList: true, subtree: true });
    registry.observers.push(observer);
  } catch (_error) {
    // A restricted root remains covered by other accessible roots; a later
    // added subtree is also handled by the persistent document observer.
  }
  return true;
}

function rebuildScanNode(registry, node) {
  if (!registry?.active || !node) return false;
  if (registry.scannedNodes >= REBUILD_MAX_NODES) {
    registry.truncated = true;
    registry.budgetReason = "nodes";
    rebuildCleanup(registry, "budget");
    return false;
  }
  registry.scannedNodes += 1;
  try {
    if (node.shadowRoot) {
      rebuildObserveRoot(registry, node.shadowRoot);
      rebuildScanRoot(registry, node.shadowRoot, false);
    }
    return rebuildDispatchErrorCard(registry, node);
  } catch (_error) {
    return false;
  }
}

function rebuildScanRoot(registry, root, recordScan = true) {
  if (!registry?.active || !root) return false;
  // Query the target tag before building the generic node list.  The alpha34
  // failure occurred because the broad walk exhausted the root budget before
  // it reached a late Shadow Root containing hui-error-card.
  if (rebuildQueryErrorCards(registry, root)) return true;
  const nodes = [];
  try {
    if (diagnosticsNodeTag(root) === "hui-error-card") nodes.push(root);
    root.querySelectorAll?.("*")?.forEach?.((node) => nodes.push(node));
  } catch (_error) {
    return false;
  }
  for (const node of nodes) {
    if (!registry.active || rebuildScanNode(registry, node)) break;
  }
  if (recordScan && registry.active) {
    diagnosticsRecord("registration-rebuild-scan", {
      dispatched_count: registry.dispatchedCount,
      scan_nodes: registry.scannedNodes,
      scan_roots: registry.scanRoots,
      truncated: registry.truncated,
    });
  }
  return registry.dispatchedCount > 0;
}

function rebuildScanAddedTree(registry, node) {
  if (!registry?.active || !node) return false;
  let dispatched = false;
  try {
    // A hui-card can be inserted together with its already-created
    // hui-error-card child.  In that case MutationObserver reports only the
    // outer hui-card, so inspect the added subtree before walking its hosts.
    dispatched = rebuildQueryErrorCards(registry, node) || dispatched;
    dispatched = rebuildScanNode(registry, node) || dispatched;
    const descendants = node.querySelectorAll?.("*") || [];
    for (const descendant of descendants) {
      if (!registry.active) break;
      dispatched = rebuildScanNode(registry, descendant) || dispatched;
    }
  } catch (_error) {
    // A restricted DOM implementation is covered by the persistent root
    // observer and the next added subtree.
  }
  return dispatched;
}

function scheduleKC761ErrorCardRebuild() {
  const registry = spectrumRebuildRegistry();
  const documentRoot = globalThis.document || globalThis.window?.document;
  if (!registry || !documentRoot || registry.started) return;
  registry.started = true;
  registry.active = true;
  const hostWindow = globalThis.window;
  registry.pagehideListener = () => rebuildCleanup(registry, "unload");
  try { hostWindow?.addEventListener?.("pagehide", registry.pagehideListener, { once: true }); } catch (_error) { /* restricted window */ }
  rebuildObserveRoot(registry, documentRoot);
  rebuildScanRoot(registry, documentRoot);
}

function diagnosticsNodePath(node) {
  const parts = [];
  const seen = new Set();
  let current = node;
  for (let depth = 0; current && depth < 8 && !seen.has(current); depth += 1) {
    seen.add(current);
    const tag = diagnosticsNodeTag(current) || "node";
    let index = 0;
    try {
      const parent = current.parentElement || current.parentNode?.host || current.parentNode;
      const siblings = parent?.children || parent?.childNodes;
      if (siblings && typeof siblings.length === "number") {
        for (let siblingIndex = 0; siblingIndex < siblings.length; siblingIndex += 1) {
          if (siblings[siblingIndex] === current) {
            index = siblingIndex;
            break;
          }
        }
      }
      current = parent || null;
    } catch (_error) {
      current = null;
    }
    parts.splice(0, 0, `${tag}[${index}]`);
  }
  return parts.join(">").slice(0, 180) || "unknown";
}

function diagnosticsResourceVersionMatch() {
  let resourceSeen = false;
  try {
    const entries = globalThis.performance?.getEntriesByType?.("resource") || [];
    for (const entry of entries) {
      const resourceName = String(entry?.name || "");
      if (!/kc761-spectrum-card\.js(?:\?|$)/i.test(resourceName)) continue;
      resourceSeen = true;
      if (resourceName.includes(`v=${encodeURIComponent(CARD_VERSION)}`) || resourceName.includes(CARD_VERSION)) return true;
    }
  } catch (_error) {
    return "unknown";
  }
  return resourceSeen ? false : "unknown";
}

function diagnosticsIsErrorCard(node) {
  const tag = diagnosticsNodeTag(node);
  if (tag === "hui-error-card") return true;
  if (tag !== "ha-card") return false;
  try {
    return Boolean(node.classList?.contains?.("error")) || /(?:^|\s)error(?:\s|$)/i.test(String(node.className || ""));
  } catch (_error) {
    return false;
  }
}

function diagnosticsNodeText(node) {
  try {
    const rootText = node?.shadowRoot?.textContent || node?.textContent || node?.getAttribute?.("title") || "";
    return sanitizeDiagnosticText(rootText).slice(0, 240);
  } catch (_error) {
    return "";
  }
}

function diagnosticsNodeError(node, text) {
  let exposed = null;
  try {
    exposed = node?.error || node?.errorDetails || null;
  } catch (_error) {
    exposed = null;
  }
  return {
    name: sanitizeDiagnosticText(exposed?.name || "LovelaceErrorCard").slice(0, 100),
    message: text || "Lovelace error card detected",
    stack: sanitizeDiagnosticStack(exposed?.stack),
  };
}

function diagnosticsErrorCardFields(node) {
  const context = diagnosticsContext();
  return {
    config_type_is_kc761: diagnosticsConfigTypeMatch(node) ?? "unknown",
    node_path: diagnosticsNodePath(node),
    custom_element_defined: context.custom_element_defined,
    module_evaluated: context.module_evaluated,
    card_instances: context.card_instances,
    resource_version_match: context.resource_version_match ?? "unknown",
  };
}

function diagnosticsInspectErrorCard(node, registry = diagnosticsListenerRegistry()) {
  if (!registry || !node || !diagnosticsIsErrorCard(node) || registry.errorCards.has(node)) return;
  registry.errorCards.add(node);
  diagnosticsRecordFailure("lovelace-error-card", diagnosticsNodeError(node, diagnosticsNodeText(node)), {
    observed_error_card: true,
    ...diagnosticsErrorCardFields(node),
  });
}

function diagnosticsScanErrorCards(root, registry = diagnosticsListenerRegistry()) {
  if (!root || !registry) return;
  registry.observerStats.scans += 1;
  diagnosticsInspectErrorCard(root, registry);
  try {
    const shadowRoot = root?.shadowRoot;
    if (shadowRoot) diagnosticsObserveRoot(registry, shadowRoot);
  } catch (_error) {
    // Closed or restricted shadow roots are intentionally ignored.
  }
  const nodes = [];
  try {
    root.querySelectorAll?.("hui-error-card, ha-card.error")?.forEach((node) => nodes.push(node));
    root.querySelectorAll?.("*")?.forEach((node) => nodes.push(node));
  } catch (_error) {
    // Restricted DOM implementations may not support selectors.
  }
  const unique = new Set();
  for (const node of nodes) {
    if (unique.size >= DIAGNOSTICS_MAX_SCAN_NODES) {
      registry.observerStats.scan_truncated += 1;
      break;
    }
    if (unique.has(node)) continue;
    unique.add(node);
    diagnosticsInspectErrorCard(node, registry);
    try {
      const shadowRoot = node?.shadowRoot;
      if (shadowRoot) diagnosticsObserveRoot(registry, shadowRoot);
    } catch (_error) {
      // Closed or restricted shadow roots are intentionally ignored.
    }
  }
}

function diagnosticsPersistObserverStats(registry) {
  const storage = diagnosticsSessionStorage();
  if (!storage || !registry?.observerStats) return;
  const snapshot = diagnosticsReadSnapshot(storage);
  snapshot.observer_stats = { ...registry.observerStats };
  diagnosticsWriteSnapshot(snapshot, storage);
}

function diagnosticsDeepNow() {
  try {
    const value = globalThis.performance?.now?.();
    if (Number.isFinite(value)) return value;
  } catch (_error) {
    // Fall back to the wall clock in restricted browser shells.
  }
  return Date.now();
}

function diagnosticsDeepPriority(node) {
  const tag = diagnosticsNodeTag(node);
  return /^(?:hui-|ha-|home-assistant)/i.test(tag) ? 0 : 1;
}

function diagnosticsDeepSnapshot(registry = diagnosticsListenerRegistry()) {
  const started = diagnosticsDeepNow();
  const deadline = started + DIAGNOSTICS_DEEP_BUDGET_MS;
  const roots = [];
  const seenRoots = new Set();
  const seenNodes = new Set();
  const errors = new Map();
  const result = {
    runs: Number(registry?.exportSnapshot?.runs) + 1 || 1,
    roots: 0,
    nodes: 0,
    dropped_roots: 0,
    dropped_nodes: 0,
    budget_exceeded: 0,
    error_cards: 0,
    last_timestamp: new Date().toISOString(),
  };
  const enqueueRoot = (root) => {
    if (!root || seenRoots.has(root)) return;
    if (seenRoots.size + roots.length >= DIAGNOSTICS_MAX_DEEP_ROOTS) {
      result.dropped_roots += 1;
      return;
    }
    roots.push(root);
  };
  const enqueueShadowRoot = (node) => {
    try {
      if (node?.shadowRoot) enqueueRoot(node.shadowRoot);
    } catch (_error) {
      // Closed or restricted shadow roots are intentionally skipped.
    }
  };
  const visit = (node) => {
    if (!node || seenNodes.has(node)) return;
    if (result.nodes >= DIAGNOSTICS_MAX_DEEP_NODES) {
      result.dropped_nodes += 1;
      return;
    }
    seenNodes.add(node);
    result.nodes += 1;
    if (diagnosticsIsErrorCard(node)) {
      const error = diagnosticsNodeError(node, diagnosticsNodeText(node));
      const fields = diagnosticsErrorCardFields(node);
      const signature = [error.name, error.message, error.stack, fields.config_type_is_kc761, fields.node_path].join("|");
      if (!errors.has(signature)) errors.set(signature, { error, node, fields });
    }
    enqueueShadowRoot(node);
  };
  const documentRoot = globalThis.document || globalThis.window?.document;
  enqueueRoot(documentRoot);
  while (roots.length) {
    if (diagnosticsDeepNow() > deadline) {
      result.budget_exceeded = 1;
      break;
    }
    const root = roots.splice(0, 1)[0];
    if (!root || seenRoots.has(root)) continue;
    seenRoots.add(root);
    result.roots += 1;
    visit(root);
    let nodes = [];
    try {
      root.querySelectorAll?.("*")?.forEach?.((node) => nodes.push(node));
    } catch (_error) {
      nodes = [];
    }
    nodes.sort((left, right) => diagnosticsDeepPriority(left) - diagnosticsDeepPriority(right));
    for (const node of nodes) {
      if (diagnosticsDeepNow() > deadline) {
        result.budget_exceeded = 1;
        break;
      }
      visit(node);
      if (result.nodes >= DIAGNOSTICS_MAX_DEEP_NODES) break;
    }
    if (result.nodes >= DIAGNOSTICS_MAX_DEEP_NODES) {
      result.dropped_nodes += 1;
      break;
    }
  }
  result.error_cards = errors.size;
  if (registry) registry.exportSnapshot = result;
  for (const { error, fields } of errors.values()) {
    diagnosticsRecordFailure("lovelace-error-card-snapshot", error, {
      observed_error_card: true,
      deep_snapshot: true,
      ...fields,
    });
  }
  const storage = diagnosticsSessionStorage();
  if (storage) {
    const snapshot = diagnosticsReadSnapshot(storage);
    snapshot.export_snapshot = { ...result };
    diagnosticsWriteSnapshot(snapshot, storage);
  }
  return result;
}

function diagnosticsDownload() {
  const snapshot = diagnosticsReadSnapshot();
  if (snapshot.enabled !== true) return false;
  try {
    diagnosticsDeepSnapshot();
    const payload = JSON.stringify(diagnosticsExportPayload(), null, 2);
    const blob = new Blob([payload], { type: "application/json" });
    const url = globalThis.URL?.createObjectURL?.(blob);
    if (!url) throw new Error("Diagnostic download is unavailable");
    const documentRoot = globalThis.document || globalThis.window?.document;
    const link = documentRoot?.createElement?.("a");
    if (!link) throw new Error("Diagnostic download is unavailable");
    link.href = url;
    link.download = `kc761-diagnostics-${Date.now()}.json`;
    link.click?.();
    globalThis.URL?.revokeObjectURL?.(url);
    diagnosticsRecord("diagnostics-exported");
    return true;
  } catch (error) {
    diagnosticsRecordError("diagnostics-export-failure", error);
    return false;
  }
}

function diagnosticsRemoveGlobalButton(registry = diagnosticsListenerRegistry()) {
  if (!registry) return;
  try {
    const documentRoot = globalThis.document || globalThis.window?.document;
    if (registry.bodyReadyListener) documentRoot?.removeEventListener?.("DOMContentLoaded", registry.bodyReadyListener);
    if (registry.globalButton && registry.globalButtonHandler) registry.globalButton.removeEventListener?.("click", registry.globalButtonHandler);
    if (registry.globalButton?.remove) registry.globalButton.remove();
    else if (registry.globalButton?.parentNode?.removeChild) registry.globalButton.parentNode.removeChild(registry.globalButton);
  } catch (_error) {
    // Browser teardown must never turn diagnostics cleanup into a card error.
  }
  registry.globalButton = null;
  registry.globalButtonHandler = null;
  registry.bodyReadyListener = null;
}

function diagnosticsInstallGlobalButton(registry = diagnosticsListenerRegistry()) {
  if (!registry || diagnosticsReadSnapshot().enabled !== true) return;
  try {
    const documentRoot = globalThis.document || globalThis.window?.document;
    const body = documentRoot?.body;
    if (!body) {
      if (!registry.bodyReadyListener && documentRoot?.addEventListener) {
        registry.bodyReadyListener = () => {
          registry.bodyReadyListener = null;
          diagnosticsInstallGlobalButton(registry);
        };
        documentRoot.addEventListener("DOMContentLoaded", registry.bodyReadyListener, { once: true });
      }
      return;
    }
    if (registry.globalButton && (registry.globalButton.isConnected || registry.globalButton.parentNode)) return;
    const button = documentRoot.createElement("button");
    button.type = "button";
    button.textContent = "Export KC761 diagnostics";
    button.title = "Download redacted KC761 diagnostics after scanning open Shadow DOM";
    button.setAttribute("aria-label", "Export KC761 diagnostics");
    button.style.position = "fixed";
    button.style.right = "12px";
    button.style.bottom = "12px";
    button.style.zIndex = "2147483647";
    button.style.padding = "6px 10px";
    button.style.borderRadius = "6px";
    button.style.border = "1px solid var(--divider-color, #777)";
    button.style.background = "var(--card-background-color, #222)";
    button.style.color = "var(--primary-text-color, #fff)";
    button.style.font = "12px sans-serif";
    const handler = () => diagnosticsDownload();
    button.addEventListener("click", handler);
    if (body.appendChild) body.appendChild(button);
    else body.append?.(button);
    registry.globalButton = button;
    registry.globalButtonHandler = handler;
  } catch (_error) {
    // A restricted document or storage shell must not affect card setup.
  }
}

function diagnosticsObserveRoot(registry, root) {
  if (!registry || !root || registry.observedRoots.has(root)) return;
  const hostWindow = globalThis.window;
  const Observer = hostWindow?.MutationObserver || globalThis.MutationObserver;
  if (typeof Observer !== "function" || registry.observedRoots.size >= DIAGNOSTICS_MAX_OBSERVER_ROOTS) {
    if (registry.observedRoots.size >= DIAGNOSTICS_MAX_OBSERVER_ROOTS) registry.observerStats.dropped_roots += 1;
    return;
  }
  try {
    const observer = new Observer((records) => {
      if (diagnosticsReadSnapshot().enabled !== true) return;
      records?.forEach?.((record) => record?.addedNodes?.forEach?.((node) => diagnosticsScanErrorCards(node, registry)));
      diagnosticsScanErrorCards(root, registry);
      diagnosticsPersistObserverStats(registry);
    });
    observer.observe(root, { childList: true, subtree: true });
    registry.observedRoots.add(root);
    registry.observers.set(root, observer);
    registry.observerStats.roots = registry.observedRoots.size;
    if (!registry.observer) registry.observer = observer;
    diagnosticsScanErrorCards(root, registry);
    diagnosticsPersistObserverStats(registry);
  } catch (_error) {
    // Restricted or closed roots are ignored without affecting card setup.
  }
}

function diagnosticsInstallObserver(registry) {
  const hostWindow = globalThis.window;
  const documentRoot = globalThis.document || hostWindow?.document;
  if (!documentRoot) return;
  diagnosticsObserveRoot(registry, documentRoot);
}

function diagnosticsEnsureGlobal() {
  const registry = diagnosticsListenerRegistry();
  if (!registry || diagnosticsReadSnapshot().enabled !== true) return false;
  const hostWindow = globalThis.window;
  if (!registry.installed) {
    if (!hostWindow?.addEventListener) return false;
    registry.onError = (event) => {
      if (/kc761/i.test(diagnosticsSourceText(event))) diagnosticsRecordError("window-error", event.error || event);
    };
    registry.onRejection = (event) => {
      if (/kc761/i.test(diagnosticsSourceText(event))) diagnosticsRecordError("unhandled-rejection", event.reason || event);
    };
    try {
      hostWindow.addEventListener("error", registry.onError, true);
      hostWindow.addEventListener("unhandledrejection", registry.onRejection, true);
      registry.installed = true;
    } catch (_error) {
      registry.onError = null;
      registry.onRejection = null;
    }
  }
  diagnosticsInstallObserver(registry);
  diagnosticsInstallGlobalButton(registry);
  return registry.installed || Boolean(registry.observer);
}

function diagnosticsStopGlobal() {
  const registry = diagnosticsListenerRegistry();
  if (!registry) return;
  diagnosticsRemoveGlobalButton(registry);
  try {
    if (registry.installed) {
      globalThis.window?.removeEventListener?.("error", registry.onError, true);
      globalThis.window?.removeEventListener?.("unhandledrejection", registry.onRejection, true);
    }
    registry.observers?.forEach?.((observer) => observer?.disconnect?.());
  } catch (_error) {
    // Listener/observer cleanup is best effort in restricted host shells.
  }
  registry.cards.clear();
  registry.installed = false;
  registry.observer = null;
  registry.observers = new Map();
  registry.observedRoots = new Set();
  registry.observerStats = { roots: 0, scans: 0, dropped_roots: 0, scan_truncated: 0 };
  registry.exportSnapshot = diagnosticsEmptySnapshot(false).export_snapshot;
  registry.errorCards = new WeakSet();
  registry.onError = null;
  registry.onRejection = null;
}

function diagnosticsAttachCard(card) {
  const registry = diagnosticsListenerRegistry();
  if (!registry) return;
  registry.cards.add(card);
  diagnosticsEnsureGlobal();
}

function diagnosticsDetachCard(card) {
  const registry = diagnosticsListenerRegistry();
  if (!registry) return;
  registry.cards.delete(card);
  // Global diagnostics remain installed while the session flag is enabled so
  // a transient card replacement or a HA error card cannot erase evidence.
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
  static get resourceBuild() { return CARD_BUILD; }

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
    this._chartInitPromise = null;
    this._chartGeneration = 0;
    this._resizeObserver = null;
    this._layoutResizeObserver = null;
    this._layoutResizeRaf = null;
    this._chartResizeRaf = null;
    this._refreshTimer = null;
    this._refreshAfterVisible = false;
    this._visibilityListener = null;
    this._visibilityRefreshPromise = null;
    this._busyState = false;
    this._entityGeneration = 0;
    this._drawToken = 0;
    this._drawRaf = null;
    this._drawPending = false;
    this._fullDrawPending = false;
    this._summaryUpdatePending = false;
    this._handoffRetryTimer = null;
    this._handoffRetryCount = 0;
    this._handoffCandidate = null;
    this._preview = false;
    this._detached = false;
    this._colors = { ...COLOR_FALLBACKS };
    this._cursor = { roiStart: 0, roiEnd: this._maxChannel(), data: 0 };
    this._zoom = { ...ZOOM_DEFAULT };
    this._xFullDomain = false;
    this._yZoom = 1;
    this._yView = null;
    this._zoomDatasetKey = null;
    this._zoomHandler = null;
    this._chartEventSource = null;
    this._chartDoubleClickHandler = null;
    this._chartWheelHandler = null;
    this._chartWheelElement = null;
    this._dataMode = "live";
    this._viewStateKey = null;
    this._viewPersistTimer = null;
    this._viewPersistPending = false;
    this._pointCache = null;
    this._summaryCache = null;
    this._xAxisMode = DEFAULT_CARD_CONFIG.x_axis;
    this._historyLoaded = false;
    this._historyLoadPromise = null;
    this._historyRequestToken = 0;
    this._historySelectionToken = 0;
    this._themeRefreshRaf = null;
    this._themeValues = {};
    this._colorsReady = false;
    this._diagnosticsEnabled = false;
    this._destructiveActionsEnabled = false;
    this._sourceSelectionExplicit = false;
    diagnosticsRecord("constructor");
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
    if (!hasKC761Identity || !Array.isArray(points)) return false;
    if (attributes.spectrum_status !== undefined && !["complete", "ambiguous"].includes(String(attributes.spectrum_status))) return false;
    const hasDeclaredCount = attributes.point_count !== undefined && attributes.point_count !== null;
    const declaredCount = Number(attributes.point_count);
    if (hasDeclaredCount && !SUPPORTED_CHANNEL_COUNTS.includes(declaredCount)) return false;
    if (points.length === 0) return true;
    if (!SUPPORTED_CHANNEL_COUNTS.includes(points.length)) return false;
    if (hasDeclaredCount && declaredCount !== points.length) return false;
    return points.every((point, index) => {
      if (!Array.isArray(point) || point.length < 3) return false;
      const channel = Number(point[0]);
      return Number.isFinite(channel) && channel === index;
    });
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
    if (next.diagnostics === undefined) next.diagnostics = DEFAULT_CARD_CONFIG.diagnostics;
    if (typeof next.diagnostics !== "boolean") throw new Error("diagnostics must be true or false");
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
        {
          name: "diagnostics",
          title: "Advanced diagnostics",
          type: "expandable",
          flatten: true,
          schema: [{ name: "diagnostics", default: false, selector: { boolean: {} } }],
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
        diagnostics: "Diagnostics",
      })[schema.name] || schema.name,
      computeHelper: (schema) => {
        if (schema.name === "refresh_interval") return "0 disables automatic capture; enabled values are 5–3600 seconds.";
        if (schema.name === "diagnostics") return "Off by default. When enabled, logging survives reloads in this browser tab session until explicitly switched off. Export diagnostics downloads a local JSON file; nothing is automatically uploaded. Redaction is best-effort: review the file before sharing.";
        return undefined;
      },
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

  _setDiagnosticsEnabled(enabled, explicitlyConfigured = false) {
    const next = Boolean(enabled);
    if (next === this._diagnosticsEnabled) {
      if (next) {
        diagnosticsSetEnabled(true);
        diagnosticsAttachCard(this);
      } else if (explicitlyConfigured) {
        diagnosticsSetEnabled(false);
      }
      this._applyDiagnosticsVisibility();
      return;
    }
    if (next) {
      this._diagnosticsEnabled = true;
      diagnosticsSetEnabled(true);
      diagnosticsAttachCard(this);
    } else {
      diagnosticsDetachCard(this);
      this._diagnosticsEnabled = false;
      if (explicitlyConfigured) diagnosticsSetEnabled(false);
    }
    this._applyDiagnosticsVisibility();
  }

  _recordDiagnostic(event, fields = {}) {
    return diagnosticsRecord(event, fields);
  }

  _recordDiagnosticError(event, error) {
    return diagnosticsRecordError(event, error);
  }

  setConfig(config) {
    diagnosticsRecord("set-config-entry", { has_config: config !== null && config !== undefined });
    // Lovelace can briefly hand a recreated card no config while the
    // dashboard state is being assembled.  Treat only null/undefined as that
    // transient lifecycle state; malformed objects and invalid field values
    // still go through validateConfig and must remain visible errors.
    if (config === null || config === undefined) {
      diagnosticsRecord("set-config-missing");
      if (this._config && this._rendered) this._setStatus("Waiting for card configuration");
      return;
    }
    const previous = this._config;
    let normalized;
    try {
      normalized = KC761SpectrumCard.validateConfig(config);
    } catch (error) {
      diagnosticsRecordError("set-config-invalid", error);
      throw error;
    }
    const entityChanged = previous?.entity !== normalized.entity;
    const yAxisProvided = Object.prototype.hasOwnProperty.call(config || {}, "y_axis");
    if (entityChanged) this._resetEntityRuntime();
    this._config = normalized;
    const diagnosticsExplicitlyConfigured = Object.prototype.hasOwnProperty.call(config, "diagnostics");
    this._setDiagnosticsEnabled(normalized.diagnostics, diagnosticsExplicitlyConfigured);
    diagnosticsRecord("set-config-valid", {
      has_entity: Boolean(normalized.entity),
      has_refresh_interval: Object.prototype.hasOwnProperty.call(config, "refresh_interval"),
      has_y_axis: Object.prototype.hasOwnProperty.call(config, "y_axis"),
      diagnostics_enabled: normalized.diagnostics,
    });
    this._cancelThemeRefresh();
    this._refreshColors();
    this._setYAxis(this._initialYAxis(normalized.y_axis, yAxisProvided));
    this._xAxisMode = normalized.x_axis === "channel" ? "channel" : "energy";
    this._renderBase();
    this._applyVisibility();
    if (!this._config.entity) this._setStatus("Select a KC761 spectrum entity");
    this._syncAutoRefresh();
    if (this._data) this._draw();
  }

  connectedCallback() {
    const reconnecting = this._detached;
    this.setAttribute("data-kc761-build", CARD_BUILD);
    this._detached = false;
    diagnosticsRecord("connected");
    if (this._diagnosticsEnabled) {
      diagnosticsSetEnabled(true);
      diagnosticsAttachCard(this);
    }
    this._scheduleThemeRefresh();
    this._installVisibilityListener();
    this._ensureLayoutObserver();
    this._applyPresentationMode();
    this._syncAutoRefresh();
    if (this._data) this._draw();
    if (reconnecting && this._entryId && this._hass) void this._loadInitial();
  }

  disconnectedCallback() {
    diagnosticsRecord("disconnected");
    this._detached = true;
    this._entityGeneration += 1;
    this._busyState = false;
    this._syncOperationUI();
    this._historyRequestToken += 1;
    this._historySelectionToken += 1;
    this._historyLoadPromise = null;
    this._drawToken += 1;
    this._flushViewState();
    this._stopAutoRefresh();
    this._cancelThemeRefresh();
    this._removeVisibilityListener();
    this._refreshAfterVisible = false;
    this._clearEditorHandoffRetry();
    this._unregisterEditorHandoffCandidate();
    this._disposeChart();
    if (this._layoutResizeObserver) this._layoutResizeObserver.disconnect();
    this._layoutResizeObserver = null;
    this._cancelLayoutResize();
    if (this._diagnosticsEnabled) {
      diagnosticsDetachCard(this);
    }
  }

  set hass(hass) {
    diagnosticsRecord("hass-entry", { has_hass: Boolean(hass) });
    let previous = null;
    try {
      previous = this._hass?.states?.[this._config?.entity];
    } catch (_error) {
      // HA can replace the state object while a dashboard is reloading.
    }
    this._hass = hass;
    this._spectrumUnavailable = true;
    if (!this._config) return;
    this._scheduleThemeRefresh();
    if (!this._config.entity) {
      this._entryId = null;
      this._device = null;
      this._setDestructiveActionsEnabled(false);
      this._stopAutoRefresh();
      this._setStatus("Select a KC761 spectrum entity");
      return;
    }
    let states = null;
    try {
      states = hass?.states;
    } catch (_error) {
      states = null;
    }
    if (!states || typeof states !== "object") {
      diagnosticsRecord("hass-readiness", { has_states: false, state_object: false });
      this._entryId = null;
      this._device = null;
      this._setDestructiveActionsEnabled(false);
      this._stopAutoRefresh();
      this._setStatus("Waiting for Home Assistant state");
      return;
    }
    let current = null;
    try {
      current = states[this._config.entity];
    } catch (_error) {
      this._entryId = null;
      this._device = null;
      this._setDestructiveActionsEnabled(false);
      this._stopAutoRefresh();
      this._setStatus("Waiting for Home Assistant state");
      return;
    }
    if (!current) {
      diagnosticsRecord("hass-readiness", { has_states: true, state_object: true, state_present: false });
      this._setStatus(`Entity not found: ${this._config.entity}`, true);
      this._setDestructiveActionsEnabled(false);
      this._stopAutoRefresh();
      return;
    }
    let validSpectrum = false;
    try {
      validSpectrum = KC761SpectrumCard.isSpectrumEntityState(current);
    } catch (error) {
      diagnosticsRecordError("hass-state-read-failure", error);
      throw error;
    }
    diagnosticsRecord("hass-readiness", { has_states: true, state_object: true, state_present: true, valid_spectrum: validSpectrum });
    if (!validSpectrum) {
      if (current.state === "unavailable") {
        // HA may omit all extra attributes while an entity is unavailable.
        // Keep strict KC761 validation for every usable state, but do not
        // discard the last good spectrum or mislabel this transient state as
        // a different entity.  A later valid state will repopulate normally.
        this._stopAutoRefresh();
        this._setDestructiveActionsEnabled(false);
        this._setStatus("Selected spectrum entity is unavailable", true);
        return;
      }
      this._entryId = null;
      this._device = null;
      this._setDestructiveActionsEnabled(false);
      this._stopAutoRefresh();
      this._setStatus("Selected entity is not a KC761 spectrum entity", true);
      return;
    }
    this._entryId = current.attributes.config_entry_id;
    this._spectrumUnavailable = false;
    this._setDestructiveActionsEnabled(current.attributes.destructive_actions_enabled);
    const historySelect = this.shadowRoot.querySelector("#history");
    if ((!historySelect || historySelect.value === "latest") && (current !== previous || !this._data)) {
      this._setData(this._fromEntity(current), { viewMode: "live" });
      this._updateSummary();
      this._draw();
    }
    if (this._entryId && !this._device) this._loadInitial();
    this._syncAutoRefresh();
  }

  getCardSize() {
    // Keep Masonry sizing deterministic.  Reading the rendered card height
    // here feeds the result back into HA's row calculation and can create a
    // self-referential 24-row card after a new control row is added.
    return 9;
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
      this._applyDiagnosticsVisibility();
      this._ensureLayoutObserver();
      this._applyPresentationMode();
      return;
    }
    this._rendered = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;min-width:0;min-height:0;height:100%;overflow:hidden} ha-card{box-sizing:border-box;height:100%;min-height:0;padding:16px;overflow:auto;display:flex;flex-direction:column}[hidden]{display:none!important}
        h2{margin:0 0 8px;font-size:20px}.header{display:flex;align-items:center;gap:8px}.header h2{flex:1;min-width:0}.zoom-meta{display:flex;align-items:center;gap:6px;min-width:0}.zoom-readout{max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:var(--secondary-text-color)}.reset-zoom{font-size:12px;padding:5px 8px}.toolbar,.roi,.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:end}
        label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--secondary-text-color)}
        select,input,button{font:inherit;padding:6px 8px;border-radius:6px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color)}
        button{cursor:pointer}button.danger{color:var(--error-color)}button:disabled{opacity:.45;cursor:not-allowed}
        .chart-wrap{position:relative;margin-top:8px;height:370px;flex:0 1 370px;min-height:220px;border-radius:6px;background:var(--card-background-color);overflow:hidden}
        #chart{width:100%;height:100%}
        .legend{display:flex;gap:12px;flex-wrap:wrap;margin-top:7px;font-size:12px;color:var(--secondary-text-color)}.legend span{display:inline-flex;gap:5px;align-items:center}.swatch{width:12px;height:3px;display:inline-block;border-radius:2px}.swatch.roi{background:var(--kc761-spectrum-roi-start,${COLOR_FALLBACKS.roiStart})}.swatch.peak{background:var(--kc761-spectrum-peak,${COLOR_FALLBACKS.peak})}.swatch.data{background:var(--kc761-spectrum-data,${COLOR_FALLBACKS.dataCursor})}.swatch.curve{background:var(--kc761-spectrum-curve,${COLOR_FALLBACKS.curve})}
        .summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));grid-auto-rows:58px;gap:6px;margin:8px 0;font-size:12px}.summary div{box-sizing:border-box;min-width:0;height:58px;padding:6px 8px;background:var(--secondary-background-color);border:1px solid var(--divider-color);border-radius:6px;overflow:hidden;display:flex;flex-direction:column}.summary b{display:block;min-width:0;height:1.3em;max-height:1.3em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px;line-height:1.3;margin-top:2px}
        .status{min-height:20px;margin-top:8px;color:var(--secondary-text-color)}.status.error{color:var(--error-color)}.actions{margin-top:10px}.spacer{flex:1}
        .history-runtime{position:relative;z-index:4;display:flex;align-items:end;gap:8px;margin-top:4px}.history-runtime label{min-width:0;flex:1 1 280px}.history-runtime select{position:relative;z-index:5;max-width:100%;overflow:hidden;text-overflow:ellipsis}
        .help{font-size:12px;color:var(--secondary-text-color);margin-top:5px}.roi-slider{position:relative;min-width:250px;flex:1 1 300px;padding:8px 0 4px}.data-slider{position:relative;width:100%;min-width:0;flex:none;padding:8px 0 4px}.data-slider-row{display:flex;gap:8px;align-items:end;margin-top:4px}.data-slider-label{flex:1 1 300px;min-width:250px}.roi-slider-track,.data-slider-track{position:absolute;left:0;right:0;top:20px;height:4px;border-radius:4px;background:var(--divider-color)}.roi-slider-fill{position:absolute;top:0;height:4px;border-radius:4px;background:var(--kc761-spectrum-roi-start,${COLOR_FALLBACKS.roiStart});opacity:.9}.roi-slider input,.data-slider input{position:absolute;left:0;top:8px;width:100%;height:24px;margin:0;padding:0;border:0;background:transparent;appearance:none}.roi-slider input{pointer-events:none}.roi-slider input::-webkit-slider-thumb,.data-slider input::-webkit-slider-thumb{appearance:none;width:18px;height:18px;border:2px solid var(--card-background-color);border-radius:50%;background:currentColor;box-shadow:0 0 0 1px currentColor;pointer-events:auto;cursor:ew-resize}.roi-slider input::-moz-range-thumb,.data-slider input::-moz-range-thumb{width:14px;height:14px;border:2px solid var(--card-background-color);border-radius:50%;background:currentColor;box-shadow:0 0 0 1px currentColor;pointer-events:auto;cursor:ew-resize}.roi-slider input:first-of-type{color:var(--kc761-spectrum-roi-start,${COLOR_FALLBACKS.roiStart});z-index:2}.roi-slider input:last-of-type{color:var(--kc761-spectrum-roi-end,${COLOR_FALLBACKS.roiEnd});z-index:3}.data-slider input{color:var(--kc761-spectrum-data,${COLOR_FALLBACKS.dataCursor});z-index:3}.roi-values,.data-values{display:flex;justify-content:space-between;gap:8px;font-variant-numeric:tabular-nums;margin-top:20px}.data-values span:last-child{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:right}.editor-only{display:block}.configure{font-size:12px;padding:5px 8px}
        @media (max-width:600px){.summary{grid-template-columns:repeat(2,minmax(0,1fr))}.header h2{font-size:18px}}
        ha-card.compact-height .chart-wrap{height:240px;flex-basis:240px;min-height:200px}ha-card.compact-height .summary{grid-auto-rows:52px}ha-card.compact-height .summary div{height:52px;padding:5px 7px}ha-card.compact-height .summary b{font-size:13px}ha-card.compact-height .actions{margin-top:6px}ha-card.compact-height select,ha-card.compact-height input,ha-card.compact-height button{padding:5px 7px}
      </style>
      <ha-card>
        <div class="header"><h2 id="title"></h2><div class="zoom-meta"><span id="zoom-readout" class="zoom-readout" role="status" aria-live="polite">View CH 0–2047</span><button id="reset-zoom" class="reset-zoom" type="button" disabled>Reset zoom</button></div><button id="export-diagnostics" class="configure" type="button" hidden>Export diagnostics</button><button id="configure" class="configure" type="button">Configure</button></div>
        <div id="controls">
          <div id="history-control" class="history-runtime">
            <label>Saved spectrum<select id="history"><option value="latest">Latest</option></select></label>
          </div>
          <div id="editor-controls" class="toolbar editor-only">
            <label>Channel<select id="source"></select></label>
            <label>X axis<select id="xaxis"><option value="energy">Energy (keV)</option><option value="channel">Channel</option></select></label>
            <label>Y axis<select id="yaxis"><option value="linear">Linear</option><option value="log">Logarithmic</option></select></label>
          </div>
          <div class="roi"><div class="roi-slider" role="group" aria-label="ROI channel range"><div class="roi-slider-track"><span id="roi-range-fill" class="roi-slider-fill"></span></div><input id="roi-start" type="range" min="0" max="2047" step="1" value="0" aria-label="ROI start channel" aria-valuemin="0" aria-valuemax="2047" aria-valuenow="0"><input id="roi-end" type="range" min="0" max="2047" step="1" value="2047" aria-label="ROI end channel" aria-valuemin="0" aria-valuemax="2047" aria-valuenow="2047"><div class="roi-values"><span id="roi-start-value">Start CH 0</span><span aria-hidden="true">·</span><span id="roi-end-value">End CH 2047</span></div></div>
          </div>
          <div class="data-slider-row"><label class="data-slider-label">Data cursor<div class="data-slider" role="group" aria-label="Data cursor channel"><div class="data-slider-track"></div><input id="data-cursor-slider" type="range" min="0" max="2047" step="1" value="0" role="slider" aria-label="Data cursor channel" aria-valuemin="0" aria-valuemax="2047" aria-valuenow="0" aria-valuetext="Channel 0"><div class="data-values"><span id="data-cursor-channel">CH 0</span><span id="data-cursor-readout">—</span></div></div></label></div>
          <div class="help">Use the ROI handles to select a range and the Data cursor slider to inspect a point. Ctrl+wheel zooms X; Shift+wheel zooms Y. Both zoom around the pointer. Double-click the plot to reset both axes.</div>
        </div>
        <div class="summary">
          <div data-summary="acquired">Acquired<b id="acquired">—</b></div><div data-summary="runtime">Accumulation<b id="runtime">—</b></div>
          <div data-summary="total">Total counts<b id="total">—</b></div><div data-summary="peak">Peak<b id="peak">—</b></div><div data-summary="roi-total">ROI counts<b id="roi-total">—</b></div><div data-summary="spectrum-status">Length<b id="spectrum-status">—</b></div>
          <div>Data cursor<b id="cursor-value">—</b></div>
        </div>
        <div id="chart-wrap" class="chart-wrap">
          <div id="chart" role="img" aria-label="KC761 spectrum chart"></div>
        </div>
        <div class="legend"><span><i class="swatch curve"></i> Spectrum</span><span><i class="swatch roi"></i> ROI range</span><span data-legend="peak"><i class="swatch peak"></i> Auto peak</span><span><i class="swatch data"></i> Data cursor</span></div>
        <div id="actions" class="actions">
          <button id="capture">Refresh</button><button id="save">Save spectrum</button><button id="csv">Export CSV</button>
          <span class="spacer"></span><button class="danger" data-clear="spectrum" hidden>Clear spectrum</button>
          <button class="danger" data-clear="dose_curve" hidden>Clear dose curve</button><button class="danger" data-clear="cumulative_dose" hidden>Clear cumulative dose</button>
        </div>
        <div id="busy-indicator" class="help" role="status" aria-live="polite" hidden></div>
        <div id="status" class="status"></div>
      </ha-card>`;
    this.shadowRoot.querySelector("#title").textContent = this._config.title || "KC761 Spectrum";
    this.shadowRoot.querySelector("#yaxis").value = this._yAxis;
    this.shadowRoot.querySelector("#xaxis").value = this._config.x_axis || "energy";
    this.shadowRoot.querySelector("#xaxis").addEventListener("change", (event) => {
      this._xAxisMode = event.target.value === "channel" ? "channel" : "energy";
      this._draw();
    });
    this.shadowRoot.querySelector("#source").addEventListener("change", () => {
      // A user-selected channel must win over the device snapshot until the
      // card is deliberately rebound to another entity.  Initial loading is
      // the only path allowed to replace a stale latest source implicitly.
      this._sourceSelectionExplicit = true;
      void this._capture();
    });
    this.shadowRoot.querySelector("#yaxis").addEventListener("change", (event) => {
      this._setYAxis(event.target.value);
      this._persistYAxis();
      this._draw();
    });
    this.shadowRoot.querySelector("#roi-start").addEventListener("input", () => this._syncRoiFromInputs("start"));
    this.shadowRoot.querySelector("#roi-end").addEventListener("input", () => this._syncRoiFromInputs("end"));
    this.shadowRoot.querySelector("#data-cursor-slider").addEventListener("input", (event) => this._setCursorChannel("data", event.target.value));
    this.shadowRoot.querySelector("#reset-zoom").addEventListener("click", () => this._resetZoom());
    const history = this.shadowRoot.querySelector("#history");
    // Begin loading before the native picker opens.  Updating the option list
    // from focus/pointerdown can make Chromium snapshot the popup while it
    // still contains only "Latest", leaving the first expansion clipped.
    history.addEventListener("pointerenter", () => this._requestHistoryFromInteraction());
    history.addEventListener("focus", () => this._requestHistoryFromInteraction());
    history.addEventListener("pointerdown", () => this._requestHistoryFromInteraction());
    history.addEventListener("change", (event) => this._selectHistory(event.target.value));
    this.shadowRoot.querySelector("#capture").addEventListener("click", () => this._capture());
    this.shadowRoot.querySelector("#save").addEventListener("click", () => this._save());
    this.shadowRoot.querySelector("#csv").addEventListener("click", () => this._csv());
    this.shadowRoot.querySelector("#export-diagnostics").addEventListener("click", () => this._exportDiagnostics());
    this.shadowRoot.querySelector("#configure").addEventListener("click", () => this._openEditor());
    this.shadowRoot.querySelectorAll("[data-clear]").forEach((button) => button.addEventListener("click", () => this._clear(button.dataset.clear)));
    this._setDestructiveActionsEnabled(this._destructiveActionsEnabled);
    this._updateRoiSliderUI();
    this._updateDataCursorUI();
    this._updateZoomReadout();
    this._applyVisibility();
    this._applyDiagnosticsVisibility();
    this._ensureLayoutObserver();
    this._applyPresentationMode();
  }

  _applyDiagnosticsVisibility() {
    const button = this.shadowRoot?.querySelector("#export-diagnostics");
    if (button) {
      button.hidden = !this._diagnosticsEnabled;
      button.title = "Download redacted KC761 card diagnostics";
      button.setAttribute("aria-label", "Export redacted diagnostics");
    }
  }

  _exportDiagnostics() {
    if (!this._diagnosticsEnabled) return;
    if (!diagnosticsDownload()) this._setStatus("Diagnostics export failed", true);
  }

  _applyPresentationMode() {
    if (!this._rendered) return;
    const path = this._findLovelacePath();
    const dialogPreview = this._preview && !path;
    const editorControls = this.shadowRoot.querySelector("#editor-controls");
    const configure = this.shadowRoot.querySelector("#configure");
    if (editorControls) editorControls.hidden = !dialogPreview;
    if (configure) configure.hidden = dialogPreview;
    if (dialogPreview && this._config?.show_controls !== false) {
      void this._requestHistoryLoad().catch((error) => this._handleHistoryError("history-load-failure", error));
    }
    if (path && this._preview) {
      this._clearEditorHandoffRetry();
      this._tryConsumeEditorHandoff(path);
    } else if (this._preview) {
      this._scheduleEditorHandoffRetry();
    } else {
      this._clearEditorHandoffRetry();
    }
  }

  _documentIsHidden() {
    try {
      return (globalThis.document || globalThis.window?.document)?.visibilityState === "hidden";
    } catch (_error) {
      return false;
    }
  }

  _installVisibilityListener() {
    if (this._visibilityListener) return;
    const documentRoot = globalThis.document || globalThis.window?.document;
    if (!documentRoot?.addEventListener) return;
    this._visibilityListener = () => {
      if (this._documentIsHidden()) {
        this._refreshAfterVisible = this._refreshAfterVisible || Boolean(this._refreshTimer);
        this._stopAutoRefresh();
        return;
      }
      this._syncAutoRefresh();
      if (this._refreshAfterVisible) this._scheduleVisibleRefresh();
    };
    try {
      documentRoot.addEventListener("visibilitychange", this._visibilityListener);
    } catch (_error) {
      this._visibilityListener = null;
    }
  }

  _removeVisibilityListener() {
    if (!this._visibilityListener) return;
    const documentRoot = globalThis.document || globalThis.window?.document;
    try {
      documentRoot?.removeEventListener?.("visibilitychange", this._visibilityListener);
    } catch (_error) {
      // Browser teardown must not turn visibility cleanup into a card error.
    }
    this._visibilityListener = null;
  }

  _scheduleVisibleRefresh() {
    if (
      !this._refreshAfterVisible
      || this._visibilityRefreshPromise
      || this._documentIsHidden()
      || this._detached
      || this._busyState
      || !this._entryId
      || !this._data
    ) return;
    this._refreshAfterVisible = false;
    this._visibilityRefreshPromise = Promise.resolve(this._autoRefresh()).finally(() => {
      this._visibilityRefreshPromise = null;
      if (this._refreshAfterVisible && !this._documentIsHidden()) this._scheduleVisibleRefresh();
    });
  }

  _ensureLayoutObserver() {
    const card = this.shadowRoot?.querySelector("ha-card");
    if (!card) return;
    this._updateCompactHeight();
    if (this._layoutResizeObserver || typeof ResizeObserver !== "function") return;
    this._layoutResizeObserver = new ResizeObserver(() => this._scheduleLayoutResize());
    this._layoutResizeObserver.observe(card);
  }

  _scheduleLayoutResize() {
    if (this._detached || this._layoutResizeRaf !== null) return;
    const request = globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 0));
    this._layoutResizeRaf = request(() => {
      this._layoutResizeRaf = null;
      if (!this._detached) this._updateCompactHeight();
    });
  }

  _cancelLayoutResize() {
    if (this._layoutResizeRaf === null) return;
    const cancel = globalThis.cancelAnimationFrame || ((id) => clearTimeout(id));
    cancel(this._layoutResizeRaf);
    this._layoutResizeRaf = null;
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

  _setDestructiveActionsEnabled(value) {
    this._destructiveActionsEnabled = value === true;
    this.shadowRoot?.querySelectorAll?.("[data-clear]")?.forEach((button) => {
      button.hidden = !this._destructiveActionsEnabled;
    });
    this._syncOperationUI();
  }

  _syncOperationUI() {
    const unavailable = this._detached || !this._entryId || this._spectrumUnavailable;
    // Configure, diagnostics export and local view controls do not issue data
    // operations and must remain usable during an initial or automatic capture.
    for (const selector of ["#capture", "#save", "#csv", "#source", "#history"]) {
      const node = this.shadowRoot?.querySelector(selector);
      if (node) node.disabled = Boolean(this._busyState || unavailable);
    }
    this.shadowRoot?.querySelectorAll?.("[data-clear]")?.forEach((button) => {
      button.disabled = Boolean(this._busyState || unavailable || !this._destructiveActionsEnabled || !this._device);
    });
    const indicator = this.shadowRoot?.querySelector("#busy-indicator");
    if (indicator) {
      indicator.hidden = !this._busyState;
      indicator.textContent = this._busyState ? "Working…" : "";
    }
  }

  _resetEntityRuntime() {
    this._entityGeneration = (this._entityGeneration || 0) + 1;
    this._busyState = false;
    this._drawToken += 1;
    this._flushViewState();
    this._stopAutoRefresh();
    this._device = null;
    this._entryId = null;
    this._setDestructiveActionsEnabled(false);
    this._data = null;
    this._history = [];
    this._sourceSelectionExplicit = false;
    this._historyLoaded = false;
    this._historyLoadPromise = null;
    this._historySelectionToken += 1;
    this._pointCache = null;
    this._summaryCache = null;
    this._cursor = { roiStart: 0, roiEnd: this._maxChannel(), data: 0 };
    this._zoom = { ...ZOOM_DEFAULT };
    this._xFullDomain = false;
    this._yZoom = 1;
    this._yView = null;
    this._zoomDatasetKey = null;
    this._dataMode = "live";
    this._viewStateKey = null;
    this._disposeChart();
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
    if (end) end.value = String(this._maxChannel());
    this._updateRoiSliderUI();
    this._updateDataCursorUI();
    this._updateZoomReadout();
  }

  _pointCount() {
    const declared = Number(this._data?.point_count);
    const points = Array.isArray(this._data?.points) ? this._data.points.length : 0;
    if (points > 0) {
      // A non-empty payload is authoritative.  Never let stale or malformed
      // metadata move the cursors beyond the points actually rendered.
      return SUPPORTED_CHANNEL_COUNTS.includes(points) ? points : 2048;
    }
    return SUPPORTED_CHANNEL_COUNTS.includes(declared) ? declared : 2048;
  }

  _maxChannel() {
    return this._pointCount() - 1;
  }

  _syncChannelBounds() {
    const max = this._maxChannel();
    for (const selector of ["#roi-start", "#roi-end", "#data-cursor-slider"]) {
      const input = this.shadowRoot?.querySelector(selector);
      if (!input) continue;
      input.max = String(max);
      input.setAttribute("aria-valuemax", String(max));
    }
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
    this._syncChannelBounds();
    const maxChannel = this._maxChannel();
    const startValueNumber = Number(this._cursor.roiStart);
    const endValueNumber = Number(this._cursor.roiEnd);
    const start = clamp(Number.isFinite(startValueNumber) ? startValueNumber : 0, 0, maxChannel);
    const end = clamp(Number.isFinite(endValueNumber) ? endValueNumber : maxChannel, start, maxChannel);
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
    if (startValue) {
      startValue.textContent = `Start CH ${start}`;
      startValue.title = `Start channel ${start}`;
      startValue.setAttribute("aria-label", `Start channel ${start}`);
    }
    if (endValue) {
      endValue.textContent = `End CH ${end}`;
      endValue.title = `End channel ${end}`;
      endValue.setAttribute("aria-label", `End channel ${end}`);
    }
    if (fill) {
      fill.style.left = `${(start / maxChannel) * 100}%`;
      fill.style.right = `${100 - (end / maxChannel) * 100}%`;
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

  _normalizeViewStateMode(mode, data = this._data) {
    if (mode !== undefined && mode !== null) {
      const explicit = String(mode);
      return explicit.startsWith("history:") && explicit.length > 8 ? explicit : "live";
    }
    const snapshotId = data?.snapshot_id;
    return snapshotId !== undefined && snapshotId !== null && String(snapshotId) !== "" && String(snapshotId) !== "latest"
      ? `history:${String(snapshotId)}`
      : "live";
  }

  _viewStateKeyFor(data = this._data, mode = null) {
    const entity = this._config?.entity;
    if (!entity) return null;
    const source = data?.source === undefined || data?.source === null ? "unknown" : String(data.source);
    const channel = data?.channel === undefined || data?.channel === null ? "unknown" : String(data.channel);
    const normalizedMode = this._normalizeViewStateMode(mode, data);
    const pointCount = this._viewStatePointCount(data);
    return `${VIEW_STATE_STORAGE_PREFIX}${[entity, channel, source, normalizedMode, pointCount].map((value) => encodeURIComponent(value)).join(":")}`;
  }

  _legacyViewStateKeyFor(data = this._data, mode = null) {
    const entity = this._config?.entity;
    if (!entity) return null;
    const source = data?.source === undefined || data?.source === null ? "unknown" : String(data.source);
    const channel = data?.channel === undefined || data?.channel === null ? "unknown" : String(data.channel);
    const normalizedMode = this._normalizeViewStateMode(mode, data);
    return `${VIEW_STATE_STORAGE_PREFIX}${[entity, channel, source, normalizedMode].map((value) => encodeURIComponent(value)).join(":")}`;
  }

  _viewStatePointCount(data = this._data) {
    const points = Array.isArray(data?.points) ? data.points.length : 0;
    if (SUPPORTED_CHANNEL_COUNTS.includes(points)) return points;
    const declared = Number(data?.point_count);
    return Number.isInteger(declared) && SUPPORTED_CHANNEL_COUNTS.includes(declared) ? declared : "unknown";
  }

  _readViewState(key = this._viewStateKey) {
    const storage = spectrumViewStateStorage();
    if (!storage || !key) return null;
    try {
      const keys = [key];
      const legacyKey = this._legacyViewStateKeyFor();
      if (legacyKey && legacyKey !== key) keys.push(legacyKey);
      for (const candidate of keys) {
        const raw = storage.getItem(candidate);
        if (!raw) continue;
        const state = JSON.parse(raw);
        if (state && state.schema === VIEW_STATE_SCHEMA_VERSION) return state;
      }
      return null;
    } catch (_error) {
      // Corrupt JSON and blocked localStorage are both safe no-op cases.
      return null;
    }
  }

  _zoomFromChannelRange(startChannel, endChannel) {
    const points = this._points();
    if (points.length < 2) return { ...ZOOM_DEFAULT };
    const last = points.length - 1;
    const startIndex = clamp(this._pointIndexForChannel(startChannel), 0, last);
    const endIndex = clamp(this._pointIndexForChannel(endChannel), startIndex, last);
    return this._clampZoom((startIndex / last) * 100, (endIndex / last) * 100);
  }

  _restoreViewState() {
    let state = this._readViewState();
    const currentPointCount = this._viewStatePointCount(this._data);
    const savedPointCount = Number(state?.point_count);
    // Records written before point-count isolation had no point_count field;
    // they are safe to reuse only for the historical 2048-point default.  A
    // record from another supported size must never seed a different dataset.
    if (state && (!Number.isInteger(savedPointCount) || !SUPPORTED_CHANNEL_COUNTS.includes(savedPointCount))) {
      state = currentPointCount === 2048 ? state : null;
    } else if (state && currentPointCount !== "unknown" && savedPointCount !== currentPointCount) {
      state = null;
    }
    const roiStart = Number(state?.roi?.start_channel);
    const roiEnd = Number(state?.roi?.end_channel);
    const dataCursor = Number(state?.data_cursor_channel);
    const maxChannel = this._maxChannel();
    const start = clamp(Number.isFinite(roiStart) ? Math.round(roiStart) : 0, 0, maxChannel);
    const end = clamp(Number.isFinite(roiEnd) ? Math.round(roiEnd) : maxChannel, start, maxChannel);
    this._cursor = {
      roiStart: start,
      roiEnd: end,
      data: this._dataCursorChannel(Number.isFinite(dataCursor) ? Math.round(dataCursor) : 0),
    };
    const zoomStart = Number(state?.zoom?.start_channel);
    this._xFullDomain = state?.x_full_domain === true;
    const zoomEnd = Number(state?.zoom?.end_channel);
    this._zoom = Number.isFinite(zoomStart) && Number.isFinite(zoomEnd)
      ? this._zoomFromChannelRange(zoomStart, zoomEnd)
      : { ...ZOOM_DEFAULT };
    const yZoom = state?.y_zoom;
    this._yZoom = typeof yZoom === "number" && Number.isFinite(yZoom) ? clamp(yZoom, 0.01, 1) : 1;
    const yView = state?.y_view;
    this._yView = yView && [yView.min, yView.max, yView.full_max].every((value) => typeof value === "number" && Number.isFinite(value))
      && yView.min >= 0 && yView.max > yView.min && yView.full_max >= yView.max
      ? { min: yView.min, max: yView.max, full_max: yView.full_max } : null;
    this._updateRoiSliderUI();
    this._updateDataCursorUI();
    this._updateZoomReadout();
  }

  _viewStatePayload() {
    const [zoomStart, zoomEnd] = this._zoomChannelRange();
    const data = this._data || {};
    const [roiStart, roiEnd] = this._roi();
    return {
      schema: VIEW_STATE_SCHEMA_VERSION,
      entity: this._config?.entity || "",
      channel: data.channel === undefined || data.channel === null ? "unknown" : String(data.channel),
      source: data.source === undefined || data.source === null ? "unknown" : String(data.source),
      mode: this._dataMode,
      point_count: this._viewStatePointCount(data),
      roi: { start_channel: roiStart, end_channel: roiEnd },
      data_cursor_channel: this._dataCursorChannel(this._cursor.data),
      zoom: { start_channel: zoomStart, end_channel: zoomEnd },
      x_full_domain: this._xFullDomain === true,
      y_zoom: this._yZoom,
      y_view: this._yView ? { ...this._yView } : null,
    };
  }

  _flushViewState() {
    if (this._viewPersistTimer !== null && typeof globalThis.clearTimeout === "function") {
      globalThis.clearTimeout(this._viewPersistTimer);
    }
    this._viewPersistTimer = null;
    if (!this._viewPersistPending) return;
    this._viewPersistPending = false;
    const storage = spectrumViewStateStorage();
    if (!storage || !this._viewStateKey || !this._data) return;
    try {
      storage.setItem(this._viewStateKey, JSON.stringify(this._viewStatePayload()));
    } catch (_error) {
      // Storage quota/privacy failures must not affect the chart or network.
    }
  }

  _scheduleViewStatePersist({ flush = false } = {}) {
    this._viewPersistPending = true;
    if (flush || typeof globalThis.setTimeout !== "function") {
      this._flushViewState();
      return;
    }
    if (this._viewPersistTimer !== null) return;
    this._viewPersistTimer = globalThis.setTimeout(() => {
      this._viewPersistTimer = null;
      this._flushViewState();
    }, VIEW_STATE_PERSIST_DELAY_MS);
  }

  _datasetIdentity(data) {
    if (!data) return null;
    const points = Array.isArray(data.points) ? data.points : [];
    const snapshot = data.snapshot_id == null ? "latest" : String(data.snapshot_id);
    const source = data.source == null ? "" : String(data.source);
    const channel = data.channel == null ? "" : String(data.channel);
    return `${snapshot}|source:${source}|channel:${channel}|points:${points.length}`;
  }

  _setData(data, { viewMode = null } = {}) {
    if (!data) return;
    const identity = this._datasetIdentity(data);
    const normalizedMode = this._normalizeViewStateMode(viewMode, data);
    const nextKey = this._viewStateKeyFor(data, normalizedMode);
    const keyChanged = nextKey !== this._viewStateKey;
    if (keyChanged && this._viewStateKey) this._flushViewState();
    const previousRange = !keyChanged && this._data ? this._zoomChannelRange() : null;
    this._dataMode = normalizedMode;
    this._viewStateKey = nextKey;
    this._zoomDatasetKey = identity;
    this._data = data;
    this._syncChannelBounds();
    if (keyChanged) this._restoreViewState();
    else if (previousRange) this._zoom = this._zoomFromChannelRange(previousRange[0], previousRange[1]);
    else this._zoom = this._clampZoom(this._zoom.start, this._zoom.end);
    this._cursor.data = this._dataCursorChannel(this._cursor.data);
    this._updateDataCursorUI();
    this._updateZoomReadout();
  }

  _zoomMinSpan() {
    const count = this._points().length;
    if (count < 2) return 100;
    return Math.min(100, Math.max(0.25, (Math.min(5, count) / (count - 1)) * 100));
  }

  _clampZoom(start, end) {
    let low = Number.isFinite(Number(start)) ? clamp(Number(start), 0, 100) : 0;
    let high = Number.isFinite(Number(end)) ? clamp(Number(end), 0, 100) : 100;
    if (high < low) [low, high] = [high, low];
    const minimum = this._zoomMinSpan();
    if (high - low < minimum) {
      const center = (low + high) / 2;
      low = center - minimum / 2;
      high = center + minimum / 2;
      if (low < 0) { high -= low; low = 0; }
      if (high > 100) { low -= high - 100; high = 100; }
      low = Math.max(0, low);
    }
    return { start: Number(low.toFixed(4)), end: Number(high.toFixed(4)) };
  }

  _resetZoomState() {
    this._zoom = { ...ZOOM_DEFAULT };
    this._xFullDomain = false;
    this._yZoom = 1;
    this._yView = null;
    this._updateZoomReadout();
  }

  _setZoomRange(start, end) {
    const next = this._clampZoom(start, end);
    if (Math.abs(next.start - this._zoom.start) < 0.0001 && Math.abs(next.end - this._zoom.end) < 0.0001) return;
    this._zoom = next;
    this._updateZoomReadout();
    this._scheduleViewStatePersist();
  }

  _onZoom(event) {
    const payload = event?.batch?.[0] || event;
    if (!payload) return;
    const start = Number(payload.start);
    const end = Number(payload.end);
    if (Number.isFinite(start) && Number.isFinite(end)) {
      this._setZoomRange(this._axisToChannelZoom(start), this._axisToChannelZoom(end));
    }
  }

  _resetZoom() {
    this._resetZoomState();
    this._scheduleViewStatePersist({ flush: true });
    const [min, max] = this._xRange();
    this._chart?.setOption?.({ xAxis: { min, max } }, { silent: true });
    if (this._chart) this._chart.dispatchAction({ type: "dataZoom", dataZoomIndex: [0, 1], start: 0, end: 100 });
    this._applyYZoom();
  }

  _yZoomMaximum(zoom = this._yZoom) {
    if (zoom >= 1) return null; // Restore ECharts' ordinary automatic maximum.
    const logarithmic = this._yAxis === "log";
    const maximum = Math.max(logarithmic ? 10 : 1, this._ensurePointCache().peak?.[2] || 0);
    return logarithmic ? Math.max(1.01, Math.exp(Math.log(maximum) * zoom)) : Math.max(1, maximum * zoom);
  }

  _applyYZoom() {
    this._chart?.setOption?.({ yAxis: this._yAxisBounds() }, { lazyUpdate: false, silent: true });
  }

  _yAxisBounds() {
    const baseline = this._yAxis === "log" ? 1 : 0;
    const minimum = Math.max(baseline, this._yView?.min || 0);
    return this._yView && this._yView.max > minimum
      ? { min: minimum, max: this._yView.max }
      : { min: baseline, max: this._yZoomMaximum() };
  }

  _wheelPlotPosition(chart, native) {
    // SVG child offsetX/Y belong to the child, not the plotting viewport.
    // Viewport bounds include its actual padding offset and CSS transform.
    const viewport = chart.getZr()?.painter?.getViewportRoot?.();
    const element = viewport?.getBoundingClientRect ? viewport : this._chartWheelElement;
    const rect = element?.getBoundingClientRect();
    if (!rect || !(rect.width > 0) || !(rect.height > 0)
      || !Number.isFinite(native.clientX) || !Number.isFinite(native.clientY)) return null;
    return [(native.clientX - rect.left) * chart.getWidth() / rect.width,
      (native.clientY - rect.top) * chart.getHeight() / rect.height];
  }

  _anchoredWindow(low, high, anchor, factor, minimum, maximum, minimumSpan) {
    if (![low, high, anchor, factor, minimum, maximum, minimumSpan].every(Number.isFinite)
      || high <= low || maximum <= minimum) return null;
    const span = clamp((high - low) * factor, minimumSpan, maximum - minimum);
    if (Math.abs(span - (high - low)) <= Math.max(1, high - low) * 1e-10) return null;
    let start = anchor - (anchor - low) * (span / (high - low));
    start = clamp(start, minimum, maximum - span);
    return [start, start + span];
  }

  _zoomYAtPointer(chart, point, factor) {
    const extent = chart.getModel().getComponent("yAxis").axis.scale.getExtent();
    const logarithmic = this._yAxis === "log";
    const transform = (value) => logarithmic ? Math.log(Math.max(1, value)) : value;
    const inverse = (value) => logarithmic ? Math.exp(value) : value;
    // A refresh must not enlarge the minimum zoom span of a saved fixed window.
    const fullMax = this._yView?.full_max ?? Math.max(logarithmic ? 10 : 1,
      this._ensurePointCache().peak?.[2] || 0, extent[1]);
    const fullSpan = transform(fullMax);
    const anchor = transform(chart.convertFromPixel({ yAxisIndex: 0 }, point[1]));
    const next = this._anchoredWindow(transform(extent[0]), transform(extent[1]), anchor, factor,
      0, fullSpan, Math.min(fullSpan, Math.max(logarithmic ? 0 : 1, fullSpan * 0.01)));
    if (!next || !next.every(Number.isFinite)) return false;
    this._yZoom = Number(((next[1] - next[0]) / fullSpan).toFixed(6));
    this._yView = this._yZoom >= 1 ? null : { min: inverse(next[0]), max: inverse(next[1]), full_max: fullMax };
    this._updateZoomReadout();
    this._scheduleViewStatePersist();
    this._applyYZoom();
    return true;
  }

  _zoomXAtPointer(chart, point, factor) {
    const extent = chart.getModel().getComponent("xAxis").axis.scale.getExtent();
    const mode = this._readXAxisMode();
    const [minimum, maximum] = this._xRange(mode, true);
    const span = maximum - minimum;
    const anchor = chart.convertFromPixel({ xAxisIndex: 0 }, point[0]);
    const next = this._anchoredWindow(extent[0], extent[1], anchor, factor, minimum, maximum, span * this._zoomMinSpan() / 100);
    if (!next || !next.every(Number.isFinite)) return false;
    const before = { ...this._zoom };
    const domainChanged = mode === "energy" && !this._xFullDomain && minimum < 0;
    if (domainChanged) {
      this._xFullDomain = true;
      chart.setOption({ xAxis: { min: minimum, max: maximum } }, { silent: true });
    }
    chart.dispatchAction({ type: "dataZoom", dataZoomIndex: [0, 1],
      start: (next[0] - minimum) / span * 100, end: (next[1] - minimum) / span * 100 });
    if (domainChanged) {
      this._updateZoomReadout();
      this._scheduleViewStatePersist();
    }
    return domainChanged || Math.abs(before.start - this._zoom.start) > 0.0001 || Math.abs(before.end - this._zoom.end) > 0.0001;
  }

  _eventInsidePlot(chart, event) {
    return !this._detached && chart === this._chart && this._points().length > 0
      && Number.isFinite(event?.offsetX) && Number.isFinite(event?.offsetY)
      && chart.containPixel({ gridIndex: 0 }, [event.offsetX, event.offsetY]);
  }

  _bindChartEvents() {
    if (!this._chart || this._zoomHandler) return;
    const chart = this._chart;
    this._zoomHandler = (event) => this._onZoom(event);
    chart.on("datazoom", this._zoomHandler);
    this._chartEventSource = chart.getZr();
    // One zrender listener covers both empty plot background and series or
    // overlays. Do not also bind ECharts/native dblclick and fire twice.
    this._chartDoubleClickHandler = (event) => {
      if (this._eventInsidePlot(chart, event)) this._resetZoom();
    };
    this._chartWheelElement = this.shadowRoot.querySelector("#chart");
    this._chartWheelHandler = (native) => {
      if (this._detached || chart !== this._chart) return;
      const point = this._wheelPlotPosition(chart, native);
      if (!point || !this._eventInsidePlot(chart, { offsetX: point[0], offsetY: point[1] })) return;
      // ECharts' inside controller consumes wheel before checking its Ctrl
      // modifier. Handle both modifiers here using viewport client coordinates,
      // while leaving ordinary or clamped no-op browser scrolling intact.
      native.stopImmediatePropagation();
      if (!native.ctrlKey && !native.shiftKey) return;
      const delta = Number.isFinite(native.deltaY) ? -native.deltaY : native.wheelDelta;
      if (!Number.isFinite(delta) || delta === 0) return;
      const factor = delta > 0 ? 0.8 : 1.25;
      const changed = native.ctrlKey ? this._zoomXAtPointer(chart, point, factor) : this._zoomYAtPointer(chart, point, factor);
      if (changed) native.preventDefault?.(); // Ctrl also wins for Ctrl+Shift.
    };
    this._chartEventSource.on("dblclick", this._chartDoubleClickHandler);
    this._chartWheelElement.addEventListener("wheel", this._chartWheelHandler, { capture: true, passive: false });
  }

  _zoomChannelRange() {
    const points = this._points();
    if (!points.length) return [0, this._maxChannel()];
    const last = points.length - 1;
    const startIndex = clamp(Math.round((this._zoom.start / 100) * last), 0, last);
    const endIndex = clamp(Math.round((this._zoom.end / 100) * last), startIndex, last);
    return [points[startIndex][0], points[endIndex][0]];
  }

  _updateZoomReadout() {
    const readout = this.shadowRoot?.querySelector("#zoom-readout");
    const reset = this.shadowRoot?.querySelector("#reset-zoom");
    const [start, end] = this._zoomChannelRange();
    const text = `View CH ${start}–${end}`;
    const full = this._zoom.start <= 0.0001 && this._zoom.end >= 99.9999 && !this._xFullDomain && this._yZoom >= 1 && !this._yView;
    if (readout) {
      readout.textContent = text;
      readout.title = text;
      readout.setAttribute("aria-label", text);
    }
    if (reset) reset.disabled = full || !this._points().length;
  }

  _zoomOptions(colors, divider) {
    const range = this._clampZoom(this._zoom.start, this._zoom.end);
    this._zoom = range;
    let start = this._channelToAxisZoom(range.start);
    let end = this._channelToAxisZoom(range.end);
    // A saved all-negative window or non-monotonic calibration may have no
    // usable physical interval. Show the default domain without rewriting the
    // saved channel bounds (switching to channel mode still exposes them).
    if (end <= start) { start = 0; end = 100; }
    const common = {
      xAxisIndex: [0], filterMode: "none", start, end,
      minSpan: this._zoomMinSpan(), zoomLock: false,
    };
    return [
      { ...common, type: "inside", zoomOnMouseWheel: "ctrl", moveOnMouseMove: true, moveOnMouseWheel: false, throttle: 50 },
      {
        ...common, type: "slider", bottom: 6, height: 14, showDataShadow: false, brushSelect: false,
        borderColor: divider, fillerColor: colors.dataCursor, handleStyle: { color: colors.dataCursor },
        dataBackground: { lineStyle: { color: colors.curve, opacity: 0.35 }, areaStyle: { color: colors.curve, opacity: 0.08 } },
      },
    ];
  }

  _dataCursorChannel(channel) {
    const requested = clamp(Math.round(Number(channel) || 0), 0, this._maxChannel());
    const points = this._points();
    if (!points.length) return requested;
    const point = this._pointForChannel(requested);
    return point ? clamp(Math.round(point[0]), 0, this._maxChannel()) : requested;
  }

  _updateDataCursorUI() {
    const slider = this.shadowRoot?.querySelector("#data-cursor-slider");
    const channelNode = this.shadowRoot?.querySelector("#data-cursor-channel");
    const readout = this.shadowRoot?.querySelector("#data-cursor-readout");
    const channel = this._dataCursorChannel(this._cursor.data);
    this._cursor.data = channel;
    const point = this._pointForChannel(channel);
    const value = point ? `CH ${point[0]} · ${point[1].toFixed(2)} keV · ${point[2]}` : `Channel ${channel}`;
    if (slider) {
      slider.value = String(channel);
      slider.setAttribute("aria-valuemin", "0");
      slider.setAttribute("aria-valuemax", String(this._maxChannel()));
      slider.setAttribute("aria-valuenow", String(channel));
      slider.setAttribute("aria-valuetext", value);
    }
    if (channelNode) channelNode.textContent = `CH ${channel}`;
    if (readout) {
      readout.textContent = point ? `${point[1].toFixed(2)} keV · ${point[2].toLocaleString()} counts` : "No data";
      readout.title = value;
      readout.setAttribute("aria-label", value);
    }
  }

  _setCursorChannel(kind, channel) {
    const requested = clamp(Math.round(Number(channel) || 0), 0, this._maxChannel());
    if (kind === "roiStart") this._cursor.roiStart = Math.min(requested, this._cursor.roiEnd);
    else if (kind === "roiEnd") this._cursor.roiEnd = Math.max(requested, this._cursor.roiStart);
    else this._cursor.data = this._dataCursorChannel(requested);
    const startInput = this.shadowRoot.querySelector("#roi-start");
    const endInput = this.shadowRoot.querySelector("#roi-end");
    if (startInput) startInput.value = String(this._cursor.roiStart);
    if (endInput) endInput.value = String(this._cursor.roiEnd);
    this._updateRoiSliderUI();
    this._updateDataCursorUI();
    this._scheduleViewStatePersist();
    this._draw({ updateSummary: true, interactionOnly: true });
  }

  _syncRoiFromInputs(changed) {
    const startInput = this.shadowRoot.querySelector("#roi-start");
    const endInput = this.shadowRoot.querySelector("#roi-end");
    const startValue = Number(startInput?.value);
    const endValue = Number(endInput?.value);
    const maxChannel = this._maxChannel();
    const start = clamp(Math.round(Number.isFinite(startValue) ? startValue : 0), 0, maxChannel);
    const end = clamp(Math.round(Number.isFinite(endValue) ? endValue : maxChannel), 0, maxChannel);
    if (changed === "start") this._cursor.roiStart = Math.min(start, end);
    else this._cursor.roiEnd = Math.max(end, start);
    if (startInput) startInput.value = String(this._cursor.roiStart);
    if (endInput) endInput.value = String(this._cursor.roiEnd);
    this._updateRoiSliderUI();
    this._scheduleViewStatePersist();
    this._draw({ updateSummary: true, interactionOnly: true });
  }

  _syncAutoRefresh() {
    const interval = Number(this._config?.refresh_interval || 0);
    if (!interval || !this._hass || !this._entryId || !this.isConnected || this._busyState) {
      if (!interval || !this._hass || !this._entryId || !this.isConnected) this._stopAutoRefresh();
      return;
    }
    if (this._documentIsHidden()) {
      this._refreshAfterVisible = this._refreshAfterVisible || Boolean(this._refreshTimer);
      this._stopAutoRefresh();
      return;
    }
    if (this._refreshTimer && this._refreshPeriod !== interval) this._stopAutoRefresh();
    if (!this._refreshTimer) {
      this._refreshPeriod = interval;
      this._refreshTimer = window.setInterval(() => this._autoRefresh(), interval * 1000);
    }
  }

  _stopAutoRefresh() {
    if (this._refreshTimer) window.clearInterval(this._refreshTimer);
    this._refreshTimer = null;
    this._refreshPeriod = null;
  }

  async _autoRefresh() {
    if (this._documentIsHidden()) {
      this._refreshAfterVisible = true;
      this._stopAutoRefresh();
      return;
    }
    if (this._busyState || !this._entryId || !this._device || !this._data || this._detached) return;
    const history = this.shadowRoot.querySelector("#history");
    if (history && history.value !== "latest") return;
    await this._capture({ silent: true });
  }

  async _loadInitial() {
    if (this._detached || !this._entryId) return;
    const generation = this._entityGeneration;
    const entryId = this._entryId;
    try {
      const device = await this._call({ type: "kc761/info", entry_id: entryId });
      if (!this._requestIsCurrent(generation, entryId)) return;
      this._device = device;
      this._setDestructiveActionsEnabled(device.destructive_actions_enabled);
      const source = this.shadowRoot.querySelector("#source");
      source.replaceChildren();
      ["Gamma", "Neutron", "PIN"].forEach((name, index) => {
        if (!this._device.sensor_types[index]) return;
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = name;
        source.append(option);
      });
      const isInstalled = (value) => (
        Number.isInteger(value)
        && value >= 0
        && value <= 2
        && Boolean(this._device.sensor_types[value])
      );
      const currentSource = Number(this._data?.source);
      const selectedSource = Number(source.value);
      const history = this.shadowRoot.querySelector("#history");
      const latestSelected = !history || history.value === "latest";
      if (!this._sourceSelectionExplicit && latestSelected && isInstalled(currentSource)) {
        // The spectrum entity's latest snapshot may belong to the previous
        // channel (for example PIN) after a device replacement.  Omitting
        // source asks the read-only capture API to use the device's current
        // active sensor, then _capture mirrors the returned source into the
        // selector.  This avoids guessing from a stale/persisted snapshot.
        await this._capture({ silent: true, useActiveSensor: true });
      } else if (this._sourceSelectionExplicit && isInstalled(selectedSource)) {
        source.value = String(selectedSource);
      } else if (isInstalled(currentSource)) {
        source.value = String(currentSource);
      } else {
        const firstInstalled = [0, 1, 2].find((index) => isInstalled(index));
        source.value = firstInstalled === undefined ? "" : String(firstInstalled);
      }
      if (!this._requestIsCurrent(generation, entryId)) return;
      if (this._shouldLoadHistory()) await this._requestHistoryLoad();
      if (!this._requestIsCurrent(generation, entryId)) return;
      this._setStatus("");
      this._syncAutoRefresh();
    } catch (error) {
      diagnosticsRecordError("initial-load-failure", error);
      if (this._requestIsCurrent(generation, entryId)) this._setStatus(error.message || String(error), true);
    }
  }

  async _loadHistory(
    generation = this._entityGeneration,
    entryId = this._entryId,
    requestToken = this._historyRequestToken,
  ) {
    const history = await this._call({ type: "kc761/spectrum/list", entry_id: entryId });
    if (requestToken !== this._historyRequestToken || this._detached || generation !== this._entityGeneration || entryId !== this._entryId) return;
    this._history = history;
    this._historyLoaded = true;
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

  _shouldLoadHistory() {
    if (!this._rendered || !this._entryId || this._config?.show_controls === false) return false;
    const editorControls = this.shadowRoot.querySelector("#editor-controls");
    return Boolean(this._preview && editorControls && !editorControls.hidden);
  }

  _requestHistoryLoad({ force = false } = {}) {
    if (!this._entryId || (!force && this._historyLoaded)) return Promise.resolve();
    if (this._historyLoadPromise) return this._historyLoadPromise;
    const generation = this._entityGeneration;
    const entryId = this._entryId;
    const requestToken = this._historyRequestToken;
    const request = this._loadHistory(generation, entryId, requestToken).finally(() => {
      if (this._historyLoadPromise === request) this._historyLoadPromise = null;
    });
    this._historyLoadPromise = request;
    return request;
  }

  _handleHistoryError(event, error) {
    if (this._detached) return;
    diagnosticsRecordError(event, error);
    this._setStatus(error?.message || String(error), true);
  }

  _requestHistoryFromInteraction() {
    if (!this._entryId || this._detached) return;
    void this._requestHistoryLoad().catch((error) => this._handleHistoryError("history-load-failure", error));
  }

  async _selectHistory(snapshotId) {
    const generation = this._entityGeneration;
    const entryId = this._entryId;
    const requestToken = this._historyRequestToken;
    const selectionToken = ++this._historySelectionToken;
    try {
      if (snapshotId === "latest") {
        const state = this._hass?.states?.[this._config.entity];
        if (!state) throw new Error("Live spectrum is unavailable");
        this._setData(this._fromEntity(state), { viewMode: "live" });
      } else {
        if (!this._historyLoaded) await this._requestHistoryLoad();
        if (selectionToken !== this._historySelectionToken || requestToken !== this._historyRequestToken || this._detached || generation !== this._entityGeneration || entryId !== this._entryId) return;
        const data = await this._call({ type: "kc761/spectrum/get", entry_id: entryId, snapshot_id: snapshotId });
        if (selectionToken !== this._historySelectionToken || requestToken !== this._historyRequestToken || this._detached || generation !== this._entityGeneration || entryId !== this._entryId) return;
        this._setData({ ...data, snapshot_id: snapshotId }, { viewMode: `history:${snapshotId}` });
      }
      if (selectionToken !== this._historySelectionToken || requestToken !== this._historyRequestToken || this._detached || generation !== this._entityGeneration || entryId !== this._entryId) return;
      this.shadowRoot.querySelector("#source").value = String(this._data.source);
      this._updateSummary();
      this._draw();
      this._syncAutoRefresh();
    } catch (error) {
      this._handleHistoryError("history-select-failure", error);
    }
  }

  async _capture({ silent = false, useActiveSensor = false } = {}) {
    if (this._busyState || !this._entryId) return;
    const generation = this._entityGeneration;
    const entryId = this._entryId;
    await this._busy(async () => {
      const message = { type: "kc761/spectrum/capture", entry_id: entryId };
      if (!useActiveSensor) message.source = Number(this.shadowRoot.querySelector("#source").value);
      const data = await this._call(message);
      if (!this._requestIsCurrent(generation, entryId)) return;
      this._setData(data, { viewMode: "live" });
      const source = Number(data?.source);
      if (Number.isInteger(source) && source >= 0 && source <= 2) {
        this.shadowRoot.querySelector("#source").value = String(source);
      }
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
      if (!this._requestIsCurrent(generation, entryId)) return;
      if (this._historyLoaded) await this._requestHistoryLoad({ force: true });
    }, "Spectrum saved");
  }

  async _csv() {
    const generation = this._entityGeneration;
    const entryId = this._entryId;
    await this._busy(async () => {
      const selected = this.shadowRoot.querySelector("#history").value;
      const message = { type: "kc761/spectrum/csv", entry_id: this._entryId };
      if (selected !== "latest") message.snapshot_id = selected;
      const result = await this._call(message);
      if (!this._requestIsCurrent(generation, entryId)) return;
      const url = URL.createObjectURL(new Blob([result.content], { type: "text/csv;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = result.filename;
      link.click();
      URL.revokeObjectURL(url);
    }, "CSV exported");
  }

  async _clear(kind) {
    if (!this._destructiveActionsEnabled) {
      this._setStatus("Destructive actions are disabled in KC761 integration options", true);
      return;
    }
    const source = Number(this.shadowRoot.querySelector("#source").value);
    const channel = ["Gamma", "Neutron", "PIN"][source];
    const label = { spectrum: "spectrum and accumulation time", dose_curve: "dose-time curve", cumulative_dose: "cumulative dose and runtime" }[kind];
    const identity = `${this._device.model} / ${this._device.serial_number}`;
    if (!window.confirm(`Device: ${identity}\nChannel: ${channel}\n\nClear ${label}? This cannot be undone.`)) return;
    const generation = this._entityGeneration;
    const entryId = this._entryId;
    await this._busy(async () => {
      await this._call({ type: "kc761/clear", entry_id: entryId, source, kind });
      if (!this._requestIsCurrent(generation, entryId)) return;
      const data = await this._call({ type: "kc761/spectrum/capture", entry_id: entryId, source });
      if (!this._requestIsCurrent(generation, entryId)) return;
      this._setData(data, { viewMode: "live" });
      this.shadowRoot.querySelector("#history").value = "latest";
      this._updateSummary();
      this._draw();
    }, `${label} cleared`);
  }

  async _busy(operation, success) {
    if (this._busyState || this._detached) return;
    const generation = this._entityGeneration;
    const entryId = this._entryId;
    this._busyState = true;
    this._syncOperationUI();
    if (success) this._setStatus("Working…");
    try {
      await operation();
      if (this._requestIsCurrent(generation, entryId) && success) this._setStatus(success);
    } catch (error) {
      diagnosticsRecordError("operation-failure", error);
      if (this._requestIsCurrent(generation, entryId)) this._setStatus(error.message || String(error), true);
    } finally {
      if (this._requestIsCurrent(generation, entryId)) {
        this._busyState = false;
        this._syncOperationUI();
        this._syncAutoRefresh();
        if (this._refreshAfterVisible && !this._documentIsHidden()) this._scheduleVisibleRefresh();
      }
    }
  }

  _requestIsCurrent(generation, entryId) {
    return !this._detached && generation === this._entityGeneration && entryId === this._entryId;
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

  _ensurePointCache() {
    const data = this._data;
    const rawPoints = Array.isArray(data?.points) ? data.points : [];
    if (this._pointCache?.data === data && this._pointCache.rawPoints === rawPoints) return this._pointCache;
    const points = [];
    const channels = [];
    const energies = [];
    const counts = [];
    const channelIndex = new Map();
    let channelsSorted = true;
    let peak = null;
    let prefixTotal = 0;
    const prefix = [0];
    for (const point of rawPoints) {
      if (!Array.isArray(point) || point.length < 3) continue;
      const channel = Number(point[0]);
      const energy = Number(point[1]);
      const count = Number(point[2]);
      if (!Number.isFinite(channel) || !Number.isFinite(energy) || !Number.isFinite(count)) continue;
      const normalized = [channel, energy, count];
      const index = points.length;
      points.push(normalized);
      channels.push(channel);
      energies.push(energy);
      counts.push(count);
      if (!channelIndex.has(channel)) channelIndex.set(channel, index);
      if (index > 0 && channel < channels[index - 1]) channelsSorted = false;
      if (!peak || count > peak[2]) peak = normalized;
      prefixTotal += count;
      prefix.push(prefixTotal);
    }
    const totalInput = Number(data?.total_counts);
    const cache = {
      data,
      rawPoints,
      points,
      channels,
      energies,
      counts,
      channelIndex,
      channelsSorted,
      prefix,
      peak,
      totalCounts: Number.isFinite(totalInput) ? totalInput : prefixTotal,
      xValues: new Map(),
      xSorted: new Map(),
      xRanges: new Map(),
      lineData: new Map(),
    };
    this._pointCache = cache;
    this._summaryCache = null;
    return cache;
  }

  _points() {
    return this._ensurePointCache().points;
  }

  _readXAxisMode() {
    const selected = this.shadowRoot?.querySelector("#xaxis")?.value;
    if (selected === "channel" || selected === "energy") this._xAxisMode = selected;
    return this._xAxisMode === "channel" ? "channel" : "energy";
  }

  _xValue(point, xMode = this._xAxisMode) {
    return xMode === "channel" ? point[0] : point[1];
  }

  _xValues(cache, xMode) {
    if (!cache.xValues.has(xMode)) {
      const values = xMode === "channel" ? cache.channels : cache.energies;
      cache.xValues.set(xMode, values);
    }
    return cache.xValues.get(xMode);
  }

  _xValuesSorted(cache, xMode) {
    if (!cache.xSorted.has(xMode)) {
      const values = this._xValues(cache, xMode);
      cache.xSorted.set(xMode, values.every((value, index) => index === 0 || value >= values[index - 1]));
    }
    return cache.xSorted.get(xMode);
  }

  _xRange(xMode = this._readXAxisMode(), fullDomain = this._xFullDomain) {
    const cache = this._ensurePointCache();
    if (!cache.points.length) return [0, 1];
    const key = `${xMode}:${fullDomain === true}`;
    if (!cache.xRanges.has(key)) {
      const values = this._xValues(cache, xMode);
      let minimum = values[0];
      let maximum = values[0];
      for (let index = 1; index < values.length; index += 1) {
        minimum = Math.min(minimum, values[index]);
        maximum = Math.max(maximum, values[index]);
      }
      // Default display clips negative extrapolation; manual Ctrl zoom uses
      // the complete domain. Neither changes calibration, counts or exports.
      cache.xRanges.set(key, xMode === "energy"
        ? [fullDomain ? Math.min(0, minimum) : 0, Math.max(1, maximum)]
        : [minimum, maximum === minimum ? minimum + 1 : maximum]);
    }
    return cache.xRanges.get(key);
  }

  _channelToAxisZoom(percent) {
    const cache = this._ensurePointCache();
    if (percent <= 0 || percent >= 100 || cache.points.length < 2) return clamp(percent, 0, 100);
    const mode = this._readXAxisMode();
    const values = this._xValues(cache, mode);
    const index = (percent / 100) * (values.length - 1);
    const lower = Math.floor(index);
    const value = values[lower] + (values[Math.min(lower + 1, values.length - 1)] - values[lower]) * (index - lower);
    const [minimum, maximum] = this._xRange(mode);
    return clamp(((value - minimum) / (maximum - minimum)) * 100, 0, 100);
  }

  _axisToChannelZoom(percent) {
    const cache = this._ensurePointCache();
    // Endpoints remain full-range markers in the existing channel-based
    // persistence schema, even though energy mode clips the negative domain.
    if (percent <= 0 || percent >= 100 || cache.points.length < 2) return clamp(percent, 0, 100);
    const mode = this._readXAxisMode();
    const values = this._xValues(cache, mode);
    const [minimum, maximum] = this._xRange(mode);
    const target = minimum + (percent / 100) * (maximum - minimum);
    const sorted = this._xValuesSorted(cache, mode);
    let index = this._nearestIndex(values, target, sorted);
    if (sorted) {
      const lower = values[index] > target ? Math.max(0, index - 1) : index;
      const upper = Math.min(values.length - 1, lower + 1);
      const span = values[upper] - values[lower];
      index = lower + (span > 0 ? clamp((target - values[lower]) / span, 0, 1) : 0);
    }
    return (index / (values.length - 1)) * 100;
  }

  _nearestIndex(values, target, sorted = false) {
    if (!values.length) return -1;
    if (!sorted) {
      let nearest = 0;
      for (let index = 1; index < values.length; index += 1) {
        if (Math.abs(values[index] - target) < Math.abs(values[nearest] - target)) nearest = index;
      }
      return nearest;
    }
    let low = 0;
    let high = values.length - 1;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (values[middle] < target) low = middle + 1;
      else high = middle;
    }
    if (low === 0) return 0;
    const previous = low - 1;
    return Math.abs(values[low] - target) < Math.abs(values[previous] - target) ? low : previous;
  }

  _nearestPointByX(xValue, xMode = this._readXAxisMode()) {
    const cache = this._ensurePointCache();
    const values = this._xValues(cache, xMode);
    const index = this._nearestIndex(values, xValue, this._xValuesSorted(cache, xMode));
    return index < 0 ? null : cache.points[index];
  }

  _pointIndexForChannel(channel) {
    const cache = this._ensurePointCache();
    if (!cache.points.length) return 0;
    const exact = cache.channelIndex.get(Number(channel));
    if (exact !== undefined) return exact;
    return this._nearestIndex(cache.channels, Number(channel), cache.channelsSorted);
  }

  _pointForChannel(channel) {
    const cache = this._ensurePointCache();
    if (!cache.points.length) return null;
    const index = this._pointIndexForChannel(channel);
    return index < 0 ? null : cache.points[index];
  }

  _roiTotal(start, end) {
    const cache = this._ensurePointCache();
    if (!cache.points.length) return 0;
    if (!cache.channelsSorted) {
      let total = 0;
      cache.points.forEach((point) => {
        if (point[0] >= start && point[0] <= end) total += point[2];
      });
      return total;
    }
    const lowerBound = (target, upper = false) => {
      let low = 0;
      let high = cache.channels.length;
      while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (cache.channels[middle] < target || (upper && cache.channels[middle] === target)) low = middle + 1;
        else high = middle;
      }
      return low;
    };
    const first = lowerBound(start);
    const after = lowerBound(end, true);
    return cache.prefix[after] - cache.prefix[first];
  }

  _roi() {
    const maxChannel = this._maxChannel();
    const start = clamp(this._cursor.roiStart, 0, maxChannel);
    const end = clamp(this._cursor.roiEnd, start, maxChannel);
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
    const cache = this._ensurePointCache();
    const points = cache.points;
    const data = this._data;
    if (!data || !points.length) return;
    const [start, end] = this._roi();
    const peak = cache.peak || points[0];
    const roiTotal = this._roiTotal(start, end);
    const cursorPoint = this._pointForChannel(this._cursor.data) || points[0];
    if (this._summaryCache !== cache) {
      this._setSummaryValue("#acquired", new Date(data.acquired_at).toLocaleString());
      this._setSummaryValue("#runtime", data.accumulation_seconds == null ? "Unknown" : `${data.accumulation_seconds} s`);
      this._setSummaryValue("#total", cache.totalCounts.toLocaleString());
      this._setSummaryValue("#peak", `CH ${peak[0]} · ${peak[1].toFixed(2)} keV · ${peak[2]}`);
      this._setSummaryValue("#spectrum-status", this._spectrumStatusText(data));
      this._summaryCache = cache;
    }
    this._setSummaryValue("#roi-total", roiTotal.toLocaleString());
    this._setSummaryValue("#cursor-value", `CH ${cursorPoint[0]} · ${cursorPoint[1].toFixed(2)} keV · ${cursorPoint[2]}`);
  }

  _spectrumStatusText(data = this._data) {
    const pointCount = Array.isArray(data?.points) ? data.points.length : Number(data?.point_count) || 0;
    const confidence = String(data?.length_confidence || "legacy");
    const basis = String(data?.completion_basis || "legacy");
    if (confidence === "inferred") return `${pointCount} channels · inferred (${basis})`;
    if (confidence === "legacy") return `${pointCount} channels · legacy archive`;
    return `${pointCount} channels · observed (${basis})`;
  }

  _lineData(cache, xMode, logarithmic) {
    const key = `${xMode}:${logarithmic ? "log" : "linear"}`;
    if (!cache.lineData.has(key)) {
      const values = this._xValues(cache, xMode);
      cache.lineData.set(key, cache.points.map((point, index) => [values[index], logarithmic ? Math.max(1, point[2]) : point[2]]));
    }
    return cache.lineData.get(key);
  }

  _seriesOverlays(cache, xMode, logarithmic) {
    const points = cache.points;
    if (!points.length) return { markArea: { data: [] }, markLine: { data: [] }, markPoint: { data: [] } };
    const [roiStart, roiEnd] = this._roi();
    const roiStartPoint = this._pointForChannel(roiStart) || points[0];
    const roiEndPoint = this._pointForChannel(roiEnd) || points[points.length - 1];
    const cursorPoint = this._pointForChannel(this._cursor.data) || points[0];
    const peak = cache.peak || points[0];
    const xFor = (point) => this._xValue(point, xMode);
    const cursorNearPeak = Math.abs(peak[0] - cursorPoint[0]) <= 16;
    const foreground = this._themeValue("--primary-text-color", "#ddd");
    const colors = this._colors || COLOR_FALLBACKS;
    return {
      markArea: {
        silent: true,
        itemStyle: { color: colors.roiStart, opacity: 0.16 },
        data: [[{ xAxis: xFor(roiStartPoint) }, { xAxis: xFor(roiEndPoint) }]],
      },
      markLine: {
        silent: true,
        symbol: ["none", "none"],
        label: { show: false },
        data: [{
          xAxis: xFor(cursorPoint),
          lineStyle: { color: colors.dataCursor, type: "solid", width: 1 },
        }],
      },
      markPoint: this._config?.show_peak === false ? { data: [] } : {
        symbol: "pin",
        symbolSize: 34,
        itemStyle: { color: colors.peak },
        label: {
          color: foreground,
          fontSize: 10,
          position: "top",
          distance: cursorNearPeak ? 28 : 8,
          offset: cursorNearPeak ? [0, -8] : [0, 0],
        },
        data: [{
          coord: [xFor(peak), logarithmic ? Math.max(1, peak[2]) : peak[2]],
          value: peak[2],
          name: `Peak CH ${peak[0]}`,
        }],
      },
    };
  }

  _interactionOption() {
    const cache = this._ensurePointCache();
    const xMode = this._readXAxisMode();
    const logarithmic = this._yAxis === "log";
    return {
      series: [{
        id: "spectrum",
        ...this._seriesOverlays(cache, xMode, logarithmic),
      }],
    };
  }

  _option() {
    const cache = this._ensurePointCache();
    const points = cache.points;
    const xMode = this._readXAxisMode();
    const logarithmic = this._yAxis === "log";
    const lineData = this._lineData(cache, xMode, logarithmic);
    const [xMinimum, xMaximum] = this._xRange(xMode);
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
      // Keep the ECharts slider inside the existing chart footprint.  The
      // extra bottom reserve is internal to the chart and must not change the
      // card's outer Masonry/Sections height.
      grid: { top: 30, left: 1, right: 1, bottom: 58, containLabel: true },
      dataZoom: this._zoomOptions(colors, divider),
      tooltip: {
        trigger: "axis", renderMode: "html", backgroundColor: cardBackground, borderColor: divider, borderWidth: 1,
        textStyle: { color: foreground }, axisPointer: { type: "line", snap: true, lineStyle: { color: colors.dataCursor } },
        formatter: (items) => {
          const item = items?.[0];
          if (!item) return "";
          const point = this._nearestPointByX(Number(item.value?.[0]), xMode);
          return point ? `CH ${point[0]}<br/>${point[1].toFixed(2)} keV<br/>${point[2].toLocaleString()} counts` : "";
        },
      },
      xAxis: {
        type: "value", name: xMode === "energy" ? "Energy (keV)" : "Channel address", nameLocation: "middle", nameGap: 38,
        min: xMinimum, max: xMaximum, boundaryGap: false,
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
        ...this._yAxisBounds(), axisLabel: { color: secondary, hideOverlap: true, showMinLabel: true, showMaxLabel: true, margin: 8, formatter: formatCountTick }, axisLine: { lineStyle: { color: divider } },
        splitLine: { lineStyle: { color: divider, opacity: 0.45 } },
      },
      series: [{
        id: "spectrum", name: "Spectrum", type: "line", showSymbol: false, sampling: "lttb", data: lineData,
        lineStyle: { color: colors.curve, width: 1.5 }, itemStyle: { color: colors.curve },
        ...this._seriesOverlays(cache, xMode, logarithmic),
      }],
    };
  }

  _draw({ updateSummary = false, interactionOnly = false } = {}) {
    if (!this._data || !this._ensurePointCache().points.length || !this.shadowRoot.querySelector("#chart")) return;
    this._drawPending = true;
    if (!interactionOnly || !this._chart) this._fullDrawPending = true;
    this._summaryUpdatePending = this._summaryUpdatePending || updateSummary;
    if (this._drawRaf !== null) return;
    const request = globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 0));
    this._drawRaf = request(() => {
      this._drawRaf = null;
      if (this._detached || !this._drawPending) return;
      this._drawPending = false;
      const fullDraw = this._fullDrawPending;
      this._fullDrawPending = false;
      if (this._summaryUpdatePending) {
        this._summaryUpdatePending = false;
        this._updateSummary();
      }
      const token = ++this._drawToken;
      diagnosticsRecord("draw-start", { generation: token });
      this._ensureChart().then(() => {
        if (token !== this._drawToken || !this._chart || this._detached) return;
        if (fullDraw) {
          this._chart.setOption(this._option(), { notMerge: true, lazyUpdate: false });
        } else {
          this._chart.setOption(
            this._interactionOption(),
            { notMerge: false, lazyUpdate: true, silent: true },
          );
        }
      }).catch((error) => {
        diagnosticsRecordError("draw-failure", error);
        this._setStatus(`Chart load failed: ${error.message || error}`, true);
      });
    });
  }

  _cancelDraw() {
    if (this._drawRaf === null) return;
    const cancel = globalThis.cancelAnimationFrame || ((id) => clearTimeout(id));
    cancel(this._drawRaf);
    this._drawRaf = null;
    this._drawPending = false;
    this._fullDrawPending = false;
    this._summaryUpdatePending = false;
  }

  _disposeChart() {
    this._chartGeneration = (this._chartGeneration || 0) + 1;
    this._chartInitPromise = null;
    this._cancelDraw();
    if (this._resizeObserver) this._resizeObserver.disconnect();
    this._resizeObserver = null;
    this._cancelChartResize();
    if (this._chart && this._zoomHandler) this._chart.off("datazoom", this._zoomHandler);
    this._zoomHandler = null;
    if (this._chartEventSource) {
      this._chartEventSource.off("dblclick", this._chartDoubleClickHandler);
    }
    if (this._chartWheelElement && this._chartWheelHandler) this._chartWheelElement.removeEventListener("wheel", this._chartWheelHandler, true);
    this._chartEventSource = null;
    this._chartDoubleClickHandler = null;
    this._chartWheelHandler = null;
    this._chartWheelElement = null;
    if (this._chart) this._chart.dispose();
    this._chart = null;
  }

  async _ensureChart() {
    if (this._chart) return this._chart;
    if (this._detached) return null;
    if (this._chartInitPromise) return this._chartInitPromise;
    const generation = this._chartGeneration;
    const pending = this._initializeChart(generation);
    this._chartInitPromise = pending;
    try {
      return await pending;
    } finally {
      if (this._chartInitPromise === pending) this._chartInitPromise = null;
    }
  }

  async _initializeChart(generation) {
    if (this._chart) return this._chart;
    if (this._detached || generation !== this._chartGeneration) return null;
    if (!this._echartsPromise) {
      diagnosticsRecord("dynamic-import-start");
      this._echartsPromise = import(ECHARTS_URL);
    }
    let module;
    try {
      module = await this._echartsPromise;
      diagnosticsRecord("dynamic-import-success");
    } catch (error) {
      diagnosticsRecordError("dynamic-import-failure", error);
      throw error;
    }
    if (this._detached || generation !== this._chartGeneration) return null;
    if (this._chart) return this._chart;
    const chartElement = this.shadowRoot.querySelector("#chart");
    if (!chartElement) return null;
    this._echarts = module.default || module;
    this._chart = this._echarts.init(chartElement, null, { renderer: "svg", useDirtyRect: false });
    this._bindChartEvents();
    this._resizeObserver = new ResizeObserver(() => this._scheduleChartResize());
    this._resizeObserver.observe(chartElement);
    return this._chart;
  }

  _scheduleChartResize() {
    if (this._detached || !this._chart || this._chartResizeRaf !== null) return;
    const request = globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 0));
    this._chartResizeRaf = request(() => {
      this._chartResizeRaf = null;
      if (!this._detached && this._chart) this._chart.resize();
    });
  }

  _cancelChartResize() {
    if (this._chartResizeRaf === null) return;
    const cancel = globalThis.cancelAnimationFrame || ((id) => clearTimeout(id));
    cancel(this._chartResizeRaf);
    this._chartResizeRaf = null;
  }

  _scheduleThemeRefresh() {
    if (this._themeRefreshRaf !== null) return;
    const request = globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 0));
    this._themeRefreshRaf = request(() => {
      this._themeRefreshRaf = null;
      if (this._detached) return;
      if (this._refreshColors() && this._chart) this._draw();
    });
  }

  _cancelThemeRefresh() {
    if (this._themeRefreshRaf === null) return;
    const cancel = globalThis.cancelAnimationFrame || ((id) => clearTimeout(id));
    cancel(this._themeRefreshRaf);
    this._themeRefreshRaf = null;
  }

  _refreshColors() {
    const wasReady = this._colorsReady;
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
    const themeValues = {};
    ["--primary-text-color", "--secondary-text-color", "--divider-color", "--card-background-color"].forEach((name) => {
      const value = computed?.getPropertyValue(name)?.trim();
      if (value) themeValues[name] = value;
    });
    const customColors = {
      "--kc761-spectrum-curve": colors.curve,
      "--kc761-spectrum-roi-start": colors.roiStart,
      "--kc761-spectrum-roi-end": colors.roiEnd,
      "--kc761-spectrum-peak": colors.peak,
      "--kc761-spectrum-data": colors.dataCursor,
    };
    const colorChanged = Object.keys(colors).some((name) => this._colors?.[name] !== colors[name]);
    const themeChanged = ["--primary-text-color", "--secondary-text-color", "--divider-color", "--card-background-color"]
      .some((name) => (this._themeValues?.[name] || "") !== (themeValues[name] || ""));
    let styleChanged = false;
    Object.entries(customColors).forEach(([name, value]) => {
      if (this.style?.getPropertyValue?.(name) === value) return;
      styleChanged = true;
      this.style?.setProperty?.(name, value);
    });
    this._colors = colors;
    this._themeValues = themeValues;
    this._colorsReady = true;
    return !wasReady || colorChanged || themeChanged || styleChanged;
  }

  _themeValue(name, fallback) {
    return this._themeValues?.[name] || fallback;
  }

}

// Keep registration in one idempotent function.  HA can import the module more
// than once during a frontend rebuild, and using globalThis avoids depending
// on a module's lexical global bindings while the app is bootstrapping.
function registerKC761SpectrumCard() {
  const registry = globalThis.customElements;
  const alreadyDefined = Boolean(registry?.get?.("kc761-spectrum-card"));
  diagnosticsRecord("registration-attempt", { already_defined: alreadyDefined });
  if (registry && !alreadyDefined) {
    try {
      registry.define("kc761-spectrum-card", KC761SpectrumCard);
      diagnosticsRecord("registration-defined", { defined: true });
    } catch (error) {
      diagnosticsRecordError("registration-failure", error);
      throw error;
    }
  } else if (alreadyDefined) {
    diagnosticsRecord("registration-already-defined", { already_defined: true });
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
    preview: true,
    getEntitySuggestion: (hass, entityId) => KC761SpectrumCard.getEntitySuggestion(hass, entityId),
  };
  const existing = cards.findIndex((card) => card.type === "kc761-spectrum-card");
  if (existing < 0) cards.push(metadata);
  else cards[existing] = { ...cards[existing], ...metadata };
}

const diagnosticRegistry = diagnosticsListenerRegistry();
if (diagnosticRegistry) diagnosticRegistry.moduleEvaluated = true;
try {
  if (globalThis.window) globalThis.window[DIAGNOSTICS_MODULE_EVALUATED_KEY] = true;
} catch (_error) {
  // A restricted window must not affect card registration or diagnostics.
}

// Reinstall session-enabled global diagnostics before registration so a
// transient Lovelace error card or module failure is captured even when no
// card instance has survived the current refresh yet.
diagnosticsEnsureGlobal();
diagnosticsRecord("module-evaluated");
  registerKC761SpectrumCard();
scheduleKC761ErrorCardRebuild();
