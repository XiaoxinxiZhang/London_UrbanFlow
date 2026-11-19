const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const region = JSON.parse(fs.readFileSync(path.join(root, "data/london_region_2025.geojson"), "utf8"));
const boroughs = JSON.parse(fs.readFileSync(path.join(root, "data/london_boroughs_2025.geojson"), "utf8"));
assert.equal(region.type, "FeatureCollection");
assert.deepEqual(region.features.map(f => f.properties.RGN25CD), ["E12000007"]);
assert.equal(boroughs.type, "FeatureCollection");
assert.deepEqual(
  new Set(boroughs.features.map(f => f.properties.LAD25CD)),
  new Set(Array.from({length: 33}, (_, i) => `E090000${String(i + 1).padStart(2, "0")}`)),
);
assert.equal(boroughs.features.find(f => f.properties.LAD25CD === "E09000001").properties.LAD25NM, "City of London");
for (const feature of [...region.features, ...boroughs.features]) {
  assert.ok(["Polygon", "MultiPolygon"].includes(feature.geometry.type));
  assert.ok(feature.properties.Shape__Area > 0);
  assert.ok(feature.properties.Shape__Length > 0);
  assert.ok(Number.isFinite(feature.properties.LONG));
  assert.ok(Number.isFinite(feature.properties.LAT));
  function checkCoordinates(value) {
    if (typeof value[0] === "number") {
      assert.ok(value[0] > -1 && value[0] < 1);
      assert.ok(value[1] > 51 && value[1] < 52);
    } else value.forEach(checkCoordinates);
  }
  checkCoordinates(feature.geometry.coordinates);
}

const elements = new Map();
const events = {};
const windowEvents = {};
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: id === "basemap" ? "dark" : id === "timezone" ? "Europe/London" : id === "cityRange" ? "48" : "",
    checked: true, hidden: false, textContent: "", innerHTML: "",
    addEventListener(name, handler) { this[name] = handler; },
    scrollIntoView() {},
    classList: {toggle() {}}, setAttribute() {}, removeAttribute() {},
  });
  return elements.get(id);
}
const tabs = ["overview", "analysis", "tables"].map(page => ({
  dataset: {page}, addEventListener() {}, classList: {toggle() {}},
  setAttribute() {}, removeAttribute() {},
}));
const sources = new Map(), layers = new Map(), visibility = new Map(), filters = new Map(), layerOrder = [];
let focusedBounds;
const map = {
  addControl() {}, on(name, handler) { events[name] = handler; }, resize() {},
  addImage() {},
  getSource(id) { return sources.get(id); },
  addSource(id, source) { sources.set(id, {...source, setData(data) { this.data = data; }}); },
  getLayer(id) { return layers.get(id); },
  addLayer(layer, before) {
    layers.set(layer.id, layer);
    const index = layerOrder.indexOf(before);
    layerOrder.splice(index < 0 ? layerOrder.length : index, 0, layer.id);
  },
  moveLayer(id, before) {
    layerOrder.splice(layerOrder.indexOf(id), 1);
    layerOrder.splice(layerOrder.indexOf(before), 0, id);
  },
  setFilter(id, filter) { filters.set(id, filter); },
  fitBounds(bounds) { focusedBounds = bounds; },
  setLayoutProperty(id, property, value) { if (property === "visibility") visibility.set(id, value); },
  setStyle() { sources.clear(); layers.clear(); visibility.clear(); layerOrder.length = 0; },
};
const library = {
  Map: function () { return map; },
  NavigationControl: function () {}, AttributionControl: function () {},
};
const context = {
  window: {
    AirWatchData: {
      api() { return new Promise(() => {}); }, time: String,
      calendarDate(date) { return date.toISOString().slice(0, 10); },
      dateRange() { throw new Error("not used"); },
    },
    maplibregl: library, addEventListener(name, handler) { windowEvents[name] = handler; },
  },
  maplibregl: library,
  document: {
    getElementById: element, querySelectorAll: () => tabs,
    createElement(name) {
      assert.equal(name, "canvas");
      return {getContext() { return {
        beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, fill() {},
        getImageData() { return {data: new Uint8ClampedArray(32 * 32 * 4)}; },
      }; }};
    },
  },
  location: {protocol: "http:"}, requestAnimationFrame: handler => handler(),
  console, Date, Intl, Map, Set, Promise,
};
vm.runInNewContext(fs.readFileSync(path.join(root, "assets/city.js"), "utf8"), context);
events["style.load"]();
assert.equal(sources.get("london-region").data, "data/london_region_2025.geojson");
assert.equal(sources.get("london-boroughs").data, "data/london_boroughs_2025.geojson");
assert.equal(sources.has("london-rivers"), false);
assert.equal(layers.get("london-outer-boundary").type, "line");
assert.equal(layers.get("london-borough-boundaries").type, "line");
assert.equal(layers.has("london-thames"), false);
assert.ok(layerOrder.indexOf("london-borough-boundaries") < layerOrder.indexOf("bike-clusters"));
assert.notEqual(layers.get("london-outer-boundary").paint["line-color"], layers.get("london-borough-boundaries").paint["line-color"]);
assert.match(element("mapLegend").innerHTML, /大伦敦外边界.*行政区边界/);
element("layerBoundary").checked = false;
element("layerBoundary").change();
assert.equal(visibility.get("london-outer-boundary"), "none");
assert.equal(visibility.get("london-borough-boundaries"), "none");
const darkColor = layers.get("london-outer-boundary").paint["line-color"];
element("basemap").value = "positron";
element("basemap").change();
events["style.load"]();
assert.notEqual(layers.get("london-outer-boundary").paint["line-color"], darkColor);
assert.equal(visibility.get("london-outer-boundary"), "none");
assert.equal(visibility.get("london-borough-boundaries"), "none");
element("layerBoundary").checked = true;
element("layerBoundary").change();
const selectedBounds = [[-0.12, 51.50], [-0.07, 51.53]];
windowEvents["airwatch:show-boundary"]({detail: {
  level: "borough", code: "E09000001", bounds: selectedBounds,
}});
assert.deepEqual(focusedBounds, selectedBounds);
assert.equal(filters.get("london-selected-borough")[2], "E09000001");
assert.equal(visibility.get("london-selected-borough"), "visible");
element("basemap").change();
events["style.load"]();
assert.equal(layers.get("london-selected-borough").filter[2], "E09000001");
windowEvents["airwatch:show-boundary"]({detail: {
  level: "region", code: "E12000007", bounds: [[-0.55, 51.28], [0.34, 51.72]],
}});
assert.equal(filters.get("london-selected-region")[2], "E12000007");
assert.equal(filters.get("london-selected-borough")[2], "");
element("layerBoundary").checked = false;
element("layerBoundary").change();
assert.equal(visibility.get("london-selected-region"), "none");
console.log("PASS London boundary data, layers, toggle and style reload");
