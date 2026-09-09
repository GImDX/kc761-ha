import assert from "node:assert/strict";
import * as echarts from "../custom_components/kc761/frontend/echarts.esm.min.js";

// The bundled engine performs its real axis, dataZoom, overlay and SVG layout.
// Only font measurement uses ECharts' built-in no-canvas fallback; no browser,
// native canvas dependency, network or real HA/device is involved.
export function verifyPhysicalEnergyRenderer(Card, entity = "sensor.real_renderer_contract") {
  echarts.setPlatformAPI({ createCanvas: () => undefined });
  const coefficients = [
    [9.99999968265523e-21, -0.00326848798431456, 2.33491539955139, -227.773223876953],
    [4.85204232347769e-8, 0.000228616394451819, 1.45147240161896, -175.682342529297],
    [9.99999968265523e-21, 0.000295038451440632, 1.6252658367157, -359.972961425781],
  ];
  // Previously saved Gamma factory calibration, with synthetic counts only.
  const points = Array.from({ length: 2048 }, (_, channel) => {
    const [a, b, c, d] = coefficients[channel < 158 ? 0 : channel <= 955 ? 1 : 2];
    return [channel, ((a * channel + b) * channel + c) * channel + d, channel + 1];
  });
  const original = JSON.stringify(points);
  const data = { source: 0, channel: 0, point_count: 2048, snapshot_id: "latest", points };
  const card = new Card();
  card.setConfig({ entity, diagnostics: false });
  card._setData(data);
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 900, height: 430 });
  card._chart = chart;
  chart.on("datazoom", (event) => card._onZoom(event));
  const extent = () => chart.getModel().getComponent("xAxis").axis.scale.getExtent();
  const roiCounts = () => card._roiTotal(...card._roi());
  const assertPhysical = () => {
    assert.equal(extent()[0], 0, "real engine's final value scale starts at zero");
    const series = chart.getModel().getSeriesByIndex(0);
    const rect = chart.getModel().getComponent("grid").coordinateSystem.getRect();
    const svg = chart.renderToSVGString();
    assert.ok(svg.includes("<clipPath"), "SVG spectrum has an actual plot clip path");
    assert.ok(![...svg.matchAll(/<text[^>]*>(.*?)<\/text>/g)].some((match) => /^-\d/.test(match[1])), "actual rendered axis labels contain no negative energies");
    const negative = points.filter((point) => point[1] < 0);
    assert.ok(negative.length > 0);
    for (const point of negative) {
      const pixel = chart.convertToPixel({ seriesIndex: 0 }, [point[1], point[2]]);
      assert.ok(pixel[0] < rect.x, "negative calibrated bins are outside the displayed plot rectangle");
      assert.equal(chart.containPixel({ gridIndex: 0 }, pixel), false);
    }
    assert.equal(series.getRawData().count(), 2048, "clipping does not remove statistical samples before display downsampling");
    return svg;
  };
  try {
    chart.setOption(card._option(), { notMerge: true });
    const defaultExtent = [...extent()];
    const initialCounts = roiCounts();
    const svg = assertPhysical();
    assert.deepEqual(card._roi(), [0, 2047]);
    // The reported UI combination is valid: ROI and chart viewport are
    // independent. Neither a markArea nor a negative cursor can expand xAxis.
    card._setCursorChannel("roiStart", 108);
    const selectedCounts = roiCounts();
    chart.setOption(card._interactionOption(), { notMerge: false, lazyUpdate: false, silent: true });
    assertPhysical();
    assert.deepEqual(card._roi(), [108, 2047]);
    assert.deepEqual(card._zoomChannelRange(), [0, 2047]);
    assert.equal(initialCounts - selectedCounts, points.slice(0, 108).reduce((sum, point) => sum + point[2], 0));
    // Reuse exactly the existing channel-coordinate persistence schema.
    card._zoom = card._zoomFromChannelRange(400, 1200);
    card._scheduleViewStatePersist({ flush: true });
    const restored = new Card();
    restored.setConfig({ entity, diagnostics: false });
    restored._setData(data);
    chart.setOption(restored._option(), { notMerge: true });
    // ECharts quantizes the value window to its axis precision (1 keV here).
    // The actual visible boundary must still resolve to the saved channel.
    assert.equal(restored._nearestPointByX(extent()[0], "energy")[0], 400);
    assert.equal(restored._nearestPointByX(extent()[1], "energy")[0], 1200);
    assert.deepEqual(restored._roi(), [108, 2047]);
    assert.equal(restored._roiTotal(...restored._roi()), selectedCounts);
    card._resetZoom();
    assertPhysical();
    assert.equal(roiCounts(), selectedCounts, "real dataZoom reset does not change statistics");
    card.shadowRoot.querySelector("#xaxis").value = "channel";
    chart.setOption(card._option(), { notMerge: true });
    assert.deepEqual(extent(), [0, 2047], "channel mode retains the complete actual axis");
    assert.equal(roiCounts(), selectedCounts);
    card.shadowRoot.querySelector("#xaxis").value = "energy";
    chart.setOption(card._option(), { notMerge: true });
    card._resetZoom();
    assertPhysical();
    assert.equal(JSON.stringify(points), original, "original calibration and raw counts stay unchanged");
    return { engine: echarts.version, default_extent: defaultExtent, default_roi: [0, 2047],
      selected_roi: card._roi(), initial_counts: initialCounts, selected_counts: selectedCounts,
      reset_extent: [...extent()], negative_bins: points.filter((point) => point[1] < 0).length, svg };
  } finally {
    chart.dispose();
    card._chart = null;
  }
}

export function verifyYAxisLabels(Card) {
  echarts.setPlatformAPI({ createCanvas: () => undefined });
  const card = new Card();
  card.setConfig({ entity: "sensor.y_tick_renderer", diagnostics: false });
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 900, height: 430 });
  card._chart = chart;
  try {
    for (const mode of ["linear", "log"]) {
      card._setYAxis(mode);
      for (const peak of [2048, 1e15]) {
        const points = Array.from({ length: 2048 }, (_, channel) => [channel, channel, Math.round((channel + 1) * peak / 2048)]);
        const unchanged = JSON.stringify(points);
        card._setData({ source: 0, channel: 0, snapshot_id: "latest", point_count: 2048, points });
        card._yView = { min: 182.44321, max: peak === 2048 ? 1828.9245689912868 : 9.876543219e14, full_max: peak };
        chart.setOption(card._option(), { notMerge: true });
        const formatter = card._option().yAxis.axisLabel.formatter;
        for (const value of [0, 1, 123, 1828.9245689912868, 1e15, Number.MAX_VALUE, Number.MIN_VALUE,
          -Number.MAX_VALUE, -Number.MIN_VALUE]) {
          const label = formatter(value);
          assert.ok(label.length > 0 && label.length <= 9, `bounded numeric Y label for ${value}: ${label}`);
          assert.ok(!/Infinity|NaN|,/.test(label));
        }
        assert.equal(formatter(0), "0");
        assert.equal(formatter(1), "1");
        assert.equal(formatter(123), "123");
        assert.equal(formatter(1828.9245689912868), "1829");
        const axis = chart.getModel().getComponent("yAxis").axis;
        assert.deepEqual(axis.scale.getExtent(), [card._yView.min, card._yView.max], "formatting never rounds actual axis bounds");
        const labels = axis.getViewLabels().map((item) => String(item.formattedLabel));
        assert.ok(labels.length > 1 && labels.every((label) => label.length <= 9));
        const svg = chart.renderToSVGString();
        assert.ok(svg.includes(`>${formatter(card._yView.max)}</text>`), "real SVG maximum label uses the bounded formatter");
        assert.ok(chart.getModel().getComponent("grid").coordinateSystem.getRect().x < 100,
          "bounded labels do not consume the plot with a long left gutter");
        assert.equal(JSON.stringify(card._points()), unchanged, "tick formatting leaves raw counts unchanged");
      }
    }
  } finally {
    chart.dispose();
    card._chart = null;
  }
}

export function verifyChartGestures(Card) {
  echarts.setPlatformAPI({ createCanvas: () => undefined });
  const card = new Card();
  card.setConfig({ entity: "sensor.gesture_renderer", diagnostics: false });
  const points = Array.from({ length: 2048 }, (_, channel) => [channel, channel * 2 - 227.77, channel + 1]);
  const data = { source: 0, channel: 0, snapshot_id: "latest", point_count: 2048, points };
  const original = JSON.stringify(data);
  card._setData(data);
  card._cursor = { roiStart: 108, roiEnd: 1700, data: 800 };
  const cursor = { ...card._cursor };
  const counts = card._roiTotal(...card._roi());
  const viewportBounds = { left: 137, top: 83, width: 1800, height: 645 };
  const create = () => {
    card._chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 900, height: 430 });
    card._chart.getZr().painter.getViewportRoot = () => ({ getBoundingClientRect: () => viewportBounds });
    card._bindChartEvents();
    card._chart.setOption(card._option(), { notMerge: true });
    return card._chart;
  };
  let chart = create();
  const packet = (native = {}, target = undefined, inside = true) => {
    const rect = chart.getModel().getComponent("grid").coordinateSystem.getRect();
    return { offsetX: inside ? rect.x + rect.width * (native.fractionX ?? 0.5) : 0,
      offsetY: inside ? rect.y + rect.height * (native.fractionY ?? 0.5) : 0, target,
      wheelDelta: native.deltaY < 0 ? 1 : native.deltaY > 0 ? -1 : 0,
      event: { preventDefault() {}, stopPropagation() {}, ...native } };
  };
  const wheel = (native, inside = true) => {
    const event = packet(native, undefined, inside);
    let stopped = false;
    const originalPrevent = event.event.preventDefault;
    event.event.defaultPrevented = false;
    event.event.preventDefault = () => { event.event.defaultPrevented = true; originalPrevent(); };
    event.event.stopImmediatePropagation = () => { stopped = true; };
    event.event.clientX = viewportBounds.left + event.offsetX * viewportBounds.width / chart.getWidth();
    event.event.clientY = viewportBounds.top + event.offsetY * viewportBounds.height / chart.getHeight();
    event.event.offsetX = 2; // Deliberately wrong SVG child-local coordinates.
    event.event.offsetY = 3;
    card.shadowRoot.querySelector("#chart").emit("wheel", event.event);
    if (!stopped) chart.getZr().trigger("mousewheel", event);
    return event.event.defaultPrevented;
  };
  let prevented = 0;
  const shiftUp = { shiftKey: true, ctrlKey: false, deltaY: -100, preventDefault: () => { prevented += 1; } };
  let resets = 0;
  const reset = card._resetZoom.bind(card);
  card._resetZoom = () => { resets += 1; reset(); };
  const checkUnchanged = () => {
    assert.deepEqual(card._cursor, cursor);
    assert.equal(card._roiTotal(...card._roi()), counts);
    assert.equal(JSON.stringify(data), original);
  };
  try {
    for (const mode of ["linear", "log"]) {
      card._setYAxis(mode);
      card._resetZoomState();
      chart.setOption(card._option(), { notMerge: true });
      const xBefore = [...chart.getModel().getComponent("xAxis").axis.scale.getExtent()];
      const yBefore = [...chart.getModel().getComponent("yAxis").axis.scale.getExtent()];
      for (const fractionY of [0.18, 0.77]) {
        const before = packet({ fractionY });
        const value = chart.convertFromPixel({ yAxisIndex: 0 }, before.offsetY);
        assert.equal(wheel({ ...shiftUp, fractionY }), true, "recognized Y zoom prevents page scrolling");
        const afterPixel = chart.convertToPixel({ yAxisIndex: 0 }, value);
        assert.ok(Math.abs(afterPixel - before.offsetY) < 0.01, `${mode} pointer anchor stays at its original Y pixel`);
        const yAfter = chart.getModel().getComponent("yAxis").axis.scale.getExtent();
        assert.ok(yAfter[0] > yBefore[0] && yAfter[1] < yBefore[1], "Y window zooms inward at both ends");
      }
      assert.equal(card._yZoom, 0.64);
      assert.deepEqual(chart.getModel().getComponent("xAxis").axis.scale.getExtent(), xBefore);
      assert.equal(card.shadowRoot.querySelector("#reset-zoom").disabled, false, "Y-only zoom enables Reset");
      checkUnchanged();
      for (let step = 0; step < 3; step += 1) wheel({ ...shiftUp, deltaY: 100, fractionY: 0.3 });
      assert.equal(card._yZoom, 1);
      assert.equal(card._yView, null);
      assert.deepEqual(chart.getModel().getComponent("yAxis").axis.scale.getExtent(), yBefore, "zoom out restores automatic Y extent");
    }
    for (const fractionX of [0.21, 0.83]) {
      const before = packet({ fractionX });
      const value = chart.convertFromPixel({ xAxisIndex: 0 }, before.offsetX);
      wheel({ ...shiftUp, ctrlKey: true, fractionX });
      assert.ok(Math.abs(chart.convertToPixel({ xAxisIndex: 0 }, value) - before.offsetX) < 0.2,
        "Ctrl uses actual viewport pixels despite padding, CSS scale and SVG child offsets");
    }
    assert.equal(card._yZoom, 1, "Ctrl+Shift goes to X, never Y");
    assert.ok(card._zoom.end - card._zoom.start < 100, "real ECharts still processes Ctrl wheel for X");
    reset();
    const beforeIgnored = prevented;
    for (const native of [{ deltaY: -100 }, { ...shiftUp, deltaY: 0 }]) assert.equal(wheel(native), false);
    assert.equal(wheel(shiftUp, false), false);
    assert.equal(wheel({ ...shiftUp, deltaY: 100 }), false);
    assert.equal(card._yZoom, 1);
    assert.equal(prevented, beforeIgnored, "ordinary/outside/no-op wheel must not prevent page scrolling");
    assert.equal(card._xFullDomain, false);
    assert.equal(wheel({ ...shiftUp, ctrlKey: true, deltaY: 100 }), true,
      "default physical full window can expand to the actual CH0 energy");
    const fullX = () => chart.getModel().getComponent("xAxis").axis.scale.getExtent();
    assert.deepEqual(fullX(), [points[0][1], points.at(-1)[1]]);
    assert.deepEqual(card._zoomChannelRange(), [0, 2047]);
    assert.equal(card.shadowRoot.querySelector("#reset-zoom").disabled, false);
    assert.equal(wheel({ ...shiftUp, ctrlKey: true, deltaY: 100 }), false, "actual full X zoom-out does not trap scrolling");
    card._scheduleViewStatePersist({ flush: true });
    const manual = new Card();
    manual.setConfig({ entity: "sensor.gesture_renderer", diagnostics: false });
    manual._setData(data);
    assert.equal(manual._xFullDomain, true);
    chart.setOption(manual._option(), { notMerge: true });
    assert.deepEqual(fullX(), [points[0][1], points.at(-1)[1]], "manual full domain survives reload");
    card._setData({ ...data });
    chart.setOption(card._option(), { notMerge: true });
    assert.deepEqual(fullX(), [points[0][1], points.at(-1)[1]], "manual full domain survives data refresh");
    checkUnchanged();
    reset();
    assert.equal(fullX()[0], 0);
    assert.equal(card._viewStatePayload().x_full_domain, false);
    for (const mode of ["channel", "energy"]) {
      card.shadowRoot.querySelector("#xaxis").value = mode;
      const positive = { ...data, points: points.map(([ch, _energy, count]) => [ch, ch * 2, count]) };
      card._setData(positive);
      chart.setOption(card._option(), { notMerge: true });
      assert.equal(wheel({ ...shiftUp, ctrlKey: true, deltaY: 100 }), false);
      assert.equal(card._xFullDomain, false, "non-negative/channel default full is unchanged");
    }
    card._setData(data);
    chart.setOption(card._option(), { notMerge: true });
    assert.equal(card._option().dataZoom[0].zoomOnMouseWheel, "ctrl", "existing X wheel modifier stays unchanged");
    for (let step = 0; step < 40; step += 1) wheel({ ...shiftUp, ctrlKey: true, fractionX: 0.25 });
    assert.equal(wheel({ ...shiftUp, ctrlKey: true, fractionX: 0.25 }), false, "minimum X span is a no-op");
    reset();
    wheel(shiftUp);
    card._setZoomRange(20, 60);
    chart.setOption(card._option(), { notMerge: true });
    chart.getZr().trigger("click", packet());
    chart.getZr().trigger("dblclick", packet({}, undefined, false));
    for (const selector of ["#source", "#history", "#roi-start", "#roi-end", "#configure"]) {
      card.shadowRoot.querySelector(selector).emit("dblclick");
    }
    assert.equal(resets, 0, "single click, chart margins and controls do not reset");
    assert.equal(card._yZoom, 0.8);
    card._bindChartEvents();
    card._bindChartEvents();
    chart.getZr().trigger("dblclick", packet());
    assert.equal(resets, 1, "plot background double-click fires exactly once after rebind/rerender requests");
    assert.deepEqual(card._zoom, { start: 0, end: 100 });
    assert.equal(card._yZoom, 1);
    assert.equal(chart.getModel().getComponent("xAxis").axis.scale.getExtent()[0], 0);
    checkUnchanged();
    const doubleClickState = card._viewStatePayload();
    card._setZoomRange(20, 60);
    wheel(shiftUp);
    card.shadowRoot.querySelector("#reset-zoom").click();
    assert.equal(resets, 2);
    assert.deepEqual(card._viewStatePayload(), doubleClickState, "button and double-click reset/persist exactly the same state");
    assert.equal(doubleClickState.y_zoom, 1);
    // Target-bearing zrender packets cover the line/overlays with the same
    // single listener used by background packets.
    card._setZoomRange(20, 60);
    chart.getZr().trigger("dblclick", packet({}, {}));
    assert.equal(resets, 3);
    wheel(shiftUp);
    card._scheduleViewStatePersist({ flush: true });
    const savedYView = { ...card._yView };
    card._setData({ ...data, points: points.map(([ch, energy, count]) => [ch, energy, count + 1]) });
    assert.equal(card._yZoom, 0.8, "same-spectrum refresh retains Y zoom ratio");
    assert.deepEqual(card._yView, savedYView, "same-spectrum refresh retains the exact manual value window");
    chart.setOption(card._option(), { notMerge: true });
    assert.deepEqual(chart.getModel().getComponent("yAxis").axis.scale.getExtent(), [savedYView.min, savedYView.max]);
    card._setData({ ...data, points: points.map(([ch, energy, count]) => [ch, energy, count * 1e6]) });
    chart.setOption(card._option(), { notMerge: true });
    assert.deepEqual(card._yView, savedYView, "a much higher new peak does not move the fixed window");
    const priorSpan = Math.log(savedYView.max) - Math.log(savedYView.min);
    wheel({ ...shiftUp, fractionY: 0.25 });
    assert.ok(Math.log(card._yView.max) - Math.log(card._yView.min) < priorSpan,
      "zoom-in after a peak increase still zooms in, rather than enforcing a new larger minimum span");
    card._yView = { ...savedYView };
    card._yZoom = 0.8;
    card._setData(data);
    const reloaded = new Card();
    reloaded.setConfig({ entity: "sensor.gesture_renderer", diagnostics: false });
    reloaded._setData(data);
    assert.equal(reloaded._yZoom, 0.8, "fresh card restores optional Y zoom");
    assert.deepEqual(reloaded._yView, savedYView, "fresh card restores pointer-anchored bounds");
    reloaded._setData({ ...data, source: 2 });
    assert.equal(reloaded._yZoom, 1, "different source does not inherit Y zoom");
    assert.equal(reloaded._yView, null);
    reloaded._setData(data);
    assert.equal(reloaded._yZoom, 0.8);
    const legacy = { ...card._viewStatePayload() };
    delete legacy.x_full_domain;
    delete legacy.y_view;
    reloaded._readViewState = () => legacy;
    reloaded._restoreViewState();
    assert.equal(reloaded._xFullDomain, false, "old records retain the zero-keV default domain");
    assert.equal(reloaded._yZoom, 0.8, "old scalar Y zoom keeps its original baseline semantics");
    assert.equal(reloaded._yView, null);
    delete legacy.y_zoom;
    reloaded._readViewState = () => legacy;
    reloaded._restoreViewState();
    assert.equal(reloaded._yZoom, 1, "old records without Y field remain full height");
    assert.deepEqual(reloaded._zoomChannelRange(), card._zoomChannelRange(), "legacy X zoom stays compatible");
    for (const invalid of [{ min: -1, max: 2, full_max: 3 }, { min: 2, max: 1, full_max: 3 },
      { min: 1, max: 2, full_max: 1 }, { min: 1, max: Infinity, full_max: Infinity },
      { min: "1", max: 2, full_max: 3 }]) {
      reloaded._readViewState = () => ({ ...legacy, y_view: invalid });
      reloaded._restoreViewState();
      assert.equal(reloaded._yView, null, "invalid optional Y bounds safely use legacy/default state");
    }
    for (let step = 0; step < 30; step += 1) wheel(shiftUp);
    assert.equal(card._yZoom, 0.01);
    const atLimit = prevented;
    wheel(shiftUp);
    assert.equal(prevented, atLimit);
    const oldSource = chart.getZr();
    const oldDoubleClick = card._chartDoubleClickHandler;
    const oldWheel = card._chartWheelHandler;
    const oldPacket = packet(shiftUp);
    const removals = [];
    const oldOff = oldSource.off;
    oldSource.off = function (name, handler) { removals.push([name, handler]); return oldOff.call(this, name, handler); };
    card.disconnectedCallback();
    assert.ok(removals.some(([name, handler]) => name === "dblclick" && handler === oldDoubleClick));
    assert.equal(card.shadowRoot.querySelector("#chart").listenerCount("wheel"), 0, "capture wheel listener removed on disconnect");
    assert.equal(card._chartEventSource, null);
    oldDoubleClick(oldPacket);
    oldWheel({ ...shiftUp, clientX: oldPacket.offsetX, clientY: oldPacket.offsetY });
    assert.equal(resets, 3, "stale chart callbacks cannot mutate a detached card");
    card.connectedCallback();
    chart = create();
    chart.getZr().trigger("dblclick", packet());
    assert.equal(resets, 4, "reconnected chart binds exactly one fresh reset handler");
    assert.equal(card._yZoom, 1);
    checkUnchanged();
  } finally {
    card._disposeChart();
  }
}
