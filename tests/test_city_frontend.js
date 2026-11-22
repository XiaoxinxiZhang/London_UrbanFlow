const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { recentWindow } = require("../assets/live.js");
const source = fs.readFileSync(require.resolve("../assets/city.js"), "utf8");

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
  const library = failure === "missing-library" ? null : {
    Map: function () {
      if (failure === "webgl-failure") throw new Error("WebGL unavailable");
      return {addControl() {}, on(name, handler) { events[name] = handler; }, resize() {}};
    },
    NavigationControl: function () {},
    AttributionControl: function () {},
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
        time: String,
        calendarDate(date) { return date.toISOString().slice(0, 10); },
        recentWindow() { return recentWindow(now); },
      },
      maplibregl: library,
      addEventListener() {},
    },
    maplibregl: library,
    document: {getElementById: element, querySelectorAll: selector => selector === "[data-page]" ? tabs : []},
    location: {protocol: "http:"},
    requestAnimationFrame: handler => handler(),
    console, Date, Intl, Map, Set, Promise,
  };
  const instrumented = source.replace(/\}\)\(\);\s*$/, "window.bikeTest = {S, drawBikes, finishOverviewPick, heatmap, summaries};})();");
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
    assert.equal(element("analysisMetric").value, "NO2");
    mode.value = "latest";
    mode.change();
    assert.equal(mapQueries.at(-1).end, recentWindow(now).end);
    assert.equal(mapQueries.at(-1).cursor, undefined);
    assert.equal(element("replayControls").hidden, true);
    element("analysisMetric").value = "PM25";
    element("analysisMetric").change();
    assert.equal(mapQueries.at(-1).metric, "PM25");
    assert.equal(element("cityMetric").value, "PM25");

    // Exercise the production drawing path against real clustering, including
    // refreshes, an empty replay frame, a recreated basemap source and fallback.
    const {S, drawBikes, finishOverviewPick, heatmap, summaries} = context.window.bikeTest;
    element("bikeStation").options = [];
    const capturedAt = "2026-11-21T07:55:00Z";
    S.data = {
      ...initial,
      cursor: "2026-11-21T07:59:59Z",
      source_stats: {laqn: {valid: 24}, breathe: {valid: 66}},
      counts: {empty: 0, full: 0, available: 0, roads: 0},
      bike_info: {captured_at: capturedAt, gap: true},
      road_info: {captured_at: null, gap: false},
      air: {features: []}, bikes: {features: []}, breaks: [],
    };
    summaries();
    let kpis = element("cityKpis").innerHTML;
    assert.match(kpis, /所选小时 · PM₂.₅/);
    assert.equal((kpis.match(/所选小时无可用快照/g) || []).length, 2);
    assert.equal((kpis.match(/<strong>—<\/strong>/g) || []).length, 4);
    assert.doesNotMatch(kpis, /<strong>0<\/strong>/);
    S.data.bike_info = {captured_at: capturedAt, gap: false};
    S.data.road_info = {captured_at: capturedAt, gap: false};
    S.data.counts.available = 455;
    summaries();
    kpis = element("cityKpis").innerHTML;
    assert.match(kpis, /有车有空位<\/small><strong>455<\/strong>/);
    assert.match(kpis, /道路事件<\/h2>\s*<strong>0<\/strong>/);
    assert.ok(kpis.includes("采集于 " + capturedAt));
    assert.doesNotMatch(kpis, /无可用快照/);

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
        assert.equal(params.site,undefined);
        return Promise.resolve({station_name:"London Test Weather",latest:{time:stamp,TEMPERATURE:{value_num:10,unit:"°C"}}});
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
    assert.match(local,/PM₂\.₅<\/span> <strong>12<\/strong>/);
    assert.match(local,/PM₁₀<\/span> <strong>17<\/strong>/);
    assert.match(local,/NO₂<\/span> <strong>22<\/strong>/);
    assert.match(local,/O₃[\s\S]*附近观测不足/);
    assert.match(element("outlookWeather").innerHTML,/London Test Weather[\s\S]*10 °C/);
    assert.equal(requests.filter(path=>path === "/api/analysis/weather").length,1);
    assert.equal(S.outlookTab,"nearby");
    assert.match(element("nearbySummary").textContent,/可用车辆 0 · 道路事件 0/);
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
    assert.match(element("nearbySummary").textContent,/车辆暂无数据/);
    assert.equal(requests.length,requestCount+1);

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
      assert.match(element("nearbyContent").innerHTML,new RegExp(`<b>${expected}</b><span>空车位</span>`));
    }
  }
}
async function main() {
  for (const failure of ["missing-library", "webgl-failure", "network-error"]) await testMapFailure(failure);
  console.log("PASS map library, WebGL and basemap network failures preserve data queries and table navigation");
  console.log("PASS bike clusters preserve all stations, expand at street zoom, refresh and retain raw-point fallback");
  console.log("PASS coordinate selection, request ordering, population advice, radii and resource statuses");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
