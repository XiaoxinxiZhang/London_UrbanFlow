// A display-timezone change must repaint every panel that shows a timestamp.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "assets/city.js"), "utf8")
  .replace(/\}\)\(\);\s*$/,
    "window.cityTest = {S, repaintForTimezone, renderLocalAir, renderNearbyResources};})();");

const elements = new Map();
let zone = "UTC";
const requests = [];

function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: id === "timezone" ? "Europe/London" : "", checked: false, hidden: false,
    textContent: "", innerHTML: "", style: {}, dataset: {},
    classList: {toggle() {}}, setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
    addEventListener(name, handler) { this[name] = handler; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    focus() {}, scrollIntoView() {},
  });
  return elements.get(id);
}

const context = {
  window: {
    AirWatchInsights: require("../assets/insights.js"),
    AirWatchData: {
      api: async (pathname) => { requests.push(pathname); return {features: []}; },
      time: value => value ? zone + "|" + new Date(value).toISOString().slice(0, 16) : "—",
      calendarDate: () => "2026-11-23",
      recentWindow: () => ({start: "2026-11-21T00:00:00.000Z", end: "2026-11-23T00:00:00.000Z"}),
    },
    addEventListener() {},
  },
  document: {
    getElementById: element,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => ({style: {}, classList: {add() {}}, append() {}, appendChild() {}, addEventListener() {}}),
  },
  location: {protocol: "file:"},   // skip map init and the initial refresh
  requestAnimationFrame: handler => handler(),
  setTimeout, clearTimeout, console, Date, Intl, Map, Set, Promise, JSON, Math, Number, String,
};
vm.runInNewContext(source, context);

const {S, repaintForTimezone} = context.window.cityTest;
const observedIso = new Date().toISOString();
const observedMinute = observedIso.slice(0, 16);

S.selected = {kind: "air", feature: {properties: {id: "BL0027", name: "Alpha", source: "breathe"}}, center: [-0.1, 51.5]};
S.officialDAQI = {status: "fresh", index: 2, band: "低", observed_at: observedIso};
S.localAirHasData = true;
S.localAirReadings = {
  PM25: {status: "measured", value: 12, unit: "ug/m3", period_seconds: 3600,
         observed_at: observedIso, stations: [{source: "laqn", name: "Alpha"}]},
};
S.nearbyData = {
  bikes: [{name: "Dock", distance_m: 120, bikes: 3, empty_docks: 5}], roads: [], air: [],
  available_bikes: 3, empty_docks: 5,
  bike_info: {mode: "nearest", captured_at: "2026-11-23T01:00:00Z", age_minutes: 60},
  road_info: {},
};
S.nearbyCursor = null;

repaintForTimezone();
const localAirUtc = element("localAir").innerHTML;
const nearbyUtc = element("nearbyStatus").textContent + element("nearbyBikesList").innerHTML;
const citizenUtc = element("citizenCard").innerHTML;
assert.match(localAirUtc, new RegExp("UTC\\|" + observedMinute));
assert.match(nearbyUtc, /UTC\|2026-11-23T01:00/);
assert.match(citizenUtc, new RegExp("UTC\\|" + observedMinute));

zone = "Asia/Shanghai";
repaintForTimezone();
assert.match(element("localAir").innerHTML, new RegExp("Asia/Shanghai\\|" + observedMinute));
assert.doesNotMatch(element("localAir").innerHTML, /UTC\|/);
assert.match(element("nearbyStatus").textContent + element("nearbyBikesList").innerHTML,
  /Asia\/Shanghai\|2026-11-23T01:00/);
assert.match(element("citizenCard").innerHTML, new RegExp("Asia/Shanghai\\|" + observedMinute));
// Repainting without a map payload must stay safe (GIS table falls back to empty).
assert.doesNotThrow(() => repaintForTimezone());

console.log("PASS timezone change repaints local air, nearby resources and health card");
