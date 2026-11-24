const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { recentWindow } = require("../assets/live.js");
const source = fs.readFileSync(require.resolve("../assets/city.js"), "utf8");

function testAnalysisMarkup() {
  const html = fs.readFileSync(require.resolve("../index.html"), "utf8");
  const start = html.indexOf('<section id="cityAnalysis"');
  const end = html.indexOf('<div id="tablesPage"');
  assert.ok(start >= 0 && end > start);
  const analysis = html.slice(start, end);
  const tabsAt = analysis.indexOf('class="analysis-tabs"');
  const controlsAt = analysis.indexOf('class="panel analysis-selection"');
  // The module row is the first row; its parameter row follows it.
  assert.ok(tabsAt >= 0 && controlsAt > tabsAt, "analysis module tabs must precede the parameter row");
  for (const section of ["series", "heatmap", "coverage", "weather", "bike", "borough"])
    assert.match(analysis, new RegExp(`data-analysis-tab="${section}"`));
  for (const control of ["source", "metric", "compare", "interval", "weather"])
    assert.match(analysis, new RegExp(`data-analysis-control="${control}"`));
  assert.match(analysis, /data-analysis-tab="series"[^>]*aria-pressed="true"/);
  assert.match(analysis, /data-analysis-control="weather"[^>]*hidden/);
  const bikeTitle = analysis.slice(analysis.indexOf('class="panel-title bike-analysis-title"'), analysis.indexOf('class="bike-analysis-legend"'));
  assert.match(bikeTitle, /id="bikeStation" aria-label="自行车站"/);
  assert.match(bikeTitle, /id="bikeApply"/);
  // Borough coverage is a map plus a ranking list, with an accessible fallback list.
  assert.match(analysis, /id="boroughMap"/);
  assert.match(analysis, /id="boroughCoverage"/);
  assert.match(analysis, /id="boroughLegend"/);
  assert.match(analysis, /id="boroughViewOnMap"/);
  const boroughPanel = analysis.slice(analysis.indexOf('data-analysis-section="borough"'));
  assert.match(boroughPanel, /id="boroughMap"[\s\S]*id="boroughCoverage"/);
  console.log("PASS analysis module tabs and bicycle map toolbar");
}

function testOverviewKpiPlaceholders() {
  const html = fs.readFileSync(require.resolve("../index.html"), "utf8");
  const start = html.indexOf('<div id="cityKpis"');
  const end = html.indexOf('<div class="city-grid">');
  assert.ok(start >= 0 && end > start, "the overview must keep the KPI card container");
  const kpis = html.slice(start, end);
  assert.match(kpis, /aria-busy="true"/);
  assert.equal((kpis.match(/class="city-kpi"/g) || []).length, 3);
  for (const title of ["有值空气监测点", "自行车资源", "道路事件"])
    assert.match(kpis, new RegExp(`<h2>${title}</h2>`));
  // The air card uses the same two-row matrix as the loaded state, so the
  // placeholder row is exactly as tall as the rendered one.
  assert.match(kpis, /<table class="city-kpi-air"/);
  assert.match(kpis, /<th scope="col">LAQN<\/th><th scope="col">Breathe London<\/th>/);
  assert.equal((kpis.match(/<td><strong>—<\/strong><\/td>/g) || []).length, 4);
  assert.equal((kpis.match(/<strong>—<\/strong>/g) || []).length, 8);
  assert.equal((kpis.match(/class="city-kpi-foot"/g) || []).length, 3);
  console.log("PASS overview KPI placeholders reserve card height before data arrives");
}

function testRichSelectShell() {
  const html = fs.readFileSync(require.resolve("../index.html"), "utf8");
  // The custom select shell is part of the HTML so the first paint already
  // looks like the final control. If it were built only by city.js, browsers
  // painted the native <select> first and its full-size digits/native arrow
  // visibly shrank when the enhancement replaced it on every refresh.
  const selects = [...html.matchAll(/<select[^>]*data-rich-select[^>]*>/g)].map(m => m.index);
  assert.equal(selects.length, 3);
  for (const at of selects) {
    const before = html.slice(Math.max(0, at - 200), at);
    assert.match(before, /<span class="rich-select">[^<]*$/, "rich select must be pre-rendered around the native select");
    const tail = html.slice(at, at + 2000);
    const button = tail.match(/<button[^>]*class="rich-select-button"[^>]*>/);
    assert.ok(button, "pre-rendered shell must include the visible button");
    assert.match(button[0], /aria-hidden="true"/, "pre-rendered button must stay inert for assistive tech");
    assert.match(button[0], /tabindex="-1"/, "pre-rendered button must not steal focus from the native select");
    assert.match(tail.slice(button.index), /<span class="rich-select-value">/, "pre-rendered button needs a value label");
    assert.match(tail.slice(button.index), /<span class="rich-select-arrow"/, "pre-rendered button needs the chevron");
  }
  const source = fs.readFileSync(require.resolve("../assets/city.js"), "utf8");
  assert.match(source, /button\.removeAttribute\("aria-hidden"\)/,
    "city.js must activate the pre-rendered button when it wires the listbox");
  assert.match(source, /button\.removeAttribute\("tabindex"\)/);
  console.log("PASS rich select shell is pre-rendered instead of replacing the native control on load");
}

function testAttributionCardMarkup() {
  const html = fs.readFileSync(require.resolve("../index.html"), "utf8");
  const template = html.slice(html.indexOf('<template id="mapAttributionTemplate">'), html.indexOf("</template>", html.indexOf('<template id="mapAttributionTemplate">')));
  // The basemap credit is static: MapLibre previously injected the style's
  // version after load, which changed the card height while it was open.
  assert.match(template, /class="map-basemap-credit"/);
  assert.ok(!template.includes("map-basemap-live"));
  assert.ok(!template.includes("map-basemap-fallback"));
  const source = fs.readFileSync(require.resolve("../assets/city.js"), "utf8");
  assert.match(source, /function collapseAttribution\(/, "the attribution control must collapse before the first paint");
  assert.ok(!source.includes("collapseAttributionAfterLoad"), "the old five-second auto-expand (and its height flash) must stay removed");
  console.log("PASS attribution card uses a static basemap credit and starts collapsed");
}

async function testMapFailure(failure) {
  const elements = new Map();
  const requests = [];
  const mapQueries = [];
  const events = {};
  let answer;
  let now = Date.parse("2026-11-22T10:37:49.500Z");
  const values = {
    timezone: "Europe/London", cityMode: "latest", cityMetric: "PM25",
  };
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: values[id] || "", hidden: false, textContent: "",
      addEventListener(name, handler) { this[name] = handler; }, classList: {toggle() {}},
      setAttribute(name, value) { this[name] = value; }, getAttribute(name) { return this[name]; }, removeAttribute() {},
      querySelectorAll() { return []; }, focus() { this.focused = true; },
    });
    return elements.get(id);
  }
  const tabs = ["overview", "analysis", "tables"].map(page => ({
    dataset: {page}, classList: {toggle() {}},
    setAttribute() {}, removeAttribute() {},
    addEventListener(name, handler) { this.click = handler; },
  }));
  const analysisTabs = ["series", "heatmap", "coverage", "weather", "bike", "borough"].map(section => ({
    dataset: {analysisTab: section}, hidden: false, attributes: {},
    classList: {toggle() {}},
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
    addEventListener(name, handler) { this[name] = handler; },
  }));
  const analysisControls = ["source", "metric", "compare", "interval", "weather"].map(control => ({
    dataset: {analysisControl: control}, hidden: false,
  }));
  const library = failure === "missing-library" ? null : {
    Map: function () {
      if (failure === "webgl-failure") throw new Error("WebGL unavailable");
      return {addControl() {}, on(name, handler) { events[name] = handler; }, resize() {}};
    },
    NavigationControl: function () {},
    AttributionControl: function () {},
  };
  // Mirror the browser formatter from assets/live.js so timestamp assertions
  // follow the selectable display timezone like the real page.
  const browserTime = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return String(value);
    const zoneName = element("timezone").value;
    const stamp = new Intl.DateTimeFormat("zh-CN", { timeZone: zoneName, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
    const name = new Intl.DateTimeFormat("en-GB", { timeZone: zoneName, timeZoneName: "short" }).formatToParts(date).find(p => p.type === "timeZoneName").value;
    return stamp + " " + name;
  };
  const context = {
    window: {
      AirWatchInsights: require("../assets/insights.js"),
      AirWatchData: {
        api(path, params) {
          requests.push(path);
          if (path === "/api/map") mapQueries.push(params);
          if (answer) return answer(path, params);
          return new Promise(() => {});
        },
        time: browserTime,
        calendarDate(date) { return date.toISOString().slice(0, 10); },
        recentWindow() { return recentWindow(now); },
      },
      maplibregl: library,
      addEventListener() {},
    },
    maplibregl: library,
    document: {getElementById: element, querySelectorAll: selector =>
      selector === "[data-page]" ? tabs :
      selector === "[data-analysis-tab]" ? analysisTabs :
      selector === "[data-analysis-control]" ? analysisControls : []},
    location: {protocol: "http:"},
    requestAnimationFrame: handler => handler(),
    console, Date, Intl, Map, Set, Promise,
  };
  const instrumented = source.replace(/\}\)\(\);\s*$/, "window.bikeTest = {S, refreshCity, showAnalysisSection, normalizeBoroughName, boroughCoverageModel, boroughBubbleSize, drawBikes, finishOverviewPick, heatmap, summaries, refreshOverviewWeather, repaintOverviewWeather, setWeatherMessage, renderLocalAir, toggleAirReferences, loadNearby};})();");
  vm.runInNewContext(instrumented, context);
  if (failure === "network-error") events.error();
  assert.match(element("mapNotice").textContent, /未加载|无法初始化|暂不可用/);
  assert.equal(element("mapNotice").hidden, false);
  assert.deepEqual(requests, ["/api/map"]);
  tabs[2].click();
  assert.equal(element("tablesPage").hidden, false);
  assert.equal(element("sourceFooter").hidden, false);
  tabs[0].click();
  assert.equal(element("sourceFooter").hidden, true);
  if (failure === "missing-library") {
    const initial = mapQueries[0];
    assert.equal(Date.parse(initial.end) - Date.parse(initial.start), 48 * 3600000);
    assert.equal(initial.period, "900");
    assert.equal(initial.source, undefined);
    assert.equal(initial.cursor, undefined);
    assert.equal(element("replayControls").hidden, true);
    const mode = element("cityMode"), slider = element("replayHour");
    now += 60000;
    mode.value = "history";
    mode.change();
    const replay = mapQueries.at(-1);
    assert.equal(element("replayControls").hidden, false);
    assert.equal(replay.end, recentWindow(now).end);
    assert.equal(replay.cursor, "2026-11-22T09:59:59.000Z");
    assert.equal(slider.max, 47);
    now += 7200000;
    slider.value = slider.max;
    slider.change();
    assert.equal(mapQueries.at(-1).end, replay.end);
    assert.equal(mapQueries.at(-1).cursor, "2026-11-22T10:38:48.000Z");
    element("cityMetric").value = "NO2";
    element("cityMetric").change();
    assert.equal(mapQueries.at(-1).metric, "NO2");
    assert.equal(mapQueries.at(-1).cursor, "2026-11-22T10:38:48.000Z");
    assert.equal(element("analysisMetric").value, "PM25");
    mode.value = "latest";
    mode.change();
    assert.equal(mapQueries.at(-1).end, recentWindow(now).end);
    assert.equal(mapQueries.at(-1).cursor, undefined);
    assert.equal(element("replayControls").hidden, true);

    // The analysis metric is independent of the map metric, and narrows to the
    // pollutants the chosen source actually reports (Breathe London: PM2.5, NO2).
    const mapRequests = mapQueries.length;
    element("analysisMetric").value = "PM10";
    element("analysisMetric").change();
    assert.equal(mapQueries.length, mapRequests);
    assert.equal(element("cityMetric").value, "NO2");
    element("analysisSource").value = "breathe";
    element("analysisSource").change();
    assert.equal(element("analysisMetric").innerHTML,
      '<option value="PM25">PM2.5</option><option value="NO2">NO2</option>');
    assert.equal(element("analysisMetric").value, "PM25");
    element("analysisMetric").value = "NO2";
    element("analysisSource").value = "laqn";
    element("analysisSource").change();
    assert.equal(element("analysisMetric").innerHTML,
      '<option value="PM25">PM2.5</option><option value="PM10">PM10</option>' +
      '<option value="NO2">NO2</option><option value="O3">O3</option>');
    assert.equal(element("analysisMetric").value, "NO2");
    assert.equal(mapQueries.length, mapRequests);

    // The module row decides which controls the second row exposes.
    const {showAnalysisSection} = context.window.bikeTest;
    const visibleControls = () => analysisControls
      .filter(node => !node.hidden).map(node => node.dataset.analysisControl);
    const pressed = section => analysisTabs
      .find(node => node.dataset.analysisTab === section).attributes["aria-pressed"];
    showAnalysisSection("bike");
    assert.deepEqual(visibleControls(), []);
    assert.equal(element("analysisSelection").hidden, true);
    assert.equal(pressed("bike"), "true");
    assert.equal(pressed("series"), "false");
    showAnalysisSection("weather");
    assert.equal(element("analysisSelection").hidden, false);
    assert.deepEqual(visibleControls(), ["source", "metric", "compare", "interval", "weather"]);
    assert.equal(element("compareStationsLabel").textContent, "污染站点（单选）");
    showAnalysisSection("heatmap");
    assert.deepEqual(visibleControls(), ["source", "metric", "compare"]);
    assert.equal(element("compareStationsLabel").textContent, "站点（单选）");
    showAnalysisSection("coverage");
    assert.deepEqual(visibleControls(), ["source", "metric", "compare"]);
    assert.equal(element("compareStationsLabel").textContent, "站点（单选）");
    showAnalysisSection("borough");
    assert.deepEqual(visibleControls(), ["source", "metric"]);
    showAnalysisSection("series");
    assert.deepEqual(visibleControls(), ["source", "metric", "compare", "interval"]);
    assert.equal(element("compareStationsLabel").textContent, "对比站点（最多 4 站）");
    showAnalysisSection("unknown-module");
    assert.equal(context.window.bikeTest.S.analysisSection, "series");
    assert.deepEqual(visibleControls(), ["source", "metric", "compare", "interval"]);

    // Single-station modules turn the shared comparison list into a radio group.
    const stationInputs = ["A", "B", "C"].map(name => ({
      value: name, checked: false, disabled: false, dataset: {name},
    }));
    element("compareStations").querySelectorAll = selector =>
      selector === 'input[type="checkbox"]' ? stationInputs : [];
    for (const input of stationInputs) input.checked = true;
    showAnalysisSection("heatmap", {reload: false});
    assert.deepEqual(stationInputs.map(input => input.checked), [true, false, false]);
    assert.equal(element("compareStationsSummary").textContent, "A");
    stationInputs[1].checked = true;
    element("compareStations").change({target: stationInputs[1]});
    assert.deepEqual(stationInputs.map(input => input.checked), [false, true, false]);
    assert.equal(element("compareStationsSummary").textContent, "B");
    // Clicking the selected station again keeps exactly one selected.
    stationInputs[1].checked = false;
    element("compareStations").change({target: stationInputs[1]});
    assert.deepEqual(stationInputs.map(input => input.checked), [false, true, false]);
    showAnalysisSection("weather", {reload: false});
    assert.equal(element("compareStationsLabel").textContent, "污染站点（单选）");
    assert.deepEqual(stationInputs.map(input => input.checked), [false, true, false]);
    // The series module still supports multi-station comparison.
    showAnalysisSection("series", {reload: false});
    stationInputs[0].checked = true;
    element("compareStations").change({target: stationInputs[0]});
    assert.deepEqual(stationInputs.map(input => input.checked), [true, true, false]);
    assert.equal(element("compareStationsSummary").textContent, "已选 2 站");

    // Borough coverage model normalizes names, separates unmatched rows and
    // computes the station-weighted coverage used by the map summary.
    const {
      normalizeBoroughName, boroughCoverageModel, boroughBubbleSize,
    } = context.window.bikeTest;
    assert.equal(normalizeBoroughName("  London Borough of Southwark "), "southwark");
    assert.equal(normalizeBoroughName("Kensington & Chelsea"), "kensingtonandchelsea");
    const boroughGeometry = {type: "Polygon", coordinates: [[
      [-0.1, 51.49], [-0.07, 51.49], [-0.07, 51.51], [-0.1, 51.51], [-0.1, 51.49],
    ]]};
    const boroughFeatures = [
      {properties: {LAD25NM: "Southwark", LAD25CD: "E09000028", LONG: -0.08, LAT: 51.5}, geometry: boroughGeometry},
      {properties: {LAD25NM: "Kensington and Chelsea", LAD25CD: "E09000033", LONG: -0.19, LAT: 51.5}, geometry: boroughGeometry},
      {properties: {LAD25NM: "Westminster", LAD25CD: "E09000034", LONG: -0.13, LAT: 51.5}, geometry: boroughGeometry},
    ];
    const boroughModel = boroughCoverageModel([
      {name: "Southwark", stations: 14, valid_hours: 100, coverage: 0.949},
      {name: "Kensington & Chelsea", stations: 6, valid_hours: 40, coverage: 0.5},
      {name: "行政区未提供", stations: 2, valid_hours: 10, coverage: 0.25},
    ], boroughFeatures);
    assert.equal(boroughModel.points.length, 2);
    assert.equal(boroughModel.unmatched.length, 1);
    assert.equal(boroughModel.missingCount, 1);
    assert.equal(boroughModel.totalStations, 22);
    assert.equal(boroughModel.points.find(point => point.name === "Southwark").coveragePercent.toFixed(1), "94.9");
    assert.equal(boroughModel.points.find(point => point.name === "Kensington and Chelsea").displayName, "Kensington & Chelsea");
    assert.equal(boroughModel.unmatched[0].coveragePercent.toFixed(1), "25.0");
    assert.equal(boroughModel.weightedCoverage.toFixed(3), "0.763");
    assert.ok(boroughBubbleSize(20) > boroughBubbleSize(5));
    const mergedBorough = boroughCoverageModel([
      {name: "Southwark", stations: 2, valid_hours: 10, coverage: 0.5},
      {name: " southwark ", stations: 2, valid_hours: 10, coverage: 1},
    ], boroughFeatures);
    assert.equal(mergedBorough.points.find(point => point.name === "Southwark").stations, 4);
    assert.equal(mergedBorough.points.find(point => point.name === "Southwark").coverage, 0.75);
    console.log("PASS borough coverage model normalizes names, separates unmatched rows and weights coverage");

    // Exercise the production drawing path against real clustering, including
    // refreshes, an empty replay frame, a recreated basemap source and fallback.
    const {S, drawBikes, finishOverviewPick, heatmap, summaries} = context.window.bikeTest;
    element("bikeStation").options = [];
    const capturedAt = "2026-11-21T07:55:00Z";
    S.data = {
      ...initial,
      cursor: "2026-11-21T07:59:59Z",
      source_stats: {laqn: {valid: 24}, breathe: {valid: 66}},
      air_metrics: ["PM25", "NO2"],
      air_counts: {laqn: {PM25: 24, NO2: 40}, breathe: {PM25: 66, NO2: 60}},
      air_info: {observed_at: capturedAt},
      counts: {empty: 0, full: 0, available: 0, roads: 0},
      bike_info: {captured_at: capturedAt, gap: true},
      road_info: {captured_at: null, gap: false},
      air: {features: []}, bikes: {features: []}, breaks: [],
    };
    summaries();
    let kpis = element("cityKpis").innerHTML;
    assert.match(kpis, /class="city-kpi-air"/);
    assert.match(kpis, /<th scope="col">LAQN<\/th><th scope="col">Breathe London<\/th>/);
    assert.match(kpis, /PM<sub>2\.5<\/sub><\/th><td><strong>24<\/strong><\/td><td><strong>66<\/strong><\/td>/);
    assert.match(kpis, /NO<sub>2<\/sub><\/th><td><strong>40<\/strong><\/td><td><strong>60<\/strong><\/td>/);
    assert.match(kpis, /有值站点数 · 所选小时/);
    assert.equal((kpis.match(/class="city-kpi-foot"/g) || []).length, 3);
    assert.equal((kpis.match(/class="city-kpi-foot[^"]*" title="[^"]*"><span>/g) || []).length, 3);
    assert.equal((kpis.match(/<span>所选小时无可用快照<\/span>/g) || []).length, 2);
    assert.equal((kpis.match(/<strong>—<\/strong>/g) || []).length, 4);
    assert.doesNotMatch(kpis, /<strong>0<\/strong>/);
    S.data.bike_info = {captured_at: capturedAt, gap: false};
    S.data.road_info = {captured_at: capturedAt, gap: false};
    S.data.counts.available = 455;
    summaries();
    kpis = element("cityKpis").innerHTML;
    assert.match(kpis, /有车有空位<\/small><strong>455<\/strong>/);
    assert.match(kpis, /道路事件<\/h2>\s*<div class="city-kpi-body"><strong>0<\/strong>/);
    assert.ok(kpis.includes("采集于 " + browserTime(capturedAt)));
    assert.doesNotMatch(kpis, /无可用快照/);

    S.data.bike_info = {captured_at: capturedAt, gap: false, source: "public_archive", age_minutes: 15};
    summaries();
    kpis = element("cityKpis").innerHTML;
    assert.match(kpis, /有车有空位<\/small><strong>455<\/strong>/);
    assert.match(kpis, /公开归档 · .*距所选 15 分钟/);

    S.data.bike_info = {captured_at: capturedAt, gap: false, mode: "nearest", age_minutes: 40};
    S.data.road_info = {captured_at: null, gap: false, reconstructed: true, event_count: 101};
    S.data.counts.roads = 101;
    summaries();
    kpis = element("cityKpis").innerHTML;
    assert.match(kpis, /最近快照/);
    assert.match(kpis, /距所选 40 分钟/);
    assert.match(kpis, /按前后快照交集重建 · 无原始快照/);
    assert.match(kpis, /道路事件<\/h2>\s*<div class="city-kpi-body"><strong>101<\/strong>/);
    assert.match(kpis, /class="city-kpi-foot kpi-note"/);

    // Older API replies without per-pollutant counts keep the original two-column card.
    const legacy = {...S.data};
    delete legacy.air_counts;
    delete legacy.air_metrics;
    delete legacy.air_info;
    S.data = legacy;
    summaries();
    kpis = element("cityKpis").innerHTML;
    assert.doesNotMatch(kpis, /class="city-kpi-air"/);
    assert.match(kpis, /LAQN<\/small><strong>24<\/strong>/);
    assert.match(kpis, /Breathe London<\/small><strong>66<\/strong>/);
    assert.match(kpis, /有值站点数 · PM<sub>2\.5<\/sub>/);

    let heatmapOption;
    S.charts.regressionHeatmap = {setOption(option){heatmapOption=option;}};
    heatmap("regressionHeatmap", [{time:"2026-11-18T01:00:00Z",value:12},{time:"2026-11-18T02:00:00Z",value:null}]);
    assert.equal(heatmapOption.visualMap.dimension,2);
    assert.equal(heatmapOption.visualMap.seriesIndex,1);
    assert.equal(heatmapOption.series[0].data.length,2);
    assert.equal(heatmapOption.series[1].data.length,1);
    assert.equal(heatmapOption.series[1].data[0][2],12);
    context.window.Supercluster = require("../assets/vendor/supercluster-8.0.1.min.js");
    const bike = (id, lon, bikes) => ({type:"Feature", geometry:{type:"Point", coordinates:[lon,51.5]}, properties:{id,bikes}});
    const points = [bike("a",-.1,0), bike("b",-.1001,7), bike("c",-.4,2)];
    context.document.createElement = () => ({style:{}, setAttribute(){}, addEventListener(){}});
    context.maplibregl = {Marker:function({element}) {
      this.setLngLat = this.addTo = () => this;
      this.remove = () => {this.removed = true;};
    }};
    element("layerBikes").checked = true;
    let zoom = 10, rendered;
    let mapSource = {setData(value) {rendered = value;}};
    S.ready = true;
    S.map = {getSource:() => mapSource, getZoom:() => zoom, getLayer:()=>true, setLayoutProperty(){}};
    S.data = {bikes:{type:"FeatureCollection",features:points}};
    drawBikes();
    const represented = () => S.bikeMarkers.reduce((count,item) => count + (item.feature.properties.point_count || 1),0);
    assert.equal(represented(),3);
    assert.ok(S.bikeMarkers.some(item => item.feature.properties.point_count === 2));
    element("layerBikes").checked = false;
    drawBikes();
    assert.ok(S.bikeMarkers.every(item => item.element.hidden));
    element("layerBikes").checked = true;
    drawBikes();
    assert.ok(S.bikeMarkers.every(item => !item.element.hidden));
    const oldMarkers = S.bikeMarkers.map(item => item.marker);
    zoom = 13;
    drawBikes();
    assert.deepEqual(Array.from(rendered.features, f=>f.properties.id).sort(),["a","b","c"]);
    assert.equal(S.bikeMarkers.length,0);
    assert.ok(oldMarkers.every(marker=>marker.removed));
    assert.equal(rendered.features.find(f=>f.properties.id === "a").properties.bikes,0);
    S.data = {bikes:{type:"FeatureCollection",features:[]}};
    drawBikes();
    assert.equal(rendered.features.length,0);
    assert.equal(S.bikeMarkers.length,0);
    S.data = {bikes:{type:"FeatureCollection",features:points}};
    mapSource = {setData(value) {rendered = value;}};
    zoom = 10;
    drawBikes();
    assert.equal(represented(),3);
    context.window.Supercluster = null;
    S.data = {bikes:{type:"FeatureCollection",features:points.slice()}};
    drawBikes();
    assert.equal(rendered.features.length,3);
    assert.equal(S.bikeMarkers.length,0);
    S.start = new Date(Date.now() - 48 * 3600000);
    S.end = new Date();
    const stamp = new Date().toISOString();
    const pending = [];
    answer = (path, params) => {
      if (path === "/api/health") return Promise.resolve({status:"fresh",index:2,observed_at:stamp});
      if (path === "/api/analysis/weather") {
        assert.equal(params.context_only,"1");
        assert.equal(params.aggregate,"mean");
        assert.equal(params.weather_site,undefined);
        assert.equal(params.site,undefined);
        return Promise.resolve({station_name:"London Test Weather",latest:{time:stamp,
          TEMPERATURE:{value_num:10,unit:"°C"},HUMIDITY:{value_num:82,unit:"%"},
          WIND_SPEED:{value_num:3.4,unit:"m/s"},MSLP:{value_num:1011,unit:"hPa",samples:2}}});
      }
      assert.equal(path, "/api/analysis/nearby");
      return new Promise(resolve => pending.push({params, resolve}));
    };
    const resources = {bikes:[],roads:[],air:[],available_bikes:0,
      bike_info:{captured_at:stamp},road_info:{captured_at:stamp}};
    const reading = value => ({status:"nearest",value,unit:"ug/m3",observed_at:stamp,
      period_seconds:3600,stations:[{name:"Test monitor",source:"laqn",distance_m:10}]});
    const response = value => ({...resources,empty_docks:0,local_air_by_metric:{PM25:reading(value), PM10:reading(17),
      NO2:reading(22), O3:{status:"insufficient"}}});
    element("nearRadius").value = "500";
    element("roadRadius").value = "1000";
    assert.equal(S.outlookTab,"advice");
    element("outlookTab-advice").keydown({key:"ArrowRight",preventDefault() {}});
    assert.equal(element("outlookPanel-air").hidden,false);
    assert.equal(element("outlookTab-air").focused,true);
    element("outlookTab-nearby").click();
    assert.equal(element("outlookTab-nearby").getAttribute("aria-selected"),"true");
    const firstPick = finishOverviewPick([-.11,51.51]);
    const secondPick = finishOverviewPick([-.12,51.52]);
    assert.equal(pending[1].params.kind,"point");
    assert.equal(pending[1].params.include_air_metrics,"1");
    assert.ok(Math.abs(pending[1].params.lon + .12) < 1e-10);
    assert.equal(pending[1].params.lat,51.52);
    assert.equal(element("selectedActions").hidden,true);
    assert.match(element("linkSubtitle").textContent,/51\.52000/);
    pending[1].resolve(response(12));
    await secondPick;
    pending[0].resolve(response(999));
    await firstPick;
    const local = element("localAir").innerHTML;
    assert.match(local,/PM<sub>2\.5<\/sub><\/span> <strong>12<\/strong>/);
    assert.match(local,/PM<sub>10<\/sub><\/span> <strong>17<\/strong>/);
    assert.match(local,/NO<sub>2<\/sub><\/span> <strong>22<\/strong>/);
    assert.match(local,/O<sub>3<\/sub>[\s\S]*附近观测不足/);
    const weatherHtml = element("outlookWeather").innerHTML;
    assert.match(weatherHtml,/class="city-kpi-body"[\s\S]*class="city-kpi-foot"/);
    assert.match(weatherHtml,/class="city-kpi-weather-source"/);
    assert.match(weatherHtml,/class="city-kpi-metrics"/);
    assert.match(weatherHtml,/温度 °C<\/small><strong>10<\/strong>/);
    assert.match(weatherHtml,/湿度 %<\/small><strong>82<\/strong>/);
    assert.match(weatherHtml,/风速 m\/s<\/small><strong>3.4<\/strong>/);
    assert.match(weatherHtml,/气压 hPa<\/small><strong>1011<\/strong>/);
    assert.match(weatherHtml,/title="2 站平均"/);
    assert.match(weatherHtml,/London Test Weather/);
    assert.doesNotMatch(weatherHtml,/部分观测未提供单位/);
    assert.equal(requests.filter(path=>path === "/api/analysis/weather").length,1);
    assert.equal(S.outlookTab,"nearby");
    assert.equal((element("nearbyStat").innerHTML.match(/<b>0<\/b>/g) || []).length,5);
    assert.deepEqual(Array.from(rendered.features, f=>f.properties.kind),["selected","resources","roads"]);
    const rings = rendered.features[1].geometry.coordinates;
    const requestCount = requests.length;
    element("healthGroup").value = "asthma";
    element("healthGroup").change();
    assert.match(element("citizenCard").innerHTML,/哮喘行动计划/);
    assert.equal(requests.length,requestCount);
    element("nearRadius").value = "2000";
    const rangeChange = element("nearRadius").change();
    assert.notEqual(rendered.features[1].geometry.coordinates[0][1],rings[0][1]);
    assert.equal(pending[2].params.resources_only,"1");
    pending[2].resolve({...resources,bike_info:{gap:true},local_air:null});
    await rangeChange;
    assert.equal(element("localAir").innerHTML,local);
    assert.match(element("nearbyStat").innerHTML,/<b>暂无数据<\/b><span>可用车辆<\/span>/);
    assert.equal(requests.length,requestCount+1);

    // Nearby counts stay on one strip while the three detail lists are tabs.
    for (const label of ["可用车辆", "空车位", "自行车站", "道路事件", "监测点"])
      assert.match(element("nearbyStat").innerHTML, new RegExp(`<span>${label}</span>`));
    assert.equal((element("nearbyStat").innerHTML.match(/<div>/g) || []).length, 5);
    assert.doesNotMatch(element("nearbyStat").innerHTML + element("nearbyBikesList").innerHTML, /采集于/);
    assert.equal(element("nearbyStatus").textContent,"");
    assert.equal(element("nearbyDetails").hidden,false);
    assert.equal(S.nearbyTab,"bikes");
    assert.equal(element("nearbyTab-bikes").getAttribute("aria-selected"),"true");
    assert.equal(element("nearbyPanel-bikes").hidden,false);
    assert.equal(element("nearbyPanel-roads").hidden,true);
    assert.equal(element("nearbyPanel-air").hidden,true);
    assert.equal(element("nearbyBikesCount").textContent,"—");
    assert.equal(element("nearbyRoadsCount").textContent,"0");
    assert.equal(element("nearbyAirCount").textContent,"0");
    element("nearbyTab-air").click();
    assert.equal(S.nearbyTab,"air");
    assert.equal(element("nearbyTab-air").getAttribute("aria-selected"),"true");
    assert.equal(element("nearbyPanel-air").hidden,false);
    assert.equal(element("nearbyPanel-bikes").hidden,true);
    element("nearbyTab-air").keydown({key:"ArrowRight",preventDefault() {}});
    assert.equal(S.nearbyTab,"bikes");
    element("nearbyTab-bikes").keydown({key:"End",preventDefault() {}});
    assert.equal(S.nearbyTab,"air");
    element("nearbyTab-air").keydown({key:"Home",preventDefault() {}});
    assert.equal(S.nearbyTab,"bikes");

    // Older nearby responses have station-level dock counts but no dock total.
    const station = {name:"Test bike station",bikes:1,distance_m:10};
    const dockScenarios = [
      {docks:[9,15], expected:"24"},
      {docks:[0,0], expected:"0"},
      {docks:[9,null], expected:"9（部分站点）"},
      {docks:[null,null], expected:"暂无数据"},
      {docks:[], expected:"0"},
    ];
    for (const [index, {docks, expected}] of dockScenarios.entries()) {
      const refresh = element("nearRadius").change();
      pending[3+index].resolve({...resources,available_bikes:docks.length,
        bikes:docks.map(empty_docks=>({...station,empty_docks})),local_air:null});
      await refresh;
      assert.match(element("nearbyStat").innerHTML,new RegExp(`<b>${expected}</b><span>空车位</span>`));
    }

    // Replaying hours must not collapse or rewrite the weather KPI between ticks.
    const weatherElement = element("outlookWeather");
    let weatherWrites = 0, weatherMarkup = weatherElement.innerHTML;
    Object.defineProperty(weatherElement, "innerHTML", {
      configurable: true,
      get() { return weatherMarkup; },
      set(value) { weatherMarkup = String(value); weatherWrites++; },
    });
    const weatherPending = [];
    answer = (path) => {
      assert.equal(path, "/api/analysis/weather");
      return new Promise((resolve, reject) => weatherPending.push({resolve, reject}));
    };
    const {refreshOverviewWeather} = context.window.bikeTest;
    const weatherCursor = (hours) => new Date(Date.parse(stamp) + hours * 3600000).toISOString();
    const weatherPayload = {station:"london-mean", station_name:"伦敦 3 站平均",
      latest:{time:stamp, TEMPERATURE:{value_num:10,unit:"°C"}, HUMIDITY:{value_num:82,unit:"%"},
              WIND_SPEED:{value_num:3.4,unit:"m/s"}, MSLP:{value_num:1011,unit:"hPa"}}};
    S.overviewWeather = null;
    S.overviewWeatherHtml = "";
    S.overviewWeatherKey = null;
    element("weatherStation").value = "";
    const firstWeather = refreshOverviewWeather({start:S.start.toISOString(), end:S.end.toISOString()});
    assert.equal(weatherWrites, 1);
    assert.match(weatherMarkup, /class="city-kpi-metrics"/);
    assert.equal((weatherMarkup.match(/<strong>—<\/strong>/g) || []).length, 4);
    assert.match(weatherMarkup, /正在查询气象观测/);
    weatherPending[0].resolve(weatherPayload);
    await firstWeather;
    assert.match(weatherMarkup, /伦敦 3 站平均/);
    assert.match(weatherMarkup, /温度 °C/);
    assert.match(weatherMarkup, /<strong>10<\/strong>/);
    assert.equal(weatherWrites, 2);
    // The weather card timestamp is formatted at paint time, so changing the
    // display timezone must repaint it instead of leaving the old zone behind.
    const {repaintOverviewWeather} = context.window.bikeTest;
    const weatherTime = (zone) => new Intl.DateTimeFormat("zh-CN", {timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).format(new Date(stamp));
    assert.match(weatherMarkup, new RegExp(weatherTime("Europe/London")));
    element("timezone").value = "Asia/Shanghai";
    repaintOverviewWeather();
    assert.match(weatherMarkup, new RegExp(weatherTime("Asia/Shanghai")));
    assert.match(weatherMarkup, /GMT\+8/);
    element("timezone").value = "Europe/London";
    repaintOverviewWeather();
    assert.match(weatherMarkup, new RegExp(weatherTime("Europe/London")));
    const settledWrites = weatherWrites;
    const nextWeather = refreshOverviewWeather({start:S.start.toISOString(), end:S.end.toISOString(), cursor:weatherCursor(1)});
    assert.equal(weatherWrites, settledWrites);
    assert.match(weatherMarkup, /伦敦 3 站平均/);
    weatherPending[1].resolve(weatherPayload);
    await nextWeather;
    assert.equal(weatherWrites, settledWrites);
    const emptyWeather = refreshOverviewWeather({start:S.start.toISOString(), end:S.end.toISOString(), cursor:weatherCursor(2)});
    assert.equal(weatherWrites, settledWrites);
    weatherPending[2].resolve({...weatherPayload, latest:null});
    await emptyWeather;
    assert.equal(weatherWrites, settledWrites + 1);
    assert.equal((weatherMarkup.match(/<strong>—<\/strong>/g) || []).length, 4);
    assert.match(weatherMarkup, /所选时段暂无伦敦气象观测/);
    const recoveredWeather = refreshOverviewWeather({start:S.start.toISOString(), end:S.end.toISOString(), cursor:weatherCursor(3)});
    weatherPending[3].resolve(weatherPayload);
    await recoveredWeather;
    const recoveredWrites = weatherWrites;
    assert.match(weatherMarkup, /温度 °C/);
    assert.match(weatherMarkup, /<strong>10<\/strong>/);
    const failedWeather = refreshOverviewWeather({start:S.start.toISOString(), end:S.end.toISOString(), cursor:weatherCursor(4)});
    weatherPending[4].reject(new Error("offline"));
    await failedWeather;
    assert.equal(weatherWrites, recoveredWrites);
    assert.match(weatherMarkup, /温度 °C/);
    assert.match(weatherMarkup, /<strong>10<\/strong>/);
    S.overviewWeather = null;
    S.overviewWeatherHtml = "";
    S.overviewWeatherKey = null;
    const coldWeather = refreshOverviewWeather({start:S.start.toISOString(), end:S.end.toISOString(), cursor:weatherCursor(5)});
    assert.equal(weatherWrites, recoveredWrites + 1);
    weatherPending[5].reject(new Error("offline"));
    await coldWeather;
    assert.equal(weatherWrites, recoveredWrites + 2);
    assert.match(weatherMarkup, /伦敦气象观测暂不可用/);

    // One skeleton per pollutant keeps the local-air panel from resizing between hours.
    const {renderLocalAir, toggleAirReferences, loadNearby} = context.window.bikeTest;
    const referenceStations = [
      {name:"Sensor A", id:"BL0001", source:"breathe", distance_m:120, value:4.2, unit:"ug/m3"},
      {name:"Sensor B", id:"BL0002", source:"breathe", distance_m:340, value:5.1, unit:"ug/m3"},
    ];
    renderLocalAir({
      PM25:{status:"estimated", value:4.6, unit:"ug/m3", observed_at:stamp, period_seconds:3600, stations:referenceStations},
      PM10:{status:"insufficient"}, NO2:{status:"insufficient"}, O3:{status:"insufficient"},
    });
    const localMarkup = element("localAir").innerHTML;
    assert.equal((localMarkup.match(/<li><p class="local-air-value">/g) || []).length, 4);
    const localRows = localMarkup.split('<li><p class="local-air-value">').slice(1);
    const localParagraphs = localRows.map(row => (row.match(/<p/g) || []).length);
    // Each row keeps the value paragraph as the split marker plus two trailing lines.
    assert.ok(localParagraphs.every(count => count === 2), JSON.stringify(localParagraphs));
    assert.match(localMarkup, /class="air-ref-toggle"/);
    assert.match(localMarkup, /aria-controls="air-ref-PM25"/);
    assert.match(localMarkup, /class="nearby-list air-ref-list" hidden/);
    const referenceButton = {
      values: {"aria-controls":"air-ref-PM25", "aria-expanded":"false"},
      getAttribute(name) { return this.values[name]; },
      setAttribute(name, value) { this.values[name] = value; },
    };
    const referenceList = element("air-ref-PM25");
    referenceList.hidden = true;
    toggleAirReferences({target:{closest: () => referenceButton}});
    assert.equal(referenceList.hidden, false);
    assert.equal(referenceButton.values["aria-expanded"], "true");
    toggleAirReferences({target:{closest: () => referenceButton}});
    assert.equal(referenceList.hidden, true);
    S.airRefOpen = {};

    // A cursor-only replay refresh keeps the rendered readings until the response arrives.
    const localBefore = element("localAir").innerHTML;
    let nearbyResolve;
    answer = (path) => {
      if (path === "/api/analysis/weather") return Promise.resolve({station:"laqn", latest:null});
      if (path === "/api/health") return Promise.resolve({status:"stale"});
      if (path === "/api/analysis/nearby") return new Promise(resolve => { nearbyResolve = resolve; });
      return Promise.resolve({});
    };
    S.hours = [Date.now() - 7200000, Date.now() - 3600000];
    S.start = new Date(Date.now() - 86400000);
    S.end = new Date();
    element("cityMode").value = "history";
    element("replayHour").value = 0;
    const refreshing = loadNearby();
    assert.equal(element("localAir").innerHTML, localBefore);
    nearbyResolve({bikes:[], roads:[], air:[], available_bikes:0, empty_docks:0,
      bike_info:{}, road_info:{}, trend:null,
      local_air_by_metric:{PM25:{status:"insufficient"}}});
    await refreshing;
    assert.match(element("localAir").innerHTML, /附近观测不足|读数暂不可用/);

    // During playback only the map layer is refreshed; a full refresh still
    // asks for the nearby panel. This is what keeps 48 frames affordable.
    const {S: state, refreshCity} = context.window.bikeTest;
    const detailFeature = {
      type: "Feature", geometry: {type: "Point", coordinates: [-0.1, 51.5]},
      properties: {id: "BL0027", source: "breathe", name: "Test", value: 12, stale: false,
                   observed_at: "2026-11-22T10:00:00Z", unit: "ug/m3", quality: "R"},
    };
    const mapReply = {
      air: {features: [detailFeature]}, bikes: {features: []}, roads: {features: []},
      breaks: [], counts: {}, bike_info: {}, road_info: {}, coverage: {}, source_stats: {},
      air_counts: {laqn: {}, breathe: {}}, air_metrics: [], start: "", end: "",
    };
    answer = (path) => {
      if (path === "/api/map") return Promise.resolve(mapReply);
      if (path === "/api/analysis/nearby")
        return Promise.resolve({bikes: [], roads: [], air: [], available_bikes: 0,
          empty_docks: 0, bike_info: {}, road_info: {}, trend: null,
          local_air_by_metric: {PM25: {status: "insufficient"}}});
      if (path === "/api/analysis/series") return Promise.resolve({available_sites: [], stations: []});
      return Promise.resolve({});
    };
    state.selected = {kind: "air", feature: detailFeature, center: [-0.1, 51.5]};
    const quiet = requests.length;
    await refreshCity(false, {detail: false});
    assert.deepEqual(requests.slice(quiet), ["/api/map"]);
    const loud = requests.length;
    await refreshCity(false);
    assert.ok(requests.slice(loud).includes("/api/analysis/nearby"),
      "a full refresh must still load the nearby panel");
  }
}
async function main() {
  testAnalysisMarkup();
  testOverviewKpiPlaceholders();
  testRichSelectShell();
  testAttributionCardMarkup();
  for (const failure of ["missing-library", "webgl-failure", "network-error"]) await testMapFailure(failure);
  console.log("PASS map library, WebGL and basemap network failures preserve data queries and table navigation");
  console.log("PASS bike clusters preserve all stations, expand at street zoom, refresh and retain raw-point fallback");
  console.log("PASS coordinate selection, request ordering, population advice, radii and resource statuses");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
