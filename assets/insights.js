/* Current observation helpers shared by the dashboard and tests. */
(() => {
  "use strict";
  const HOUR = 3600000;
  const valid = value => typeof value === "number" && Number.isFinite(value) && value >= 0;
  function citizen(air, nearby, hourlyPoints, referenceMillis, replay) {
    const observation = air && air.value !== null && valid(Number(air.value)) && air.observed_at ? Date.parse(air.observed_at) : NaN;
    const airFresh = Number.isFinite(observation) && observation <= referenceMillis && referenceMillis - observation <= 3 * HOUR;
    const bikeTime = Date.parse(nearby?.bike_info?.captured_at || "");
    const roadTime = Date.parse(nearby?.road_info?.captured_at || "");
    const bikeFresh = !nearby?.bike_info?.gap && Number.isFinite(bikeTime) && bikeTime <= referenceMillis && referenceMillis - bikeTime <= 15 * 60000;
    const roadFresh = !nearby?.road_info?.gap && Number.isFinite(roadTime) && roadTime <= referenceMillis && referenceMillis - roadTime <= 15 * 60000;
    const points = (hourlyPoints || []).filter(point => valid(point.value_num) && Number.isFinite(Date.parse(point.time)) && Date.parse(point.time) <= referenceMillis)
      .sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
    let threeHourChange = null;
    if (airFresh && points.length >= 4) {
      const last = points[points.length - 1], previous = points[points.length - 4];
      if (Date.parse(last.time) - Date.parse(previous.time) === 3 * HOUR &&
          Date.parse(last.time) === Math.floor(observation / HOUR) * HOUR)
        threeHourChange = last.value_num - previous.value_num;
    }
    return {
      replay: !!replay, airFresh, bikeFresh, roadFresh, threeHourChange,
      air: air || null, airAgeMinutes: Number.isFinite(observation) ? Math.max(0, Math.round((referenceMillis - observation) / 60000)) : null,
      bikeTime: Number.isFinite(bikeTime) ? nearby.bike_info.captured_at : null,
      roadTime: Number.isFinite(roadTime) ? nearby.road_info.captured_at : null,
      availableBikes: bikeFresh ? nearby.available_bikes : null,
      roadCount: roadFresh ? nearby.roads.length : null,
    };
  }
  const exposed = {citizen};
  if (typeof module !== "undefined") module.exports = exposed;
  if (typeof window !== "undefined") window.AirWatchInsights = exposed;
})();
