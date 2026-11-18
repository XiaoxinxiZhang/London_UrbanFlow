const assert = require("node:assert/strict");
const insights = require("../assets/insights.js");

const now = Date.parse("2026-11-18T12:00:00Z");
const nearby = {
  bike_info: {captured_at: "2026-11-18T11:55:00Z"},
  road_info: {captured_at: "2026-11-18T11:50:00Z"},
  available_bikes: 7, roads: [{id: "R1"}],
};
const air = {name: "A", value: 20, observed_at: "2026-11-18T11:00:00Z"};
const points = [8, 9, 10, 11].map((hour, i) => ({time: `2026-11-18T${String(hour).padStart(2, "0")}:00:00Z`, value_num: 10 + 2 * i}));
const fresh = insights.citizen(air, nearby, points, now, false);
assert.equal(fresh.airFresh, true);
assert.equal(fresh.threeHourChange, 6);
assert.equal(fresh.availableBikes, 7);
assert.equal(fresh.roadCount, 1);

const stale = insights.citizen({...air, observed_at: "2026-11-18T08:00:00Z"},
  {...nearby, bike_info: {captured_at: "2026-11-18T11:30:00Z"}, road_info: {}}, points, now, false);
assert.equal(stale.airFresh, false);
assert.equal(stale.threeHourChange, null);
assert.equal(stale.availableBikes, null);
assert.equal(stale.roadCount, null);
assert.equal(insights.citizen(null, {}, [], now, false).airFresh, false);
assert.equal(insights.citizen(air, nearby, points, now, true).replay, true);
console.log("PASS current, missing and stale citizen data");
