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
  function healthAdvice(daqi, group = "general", replay = false) {
    const names = {general:"健康人群", older:"老年人", children:"儿童与照护者", asthma:"哮喘人群", heartlung:"心肺疾病人群"};
    const index = daqi?.index;
    const fresh = !replay && daqi?.status === "fresh" && Number.isInteger(index) && index >= 1 && index <= 10 &&
      Number.isFinite(Date.parse(daqi.observed_at)) && Date.now() - Date.parse(daqi.observed_at) <= 3 * HOUR && Date.parse(daqi.observed_at) <= Date.now();
    const condition = group === "asthma" || group === "heartlung";
    const note = group === "asthma" ? "遵循个人哮喘行动计划；也留意花粉等诱因，不自行调整药量。" :
      condition ? "按医生或护士提供的疾病管理计划安排活动。" :
      group === "children" ? "由照护者关注咳嗽、喘息等症状；有心肺疾病的儿童请同时参考对应人群建议。" :
      "出现新症状、症状加重或一周未改善，请联系医生或护士。";
    if (!fresh) return {tone:"unknown", basis:replay ? "历史回放 · 健康参考" : "大伦敦官方 DAQI · 暂不可用", title:names[group] || names.general,
      advice:replay ? "当前官方等级不适用于历史时刻。请结合当时的官方预报与个人健康计划查看历史观测。" : "官方等级缺失或过期，暂不生成按等级的活动判断。可查看下方地点读数，并在出行前核对官方预报。", note};
    const band = index <= 3 ? "低" : index <= 6 ? "中" : index <= 9 ? "高" : "极高";
    let advice;
    if (index <= 3) advice = "可继续通常的户外活动；敏感者即使在低污染时也可能有症状。";
    else if (index <= 6) advice = condition ? "适当调整户外高强度活动，尤其在症状加重时；遵循个人健康管理计划。" : "多数人可继续通常的户外活动；如出现症状，应减少污染暴露。";
    else advice = condition || group === "older" ? "减少污染暴露，并调整户外高强度活动，尤其在出现或加重症状时。" : "尽量减少污染暴露，尤其在出现症状时；调整户外活动安排。";
    return {tone:index <= 3 ? "low" : index <= 6 ? "moderate" : "high", basis:"大伦敦官方 DAQI · " + index + "/10 · " + band,
      title:names[group] || names.general, advice, note:note + " 地区等级提供健康背景，所选点读数用于了解附近污染状况。"};
  }
  const pollutionPalette = ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#99000d"];
  function pollutionZones(boundaries, stations, breaks) {
    function ringContains(point, ring) {
      let inside = false;
      for (let i=0, j=ring.length-1; i<ring.length; j=i++) {
        const a=ring[i], b=ring[j];
        if ((a[1]>point[1]) !== (b[1]>point[1]) && point[0] < (b[0]-a[0])*(point[1]-a[1])/(b[1]-a[1])+a[0]) inside=!inside;
      }
      return inside;
    }
    function contains(point, geometry) {
      const polygons = geometry.type === "MultiPolygon" ? geometry.coordinates : geometry.type === "Polygon" ? [geometry.coordinates] : [];
      return polygons.some(rings => rings.length && ringContains(point,rings[0]) && !rings.slice(1).some(ring => ringContains(point,ring)));
    }
    const validStations = stations.filter(f => typeof f.properties.value === "number" && Number.isFinite(f.properties.value) && !f.properties.stale);
    return {type:"FeatureCollection", features:boundaries.features.map(f => {
      const points=validStations.filter(s => contains(s.geometry.coordinates,f.geometry));
      const value=points.length ? points.reduce((n,s)=>n+s.properties.value,0)/points.length : null;
      const grade=value===null ? -1 : breaks.filter(b=>value>b).length;
      return {...f, properties:{...f.properties, pollution_value:value, pollution_count:points.length, pollution_color:grade<0 ? "#657481" : pollutionPalette[Math.min(4,grade)]}};
    })};
  }
  const exposed = {citizen, healthAdvice, pollutionZones, pollutionPalette};
  if (typeof module !== "undefined") module.exports = exposed;
  if (typeof window !== "undefined") window.AirWatchInsights = exposed;
})();
