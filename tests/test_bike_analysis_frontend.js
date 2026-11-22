const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

async function testReplay() {
  const start = Date.parse("2026-11-22T12:00:00Z");
  const at = minutes => new Date(start + minutes * 60000).toISOString();
  const [first, second, third, fourth, fifth] = [0, 5, 9, 30, 39].map(at);
  const sites = [{id: "A", name: "Central"}, {id: "B", name: "Nearby"}];
  const points = [first, second, third, fourth, fifth].map(captured_at => ({
    captured_at, bikes: 4, empty_docks: 12,
    neighbors: [{id: "B", distance_m: 170}],
    borrow: {tight: 1, valid: 1}, return: {tight: 0, valid: 1},
  }));
  let batches = [first, second, third, fourth];
  let zone = "London";
  let now = 0;
  let lastId = 0;
  const timers = new Map();
  const pendingFrames = new Map();
  const nodes = new Map();
  const events = new Map();
  const charts = new Map();
  const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
  async function advance(delay) {
    const end = now + delay;
    for (;;) {
      const next = [...timers].sort((a, b) => a[1].time - b[1].time)[0];
      if (!next || next[1].time > end) break;
      const [id, timer] = next;
      timers.delete(id);
      now = timer.time;
      timer.run();
      await flush();
    }
    now = end;
  }
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {
      value: "", hidden: false, textContent: "",
      addEventListener(event, callback) { this[event] = callback; },
      setAttribute(key, value) { this[key] = value; },
      classList: {toggle() {}},
    });
    return nodes.get(id);
  }
  const window = {
    AirWatchData: {
      time(value) { return zone + " " + value; },
      async api(path, params) {
        switch (path.split("/").at(-1)) {
          case "timeline":
            assert.equal(params, undefined);
            return {start: first, end: at(60), batches, stations: sites, radius_m: 500, tight_max: 2,
              freshness_seconds: 600, batch_meta: {[fourth]: {source: "public_archive", freshness_seconds: 1200}}};
          case "station":
            assert.equal(params.site, "A");
            return {points: points.filter(point => batches.includes(point.captured_at))};
          case "frame":
            return new Promise(resolve => pendingFrames.set(params.captured_at, resolve));
          default:
            assert.fail("Unexpected request " + path);
        }
      },
    },
    echarts: {
      init(element) {
        const chart = {setOption(option, replace) { if (replace) this.option = option; }, resize() {}};
        charts.set(element, chart);
        return chart;
      },
      connect() {},
    },
    addEventListener(name, callback) { events.set(name, callback); },
  };
  vm.runInNewContext(fs.readFileSync(require.resolve("../assets/bike-analysis.js"), "utf8"), {
    window, document: {getElementById: node}, requestAnimationFrame: run => run(),
    setTimeout(run, delay) { timers.set(++lastId, {run, time: now + delay}); return lastId; },
    clearTimeout(id) { timers.delete(id); },
  });
  const controller = window.AirWatchBikeAnalysis;
  function seek(capturedAt) {
    node("bikeTimeSlider").input({target: {value: Date.parse(capturedAt) / 1000}});
  }
  async function resolveFrame(capturedAt, bikes) {
    const resolve = pendingFrames.get(capturedAt);
    assert.ok(resolve, capturedAt + " was requested");
    pendingFrames.delete(capturedAt);
    resolve({captured_at: capturedAt, source: capturedAt === fourth ? "public_archive" : "tfl", stations: [
      {...sites[0], coordinates: [0, 51.5], bikes, empty_docks: 12, capacity: 20},
      {...sites[1], coordinates: [0, 51.501], bikes: 1, empty_docks: 4, capacity: 5},
    ]});
    await flush();
  }

  controller.show();
  assert.equal(node("bikeMapView").hidden, false);
  assert.equal(node("bikeTrends").hidden, true);
  node("bikeShowTrends").click();
  assert.equal(node("bikeMapView").hidden, true);
  assert.equal(node("bikeTrendEmpty").hidden, false);
  assert.equal(node("bikeTrendCharts").hidden, true);
  node("bikeShowMap").click();
  await flush();
  await advance(85);
  await resolveFrame(fourth, 4);
  assert.equal(node("bikeMap").hidden, true);
  assert.match(node("bikeCapture").textContent, /12:30/);
  assert.match(node("bikeCapture").textContent, /公开归档/);
  assert.equal(node("bikeArchiveCredit").hidden, false);
  seek(at(50));
  assert.match(node("bikeCapture").textContent, /12:30/);
  seek(new Date(Date.parse(at(50)) + 1000).toISOString());
  assert.match(node("bikeCapture").textContent, /缺测/);
  assert.equal(node("bikeArchiveCredit").hidden, true);
  seek(fourth);
  node("bikeStation").change({target: {value: "A"}});
  await flush();
  assert.match(node("bikeNearbyStatus").textContent, /1 \/ 1/);
  assert.match(node("bikeNeighbors").innerHTML, /bike-low">1/);
  node("bikeReturn").click();
  assert.match(node("bikeNearbyStatus").textContent, /0 \/ 1/);
  assert.doesNotMatch(node("bikeNeighbors").innerHTML, /bike-low">1/);
  assert.equal(node("bikeTrends").hidden, true, "station selection keeps the map view active");
  node("bikeShowTrends").click();
  assert.equal(node("bikeShowTrends")["aria-pressed"], "true");
  assert.equal(node("bikeTrendCharts").hidden, false);
  assert.equal(node("bikeStation").value, "A");
  const chartData = charts.get(node("bikePressureChart")).option.series[0].data;
  assert.equal(chartData.at(-2)[1], null, "chart breaks during the sampling gap");
  assert.equal(chartData.at(-1)[1], 0);
  node("bikeShowMap").click();

  seek(first);
  await advance(85);
  seek(second);
  await advance(85);
  await resolveFrame(second, 8);
  await resolveFrame(first, 3);
  assert.equal(node("bikeCurrentBikes").textContent, 8, "a slow earlier frame cannot replace the latest selection");
  assert.match(node("bikeCapture").textContent, /12:05/);
  zone = "China";
  events.get("airwatch:timezone")();
  assert.match(node("bikeCapture").textContent, /China/);

  seek(at(19));
  await advance(85);
  await resolveFrame(third, 5);
  assert.match(node("bikeCapture").textContent, /12:09/);
  seek(new Date(Date.parse(at(19)) + 1000).toISOString());
  assert.match(node("bikeCapture").textContent, /缺测/);
  assert.equal(node("bikeCurrentBikes").textContent, "—");
  assert.equal(node("bikeNeighborTable").hidden, true);
  seek(third);
  node("bikePlay").click();
  await advance(700);
  assert.match(node("bikeCapture").textContent, /缺测/);
  const pausedAt = node("bikeTimeSlider").value;
  node("bikeShowTrends").click();
  await advance(700);
  assert.equal(node("bikeTimeSlider").value, pausedAt, "switching to trends pauses playback");
  assert.equal(node("bikePlay").textContent, "播放");
  node("bikeShowMap").click();
  node("bikePlay").click();
  await advance(700);
  assert.match(node("bikeCapture").textContent, /12:30/);

  seek(second);
  batches = [...batches, fifth];
  await node("bikeApply").click();
  assert.match(node("bikeCapture").textContent, /12:05/, "refresh preserves historical replay");
  seek(fifth);
  await advance(85);
  await resolveFrame(fifth, 6);
  const sixth = at(43);
  batches = [...batches, sixth];
  await controller.refresh();
  await advance(85);
  await resolveFrame(sixth, 7);
  assert.match(node("bikeCapture").textContent, /12:43/, "refresh advances a cursor parked at the latest batch");
  assert.equal(node("bikeCurrentBikes").textContent, 7);
}

testReplay().then(() => console.log("PASS bicycle analysis views, replay, gaps, metric switch, timezone and refresh without a map"))
  .catch(error => { console.error(error); process.exitCode = 1; });
