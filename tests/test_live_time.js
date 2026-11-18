const assert = require("node:assert/strict");
const {dateRange, midnightUTC} = require("../assets/live.js");
assert.equal(midnightUTC("2026-07-01", "Europe/London"), "2026-06-30T23:00:00.000Z");
assert.equal(midnightUTC("2026-11-18", "Asia/Shanghai"), "2026-11-17T16:00:00.000Z");
for (const [date, hours] of [["2026-03-29", 23], ["2026-10-25", 25]]) {
  const range = dateRange(date, date, "Europe/London");
  assert.equal((Date.parse(range.end) - Date.parse(range.start)) / 3600000, hours);
}
assert.throws(() => dateRange("2026-11-20", "2026-11-18", "UTC"));
assert.throws(() => midnightUTC("2026-02-30", "UTC"));
console.log("PASS live browser timezone conversion, DST boundaries and invalid date ranges");
