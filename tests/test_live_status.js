// Status aggregation, failure isolation, update polling and request hardening.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "assets/live.js"), "utf8")
  .replace(/\}\)\(\);\s*$/,
    "window.liveTest = {state, api, refresh, renderStatus, update, renderUpdate};})();");

function makeHarness() {
  const elements = new Map();
  const messages = [];
  const dispatched = [];
  const windowListeners = {};
  const calls = [];
  let respond = () => ({ok: true, status: 200, text: async () => "{}"});
  let nowMs = Date.parse("2026-11-23T10:00:00Z");

  function element(id) {
    if (!elements.has(id)) {
      const node = {
      value: id === "pageSize" ? "50" : id === "timezone" ? "Europe/London" : "", checked: false, hidden: false, open: false,
      textContent: "", innerHTML: "", disabled: false, style: {},
      selectedOptions: [{text: "默认"}],
      classList: {toggle(name, on) { this[name] = on; }}, setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
      addEventListener(name, handler) { this[name] = handler; },
      querySelectorAll() { return []; }, replaceChildren() {},
      showModal() { this.open = true; }, close() { this.open = false; },
      };
      if (id === "updateMessage") {
        let text = "";
        Object.defineProperty(node, "textContent", {
          get: () => text, set: value => { text = value; messages.push(value); },
        });
      }
      elements.set(id, node);
    }
    return elements.get(id);
  }

  class FakeDate extends Date {
    constructor(...args) { super(...(args.length ? args : [nowMs])); }
    static now() { return nowMs; }
  }

  const context = {
    window: {
      dispatchEvent(event) { dispatched.push(event); },
      addEventListener(name, handler) { windowListeners[name] = handler; },
    },
    document: {
      getElementById: element,
      addEventListener() {},
      hidden: false,
      createElement() { return {textContent: ""}; },
    },
    location: {protocol: "file:"},
    fetch: async (url, options = {}) => {
      calls.push(url);
      if (options.signal?.aborted) {
        const error = new Error("aborted");
        error.name = "AbortError";
        throw error;
      }
      return respond(url, options);
    },
    // The update loop waits with setTimeout; running short delays immediately
    // (and advancing the clock) keeps the test deterministic without fake timers.
    setTimeout(callback, milliseconds) {
      if (milliseconds <= 3000) { nowMs += milliseconds; callback(); }
      return 0;
    },
    clearTimeout() {},
    CustomEvent: function (name, options = {}) { this.type = name; this.detail = options.detail; },
    AbortController, URLSearchParams, Date: FakeDate, Intl, Promise, Map, Set, console,
  };
  vm.runInNewContext(source, context);

  return {
    elements, calls, messages, dispatched, windowListeners, element,
    setRespond(handler) { respond = handler; },
    setNow(value) { nowMs = value; },
    advance(ms) { nowMs += ms; },
    test: context.window.liveTest,
  };
}

const statusPayload = (jobs, extra = {}) => JSON.stringify({
  server_time: "2026-11-23T10:00:00+00:00", jobs, regions: [], collector_running: false,
  breathe_coverage: {active: 0, checked: 0, failed: 0},
  metoffice_budget: {used: 12, limit: 300}, ...extra,
});

async function testStatusMapping() {
  const harness = makeHarness();
  const {test, element} = harness;
  const jobs = [
    {job: "tfl_bike_archive", source: "public_archive", parent_source: "tfl",
     display_name: "自行车公开归档补录", status: "error", error: "offline",
     last_success_utc: null, latest_observation_utc: null, stale_run: false, stale_data: false},
    {job: "tfl_bikes", source: "tfl", parent_source: "tfl", display_name: "自行车",
     status: "success", last_success_utc: "2026-11-23T09:55:00+00:00",
     latest_observation_utc: null, stale_run: false, stale_data: false},
    {job: "laqn_hourly", source: "laqn", parent_source: "laqn", status: "success",
     last_success_utc: "2026-11-23T09:50:00+00:00", latest_observation_utc: null},
    {job: "met_other_daily", source: "metoffice", parent_source: "metoffice",
     display_name: "全国气象站点日更", status: "success",
     last_success_utc: "2026-11-23T06:00:00+00:00", latest_observation_utc: null},
  ];
  test.state.status = JSON.parse(statusPayload(jobs));
  test.state.source = "tfl";
  test.renderStatus();
  assert.equal(element("badge-tfl").textContent, "1 项需关注");
  assert.equal(element("badge-tfl").classList.warning, true);
  assert.equal(element("badge-laqn").textContent, "采集正常");
  assert.equal(element("badge-metoffice").textContent, "采集正常");
  assert.match(element("sourceStatus").innerHTML, /自行车公开归档补录/);
  assert.match(element("sourceStatus").innerHTML, /offline/);
  assert.match(element("sourceStatus").innerHTML, /自行车/);

  test.state.source = "metoffice";
  test.renderStatus();
  assert.match(element("sourceStatus").innerHTML, /全国气象站点日更/);
  assert.match(element("sourceStatus").innerHTML, /今日 UTC 请求用量 12 \/ 300/);

  test.state.status = JSON.parse(statusPayload([]));
  test.renderStatus();
  for (const source of ["laqn", "breathe", "tfl", "metoffice"])
    assert.equal(element("badge-" + source).textContent, "暂无任务");
}

async function testStatusFailureIsolation() {
  const harness = makeHarness();
  const {test, element} = harness;
  harness.setRespond(url => {
    if (url.startsWith("/api/status")) return {ok: false, status: 500, text: async () => "{\"error\":\"数据库暂时无法读取\"}"};
    return {ok: true, status: 200, text: async () => JSON.stringify({items: [], total: 0})};
  });
  await test.refresh();
  assert.equal(element("statusError").hidden, false);
  assert.match(element("statusError").textContent, /采集状态读取失败/);
  assert.equal(element("statusSummary").textContent, "状态读取失败");
  assert.equal(harness.dispatched.at(-1).type, "airwatch:refreshed");
}

async function testUpdatePollsRunOnly() {
  const harness = makeHarness();
  const {test, element} = harness;
  let polls = 0;
  harness.setRespond(url => {
    if (url === "/api/update") return {ok: true, status: 202, text: async () =>
      JSON.stringify({id: "run-1", state: "running", jobs: {}, finished_at: null})};
    if (url === "/api/update/run-1") {
      polls += 1;
      return {ok: true, status: 200, text: async () => JSON.stringify(polls < 2
        ? {id: "run-1", state: "running", jobs: {tfl_bikes: "running"}}
        : {id: "run-1", state: "completed", jobs: {tfl_bikes: "success (3 changes)"},
           finished_at: "2026-11-23T10:00:01+00:00"})};
    }
    if (url.startsWith("/api/status")) return {ok: true, status: 200, text: async () => statusPayload([])};
    return {ok: true, status: 200, text: async () => JSON.stringify({items: [{id: "fresh"}], total: 1})};
  });
  await test.update();
  assert.equal(test.state.updating, false);
  assert.equal(element("updateButton").disabled, false);
  assert.equal(polls, 2);
  assert.equal(test.state.updateFinishedAt, "2026-11-23T10:00:01+00:00");
  const measurements = harness.calls.filter(url => url.startsWith("/api/measurements")).length;
  const statuses = harness.calls.filter(url => url.startsWith("/api/status")).length;
  assert.equal(measurements, 1, "table must be refreshed once, after the run finishes");
  assert.equal(statuses, 1);
  const lastMeasurement = harness.calls.findIndex(url => url.startsWith("/api/measurements"));
  const lastPoll = harness.calls.map((url, index) => url.startsWith("/api/update/run-1") ? index : -1).at(-1);
  assert.ok(lastMeasurement > lastPoll);
  assert.ok(harness.messages.some(message => /已等待/.test(message)));
  assert.equal(element("updateFinished").textContent.startsWith("完成时间 "), true);
}

async function testUpdateStopsAfterLimit() {
  const harness = makeHarness();
  const {test, element} = harness;
  harness.setRespond(url => {
    if (url === "/api/update") return {ok: true, status: 202, text: async () =>
      JSON.stringify({id: "run-2", state: "running", jobs: {}, finished_at: null})};
    if (url === "/api/update/run-2") return {ok: true, status: 200, text: async () =>
      JSON.stringify({id: "run-2", state: "running", jobs: {}, finished_at: null})};
    return {ok: true, status: 200, text: async () => JSON.stringify({items: [], total: 0})};
  });
  await test.update();
  assert.equal(test.state.updating, false);
  assert.equal(element("updateButton").disabled, false);
  assert.match(element("updateMessage").textContent, /仍在后台运行/);
  assert.equal(harness.calls.filter(url => url.startsWith("/api/measurements")).length, 0);
  assert.ok(harness.calls.filter(url => url.startsWith("/api/update/run-2")).length > 400);
}

async function testApiErrors() {
  const harness = makeHarness();
  const {test} = harness;

  harness.setRespond(() => ({ok: false, status: 500, text: async () => "<html>boom</html>"}));
  await assert.rejects(() => test.api("/api/boom"), /请求失败（HTTP 500）/);

  harness.setRespond(() => ({ok: true, status: 200, text: async () => "not json"}));
  await assert.rejects(() => test.api("/api/broken"), /无法读取的响应/);

  harness.setRespond(() => new Promise(() => {}));
  await assert.rejects(() => test.api("/api/slow", {}, {timeoutMs: 20}), /请求超时/);

  harness.setRespond(() => ({ok: false, status: 403, text: async () => JSON.stringify({error: "请求必须来自本地页面"})}));
  await assert.rejects(() => test.api("/api/update", {}, {method: "POST"}), /请求必须来自本地页面/);
}

(async () => {
  await testStatusMapping();
  await testStatusFailureIsolation();
  await testUpdatePollsRunOnly();
  await testUpdateStopsAfterLimit();
  await testApiErrors();
  console.log("PASS status mapping, failure isolation, update polling cap and request errors");
})().catch(error => { console.error(error); process.exitCode = 1; });
