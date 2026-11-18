const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "assets/live.js"), "utf8")
  .replace(/\}\)\(\);\s*$/, "window.boundaryTest = {state, loadTable, openDetail};})();");
const elements = new Map(), dispatched = [];
const buttons = ["laqn", "breathe", "tfl", "metoffice", "boundaries"].map(source => ({
  dataset: {source}, classList: {toggle() {}}, setAttribute() {}, removeAttribute() {},
}));
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: id === "pageSize" ? "50" : "", checked: false, hidden: false,
    textContent: "", innerHTML: "", placeholder: "", style: {},
    selectedOptions: [{text: "默认"}],
    classList: {toggle() {}}, setAttribute() {}, addEventListener() {},
    querySelector() { return {addEventListener(name, handler) { this[name] = handler; }}; },
    querySelectorAll() { return buttons; },
    showModal() { this.open = true; }, close() { this.open = false; },
  });
  return elements.get(id);
}
let mapButton;
element("boundaryDetail").querySelector = () => (mapButton = {addEventListener(name, handler) { this[name] = handler; }});
const context = {
  window: {dispatchEvent(event) { dispatched.push(event); }, addEventListener() {}},
  document: {getElementById: element, addEventListener() {}},
  location: {protocol: "file:"},
  fetch: async url => ({ok: true, async json() {
    if (url.startsWith("/api/")) return {items: [], total: 0};
    return JSON.parse(fs.readFileSync(path.join(root, url.slice(1)), "utf8"));
  }}),
  CustomEvent: function (name, options) { this.type = name; this.detail = options.detail; },
  URLSearchParams, Date, Intl, Promise, Map, Set, console,
};
vm.runInNewContext(source, context);
(async () => {
  const {state, loadTable, openDetail} = context.window.boundaryTest;
  state.source = "boundaries";
  await loadTable();
  assert.equal(state.total, 34);
  assert.equal(state.items[0].name, "London");
  assert.equal(element("showAllFilter").hidden, true);
  assert.equal(element("collectionStatus").hidden, true);
  assert.match(element("dataTable").innerHTML, /面积（平方公里）.*周长（公里）/);
  assert.match(element("dataTable").innerHTML, /City of London/);
  element("search").value = "E09000001";
  await loadTable();
  assert.equal(state.total, 1);
  assert.equal(state.items[0].name, "City of London");
  openDetail(0);
  assert.match(element("boundaryDetail").innerHTML, /面积.*周长.*2025 年 12 月/);
  mapButton.click();
  assert.equal(dispatched[0].type, "airwatch:show-boundary");
  assert.equal(dispatched[0].detail.code, "E09000001");
  assert.equal(dispatched[0].detail.level, "borough");
  assert.equal(dispatched[0].detail.bounds.length, 2);
  element("search").value = "";
  element("pageSize").value = "25";
  state.page = 2;
  await loadTable();
  assert.equal(state.items.length, 9);
  assert.match(element("pageInfo").textContent, /第 2 \/ 2 页/);
  state.source = "laqn";
  element("laqnMode").value = "900";
  await loadTable();
  assert.equal(element("showAllFilter").hidden, false);
  assert.equal(element("collectionStatus").hidden, false);
  console.log("PASS boundary table, search, pagination, detail and map event");
})().catch(error => { console.error(error); process.exitCode = 1; });
