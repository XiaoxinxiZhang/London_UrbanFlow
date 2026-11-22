const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

async function testJourney() {
  const elements = new Map();
  const mapEvents = new Map();
  const sources = new Map();
  const layers = new Map();
  const requests = [];
  let styleLoaded = true;

  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      dataset: {}, value: "", hidden: false, classList: {toggle() {}},
      addEventListener(name, handler) { this[name] = handler; },
      set textContent(value) { this.innerHTML = value; },
    });
    return elements.get(id);
  }

  const map = {
    on(name, handler) { mapEvents.set(name, handler); },
    isStyleLoaded: () => styleLoaded,
    getSource: id => sources.get(id),
    addSource(id, {data}) { sources.set(id, {data, setData(value) { this.data = value; }}); },
    getLayer: id => layers.get(id),
    addLayer(layer) { layers.set(layer.id, layer); },
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
    element(button).click();
    mapEvents.get("click")({lngLat: {lng, lat: 51.5}});
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
  complete(requests[2]);
  await latestPlan;
  assert.equal(plan.disabled, false);
  assert.match(result.innerHTML, /个人自行车骑行/);
  assert.equal(lines(), 1);

  // A pending map source update can make isStyleLoaded false with layers present.
  styleLoaded = false;
  element("swapJourney").click();
  assert.equal(element("startLabel").innerHTML, "-0.12000, 51.50000");
  assert.match(result.innerHTML, /请重新规划路线/);
  assert.equal(lines(), 0);

  element("journeyMode").value = "shared_bike";
  element("journeyMode").change();
  const sharedPlan = plan.click();
  const {payload, resolve} = requests[3];
  resolve({ok: true, json: async () => ({
    mode: "shared_bike", message: "自行车快照缺失或超过 15 分钟", alternatives: [], alerts: [],
    legs: [{kind: "walk", distance_m: 850, duration_s: 600,
      geometry: {type: "LineString", coordinates: [payload.start, payload.end]}}]
  })});
  await sharedPlan;
  assert.match(result.innerHTML, /当前显示步行备选路线。自行车快照缺失/);
}

testJourney().then(() => console.log("PASS journey cancels outdated results, preserves current requests and clears the map"))
  .catch(error => { console.error(error); process.exitCode = 1; });
