const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

async function testJourney() {
  const elements = new Map();
  const mapEvents = new Map();
  const sources = new Map();
  const layers = new Map();
  const requests = [];
  let styleReady = true;
  let routeFits = 0;
  let focused;

  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      dataset: {}, value: "", hidden: false, attributes: {},
      addEventListener(name, handler) { this[name] = handler; },
      setAttribute(name, value) { this.attributes[name] = value; },
      set textContent(value) { this.innerHTML = value; },
      focus() { focused = id; },
    });
    return elements.get(id);
  }

  const map = {
    on(name, handler) { mapEvents.set(name, handler); },
    getStyle: () => styleReady ? {version: 8} : undefined,
    getSource: id => sources.get(id),
    addSource(id, {data}) { sources.set(id, {data, setData(value) { this.data = value; }}); },
    getLayer: id => layers.get(id),
    addLayer(layer) { layers.set(layer.id, layer); },
    fitBounds() { routeFits++; },
  };
  element("journeyMode").value = "walk";
  element("maxWalk").value = "1000";
  vm.runInNewContext(fs.readFileSync(require.resolve("../assets/journey.js"), "utf8"), {
    window: {AirWatchCity: {getMap: () => map, showPage() {}}},
    document: {getElementById: element, querySelector: () => null},
    AbortController,
    fetch(_url, {body, signal}) {
      return new Promise(resolve => requests.push({payload: JSON.parse(body), signal, resolve}));
    },
  });

  function choose(button, lng) {
    const control = element(button);
    control.click();
    assert.equal(control.attributes["aria-pressed"], "true");
    mapEvents.get("click")({lngLat: {lng, lat: 51.5}});
    assert.equal(control.attributes["aria-pressed"], "false");
  }
  function complete({payload, resolve}) {
    const {mode, start, end} = payload;
    resolve({ok: true, json: async () => ({mode, alternatives: [], alerts: [], legs: [{
      kind: mode, distance_m: 850, duration_s: 600,
      geometry: {type: "LineString", coordinates: [start, end]},
    }]})});
  }
  const plan = element("planJourney");
  const result = element("journeyResult");
  const environment = element("journeyEnvironment");
  const routePanel = element("journeyRoutePanel");
  const environmentPanel = element("journeyEnvironmentPanel");
  const routeTab = element("journeyRouteTab");
  const environmentTab = element("journeyEnvironmentTab");
  const lines = () => sources.get("journey-legs").data.features.length;
  choose("pickStart", -.13);
  choose("pickEnd", -.12);
  const clearedPlan = plan.click();
  assert.equal(plan.disabled, true);
  element("clearJourney").click();
  assert.equal(requests[0].signal.aborted, true);
  complete(requests[0]);
  await clearedPlan;
  assert.equal(plan.disabled, true);
  assert.match(result.innerHTML, /依次选择起点和终点/);
  assert.match(environment.innerHTML, /规划路线后查看沿途空气质量/);
  assert.equal(sources.get("journey-points").data.features.length, 0);
  assert.equal(lines(), 0);

  choose("pickStart", -.13);
  choose("pickEnd", -.12);
  const outdatedPlan = plan.click();
  element("journeyMode").value = "personal_bike";
  element("journeyMode").change();
  assert.equal(requests[1].signal.aborted, true);
  const latestPlan = plan.click();
  complete(requests[1]);
  await outdatedPlan;
  assert.equal(plan.disabled, true);
  assert.match(result.innerHTML, /正在检查/);
  assert.match(environment.innerHTML, /正在规划路线并估算沿途空气质量/);
  routePanel.scrollTop = environmentPanel.scrollTop = 100;
  complete(requests[2]);
  await latestPlan;
  assert.equal(plan.disabled, false);
  assert.equal(routePanel.scrollTop, 0);
  assert.equal(environmentPanel.scrollTop, 0);
  assert.match(result.innerHTML, /预计用时/);
  assert.match(result.innerHTML, /850 米/);
  assert.match(result.innerHTML, /个人自行车骑行/);
  assert.equal(lines(), 1);
  assert.match(environment.innerHTML, /路线已生成。.*请重启本地服务后重新规划/);

  const fitsBeforeTabs = routeFits;
  environmentTab.click();
  assert.equal(environmentTab.attributes["aria-selected"], "true");
  assert.equal(routeTab.tabIndex, -1);
  assert.equal(environmentTab.tabIndex, 0);
  assert.equal(routePanel.hidden, true);
  assert.equal(environmentPanel.hidden, false);
  function pressTabKey(tab, key, destination) {
    let prevented = false;
    tab.keydown({key, preventDefault() { prevented = true; }});
    assert.equal(prevented, true);
    assert.equal(focused, destination);
    assert.equal(element(destination).attributes["aria-selected"], "true");
  }
  pressTabKey(environmentTab, "ArrowRight", "journeyRouteTab");
  pressTabKey(routeTab, "ArrowLeft", "journeyEnvironmentTab");
  pressTabKey(environmentTab, "Home", "journeyRouteTab");
  pressTabKey(routeTab, "End", "journeyEnvironmentTab");
  assert.equal(routeFits, fitsBeforeTabs);
  assert.equal(requests.length, 3);
  assert.equal(lines(), 1);

  element("swapJourney").click();
  assert.equal(element("startLabel").innerHTML, "-0.12000, 51.50000");
  assert.match(result.innerHTML, /请重新规划路线/);
  assert.equal(lines(), 0);

  element("journeyMode").value = "shared_bike";
  element("journeyMode").change();
  assert.equal(element("maxWalkLabel").hidden, false);
  const sharedPlan = plan.click();
  element("journeyPage").hidden = true;
  const previousFits = routeFits;
  sources.clear();
  layers.clear();
  styleReady = false;
  const {payload, resolve} = requests[3];
  const observedAt = "2026-11-22T06:00:00Z";
  const estimate = (value, unit, station_count, nearest_m) =>
    ({status: "estimated", value, unit, station_count, nearest_m, latest_at: observedAt});
  const missing = {status: "insufficient", station_count: 0, nearest_m: null};
  const routeAir = {
    status: "available", total_distance_m: 850,
    summary: {PM25: {
      laqn: {min: 0, max: 0, unit: "ug/m3", coverage: 0.25, peak_segment_index: 0},
      breathe: {min: 4, max: 4.4, unit: "ug/m3", coverage: 1, peak_segment_index: 1},
    }},
    segments: [
      {leg_index: 0, from_m: 0, to_m: 212.5, readings: {
        PM25: {laqn: estimate(0, "ug/m3", 2, 315), breathe: estimate(4, "ug/m3", 14, 614)},
        PM10: {laqn: missing},
      }},
      {leg_index: 0, from_m: 212.5, to_m: 850, readings: {
        PM25: {laqn: missing, breathe: estimate(4.4, "ug/m3", 6, 339)},
      }},
    ],
  };
  resolve({ok: true, json: async () => ({
    mode: "shared_bike", message: "自行车快照缺失或超过 15 分钟", alternatives: [], alerts: [],
    legs: [{kind: "walk", distance_m: 850, duration_s: 600,
      geometry: {type: "LineString", coordinates: [payload.start, payload.end]}}],
    context: {route_air: routeAir, daqi: {status: "fresh", index: 2, band: "低", observed_at: observedAt}},
  })});
  await sharedPlan;
  assert.match(result.innerHTML, /当前显示步行备选路线。自行车快照缺失/);
  assert.match(result.innerHTML, /<details class="journey-leg" open>/);
  assert.doesNotMatch(result.innerHTML, /沿途浓度估计|DAQI/);
  const [table] = environment.innerHTML.match(/<table class="journey-air-table">[\s\S]*?<\/table>/);
  assert.match(table, /<th scope="col">LAQN<\/th><th scope="col">Breathe London<\/th>/);
  assert.match(table, /PM₂.₅<\/th><td>[\s\S]*?0 µg\/m³[\s\S]*?覆盖 25%[\s\S]*?最高路段 0 米–213 米/);
  assert.match(table, /4–4.4 µg\/m³[\s\S]*?覆盖 100%[\s\S]*?最高路段 213 米–850 米/);
  assert.match(table, /PM₁₀<\/th><td>.*站点不足.*覆盖 0%/);
  assert.match(environment.innerHTML, /<summary>按距离查看各段/);
  assert.match(environment.innerHTML, /<summary>213 米–850 米 · 步行/);
  assert.match(environment.innerHTML, /14 站 · 最近 614 米 · 最新 2026-11-22T06:00:00Z/);
  assert.match(environment.innerHTML, /大伦敦官方 DAQI[\s\S]*?2 · 低[\s\S]*?查看 Defra 分人群指导/);
  assert.equal(environmentPanel.hidden, false);
  assert.equal(routeFits, previousFits);
  assert.equal(layers.size, 0);
  styleReady = true;
  mapEvents.get("style.load")();
  assert.equal(lines(), 1);
  assert.equal(layers.size, 3);
  for (const layer of layers.values()) assert.equal(layer.layout.visibility, "none");

  const alternative = result.click({target: {closest: () => ({dataset: {pickup: "p2", dropoff: "d2"}})}});
  assert.equal(requests[4].payload.pickup_id, "p2");
  assert.equal(requests[4].payload.dropoff_id, "d2");
  assert.match(environment.innerHTML, /正在规划路线/);
  assert.doesNotMatch(environment.innerHTML, /4.4/);
  requests[4].resolve({ok: true, json: async () => ({legs: [], routing_error: "路线服务不可用",
    context: {route_air: {status: "unavailable"}, daqi: {status: "stale"}}})});
  await alternative;
  assert.match(result.innerHTML, /路线服务不可用/);
  assert.match(environment.innerHTML, /沿途空气质量估算需要有效的道路路线/);
  assert.match(environment.innerHTML, /大伦敦官方 DAQI[\s\S]*?缺失或过期/);
  assert.equal(environmentPanel.hidden, false);
  assert.equal(lines(), 0);
}

testJourney().then(() => console.log("PASS journey cancels outdated results, preserves current requests and clears the map"))
  .catch(error => { console.error(error); process.exitCode = 1; });
