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
const official = {status:"fresh", index:7, observed_at:new Date(Date.now()-60000).toISOString()};
assert.match(insights.healthAdvice(official,"asthma").advice, /高强度/);
assert.match(insights.healthAdvice(official,"asthma").note, /哮喘行动计划/);
assert.equal(insights.healthAdvice({...official,index:3},"general").tone,"low");
assert.equal(insights.healthAdvice({...official,index:4},"general").tone,"moderate");
assert.equal(insights.healthAdvice({...official,index:10},"older").tone,"high");
assert.equal(insights.healthAdvice({...official,status:"stale"},"children").tone,"unknown");
assert.equal(insights.healthAdvice(official,"general",true).tone,"unknown");
assert.equal(insights.healthAdvice({...official,index:11},"general").tone,"unknown");
console.log("PASS official health bands, group advice, missing data and replay");
const region = {type:"FeatureCollection",features:[{type:"Feature",geometry:{type:"Polygon",coordinates:[[[0,0],[4,0],[4,4],[0,4],[0,0]],[[1,1],[2,1],[2,2],[1,2],[1,1]]]},properties:{name:"test"}}]};
const monitor=(point,value,stale=false)=>({geometry:{type:"Point",coordinates:point},properties:{value,stale}});
const zone = insights.pollutionZones(region,[monitor([.5,.5],10),monitor([3,3],30),monitor([1.5,1.5],999),monitor([3,2],999,true)],[5,15,25,35]);
assert.equal(zone.features[0].properties.pollution_count,2);
assert.equal(zone.features[0].properties.pollution_value,20);
assert.equal(zone.features[0].properties.pollution_color, insights.pollutionPalette[2]);
assert.equal(insights.pollutionZones(region,[],[]).features[0].properties.pollution_value,null);
console.log("PASS pollution zones exclude polygon holes, stale stations and missing data");
