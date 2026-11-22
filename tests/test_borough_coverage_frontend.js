const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const echarts = require("../assets/vendor/echarts-5.6.0.min.js");

const source = fs.readFileSync(require.resolve("../assets/city.js"), "utf8");
const startup = "  S.overviewMapHost =";
assert.ok(source.includes(startup));
const setup = source.slice(0, source.indexOf(startup)) +
  "window.coverageTest = {S, boroughCoverageModel, renderBoroughList, selectBorough, drawAnalysis};})();";

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    const classes = new Set();
    elements.set(id, {
      classList: {
        toggle: (name, on) => on ? classes.add(name) : classes.delete(name),
        contains: (name) => classes.has(name),
      },
      dataset: {},
    });
  }
  return elements.get(id);
}

async function main() {
  const list = element("boroughCoverage");
  const detail = { hidden: true };
  const southwarkRow = {
    dataset: { borough: "Southwark" },
    classList: { toggle() {} },
    setAttribute(name, value) { this[name] = value; },
    querySelector: () => detail,
  };
  list.querySelectorAll = () => [southwarkRow];
  const window = { AirWatchData: { time: (value) => value }, echarts };
  vm.runInNewContext(setup, { window, document: { getElementById: element } });
  const { S, boroughCoverageModel, renderBoroughList, selectBorough, drawAnalysis } = window.coverageTest;

  const rows = [
    { name: "Islington", stations: 3, coverage: 0.958 },
    { name: "Southwark", stations: 14, coverage: 0.949 },
    { name: "Harrow", stations: 4, coverage: 0.93175 },
    { name: "Westminster", stations: 1, coverage: 0.947 },
    { name: "Newham", stations: 1, coverage: 0.9 },
    { name: "Bexley", stations: 1, coverage: 0.994 },
  ];
  const features = [...rows.map((row) => row.name), "Camden"].map((name) => ({
    properties: { LAD25NM: name, LAD25CD: name, LONG: -0.12, LAT: 51.5 },
    geometry: { type: "Polygon", coordinates: [[[-0.2, 51.4], [-0.1, 51.5], [-0.2, 51.4]]] },
  }));
  const model = boroughCoverageModel(rows, features);
  assert.equal(model.weightedCoverage.toFixed(3), "0.947");
  assert.equal(model.missingCount, 1);

  const start = "2026-11-20T10:00:00Z";
  const end = "2026-11-22T10:00:00Z";
  const cursor = "2026-11-21T14:59:59Z";
  S.analysisSection = "borough";
  S.analysisSeriesCursor = cursor;
  S.series = { boroughs: rows, start, end };
  S.boroughMapReady = true;
  S.boroughGeo = { features };
  let option, mapClick;
  const actions = [];
  S.charts.boroughMap = {
    on: (event, handler) => { if (event === "click") mapClick = handler; },
    resize() {},
    setOption: (value) => { option = value; },
    dispatchAction: (action) => { actions.push(action); },
  };
  drawAnalysis();
  await Promise.resolve();
  assert.equal(element("boroughRange").textContent, `统计范围 ${start} 至 ${cursor}`);
  assert.match(element("boroughSummary").textContent, /94\.7%/);
  assert.match(element("boroughLegend").innerHTML, /≤ −2.*94\.7%.*≥ \+2.*无数据/);
  const tooltip = option.tooltip.formatter;
  assert.match(tooltip({ name: "Islington" }), /95\.8%.*\+1\.1 个百分点.*约缺测 42 小时/);
  assert.match(tooltip({ name: "Southwark" }), /94\.9%.*\+0\.2 个百分点.*约缺测 51 小时/);
  assert.match(tooltip({ name: "Newham" }), /−4\.7 个百分点/);
  assert.match(tooltip({ name: "Westminster" }), /较全市 0\.0 个百分点/);
  assert.match(tooltip({ name: "Camden" }), /无可用站点/);
  const colors = Object.fromEntries(option.geo.regions.map((region) => [region.name, region.itemStyle.areaColor]));
  assert.equal(colors.Newham, echarts.color.lerp(0, ["#de9560", "#40586d", "#70c2e8"]));
  assert.equal(colors.Bexley, echarts.color.lerp(1, ["#de9560", "#40586d", "#70c2e8"]));
  assert.notEqual(colors.Islington, colors.Southwark);
  assert.notEqual(colors.Westminster, colors.Camden);

  const rowHtml = (name) => list.innerHTML.split("</button>").find((row) => row.includes(`data-borough='${name}'`));
  assert.ok(list.innerHTML.indexOf("data-borough='Newham'") < list.innerHTML.indexOf("data-borough='Islington'"));
  assert.match(rowHtml("Newham"), /left:0%;width:50%/);
  assert.match(rowHtml("Bexley"), /left:50%;width:50%/);
  assert.match(rowHtml("Westminster"), /data-direction='neutral'>0\.0/);
  mapClick({ name: "Southwark" });
  assert.equal(southwarkRow["aria-pressed"], "true");
  assert.equal(detail.hidden, false);
  assert.match(element("boroughSelectionText").textContent, /约缺测 51 小时/);
  selectBorough("Islington");
  assert.equal(actions.at(-1).name, "Islington");
  assert.equal(detail.hidden, true);

  const tied = boroughCoverageModel([
    { name: "Westminster", stations: 2, coverage: 0.9584 },
    { name: "Newham", stations: 4, coverage: 0.9581 },
    { name: "Bexley", stations: 4, coverage: 0.9582 },
  ]);
  renderBoroughList(tied, { mapReady: false });
  assert.deepEqual(Array.from(list.innerHTML.matchAll(/data-borough='([^']+)'/g), (match) => match[1]),
    ["Bexley", "Newham", "Westminster"]);

  delete window.echarts;
  S.analysisSeriesCursor = null;
  drawAnalysis();
  assert.equal(element("boroughRange").textContent, `统计范围 ${start} 至 ${end}`);
  assert.match(rowHtml("Southwark"), /\+0\.2/);
  assert.equal(element("boroughLegend").hidden, true);
  assert.match(element("boroughMapNote").textContent, /已保留覆盖率列表/);

  S.series = { boroughs: [{ name: "行政区未提供", stations: 1, coverage: 0.91 }], start, end };
  drawAnalysis();
  southwarkRow.dataset.borough = "行政区未提供";
  selectBorough("行政区未提供");
  assert.equal(southwarkRow["aria-pressed"], "true");
  assert.equal(detail.hidden, false);

  S.series = { boroughs: [], start, end };
  drawAnalysis();
  assert.match(element("boroughSummary").textContent, /暂无数据/);
  assert.match(list.innerHTML, /暂无行政区覆盖数据/);
  assert.doesNotMatch(list.innerHTML, /borough-row/);

  console.log("PASS borough coverage deltas, map colors, sorting, selection, date range and fallback");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
