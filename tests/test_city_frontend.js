const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("../assets/city.js"), "utf8");

for (const failure of ["missing-library", "webgl-failure", "network-error"]) {
  const elements = new Map();
  const requests = [];
  const events = {};
  const values = {
    timezone: "Europe/London", cityRange: "48", cityMode: "latest",
    cityMetric: "PM25", citySource: "", cityPeriod: "900",
  };
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: values[id] || "", hidden: false, textContent: "",
      addEventListener() {}, classList: {toggle() {}},
      setAttribute() {}, removeAttribute() {},
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
        api(path) { requests.push(path); return new Promise(() => {}); },
        time: String,
        calendarDate(date) { return date.toISOString().slice(0, 10); },
        dateRange() { throw new Error("not used"); },
      },
      maplibregl: library,
      addEventListener() {},
    },
    maplibregl: library,
    document: {getElementById: element, querySelectorAll: () => tabs},
    location: {protocol: "http:"},
    requestAnimationFrame: handler => handler(),
    console, Date, Intl, Map, Set, Promise,
  };
  const instrumented = source.replace(/\}\)\(\);\s*$/, "window.bikeTest = {S, drawBikes, renderCitizen, heatmap};})();");
  vm.runInNewContext(instrumented, context);
  if (failure === "network-error") events.error();
  assert.match(element("mapNotice").textContent, /未加载|无法初始化|暂不可用/);
  assert.deepEqual(requests, ["/api/map"]);
  tabs[2].click();
  assert.equal(element("tablesPage").hidden, false);
  if (failure === "missing-library") {
    // Exercise the production drawing path against real clustering, including
    // refreshes, an empty replay frame, a recreated basemap source and fallback.
    const {S, drawBikes, renderCitizen, heatmap} = context.window.bikeTest;
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
    renderCitizen({name:"Test monitor",value:12,unit:"ug/m3",observed_at:stamp},
      {bike_info:{captured_at:stamp},road_info:{captured_at:stamp},available_bikes:2,roads:[]});
    assert.match(element("citizenCard").innerHTML,/当前出行参考/);
    assert.match(element("citizenCard").innerHTML,/PM₂.₅/);
    renderCitizen(null,{bike_info:{},road_info:{},available_bikes:2,roads:[]});
    assert.match(element("citizenCard").innerHTML,/暂无有效空气监测读数/);
    assert.doesNotMatch(element("citizenCard").innerHTML,/可用车辆 2 辆/);
  }
}
console.log("PASS map library, WebGL and basemap network failures preserve data queries and table navigation");
console.log("PASS bike clusters preserve all stations, expand at street zoom, refresh and retain raw-point fallback");
