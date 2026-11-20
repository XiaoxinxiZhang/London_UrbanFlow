(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const city = window.AirWatchCity;
  if (!city || !$('journeyPage')) return;
  const state = {start:null, end:null, picking:null, result:null, request:null};
  const labels = {walk_to_bike:"① 步行取车", cycle:"② 骑行至还车站",
    walk_to_destination:"③ 步行到终点", walk:"步行", personal_bike:"个人自行车骑行"};
  const esc = value => String(value ?? "—").replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const km = meters => meters >= 1000 ? (meters/1000).toFixed(2) + " 公里" : Math.round(meters) + " 米";
  const minutes = seconds => Math.max(1, Math.round(seconds/60)) + " 分钟";
  const time = value => window.AirWatchData?.time?.(value) || value || "—";
  function updatePlanButton() {
    $("planJourney").disabled = !!state.request || !state.start || !state.end;
  }
  function setLabels() {
    $("journeyPage").dataset.picking = state.picking || "";
    $("startLabel").textContent = state.start ? state.start.map(v => v.toFixed(5)).join(", ") : "尚未选择";
    $("endLabel").textContent = state.end ? state.end.map(v => v.toFixed(5)).join(", ") : "尚未选择";
    $("journeyMapHint").textContent = state.picking ? "点击地图设置" + (state.picking === "start" ? "起点" : "终点") :
      state.start && state.end ? "选择方式并规划路线" : "选择起点与终点";
    $("pickStart").classList.toggle("active", state.picking === "start");
    $("pickEnd").classList.toggle("active", state.picking === "end");
    $("maxWalkLabel").hidden = $("journeyMode").value !== "shared_bike";
    updatePlanButton();
    draw();
  }
  function resetPlan() {
    state.request?.abort();
    state.request = null;
    state.result = null;
    const message = state.start && state.end ? "出行设置已更新，请重新规划路线。" : "在地图上依次选择起点和终点。";
    $("journeyResult").innerHTML = `<p class="muted">${message}</p>`;
    setLabels();
  }
  function blank() { return {type:"FeatureCollection",features:[]}; }
  function ensureLayers() {
    const map = city.getMap();
    if (!map || (!map.getLayer("journey-point-label") && !map.isStyleLoaded?.())) return false;
    if (!map.getSource("journey-legs")) map.addSource("journey-legs", {type:"geojson",data:blank()});
    if (!map.getSource("journey-points")) map.addSource("journey-points", {type:"geojson",data:blank()});
    if (!map.getLayer("journey-leg-line")) map.addLayer({id:"journey-leg-line",type:"line",source:"journey-legs",
      layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":["match",["get","kind"],"cycle","#63dfc6","personal_bike","#63dfc6","#f5c36c"],"line-width":6,"line-opacity":.9}});
    if (!map.getLayer("journey-point-dot")) map.addLayer({id:"journey-point-dot",type:"circle",source:"journey-points",
      paint:{"circle-radius":9,"circle-color":["match",["get","kind"],"start","#65e3bd","end","#ef8b8b","pickup","#59bbdf","#b18ade"],
        "circle-stroke-color":"#102131","circle-stroke-width":2}});
    if (!map.getLayer("journey-point-label")) map.addLayer({id:"journey-point-label",type:"symbol",source:"journey-points",
      layout:{"text-field":["get","label"],"text-size":13,"text-offset":[0,1.5],"text-allow-overlap":true},
      paint:{"text-color":"#fff","text-halo-color":"#102131","text-halo-width":2}});
    return true;
  }
  function draw() {
    const map = city.getMap();
    if (!ensureLayers()) return;
    const point = (coords, kind, label) => ({type:"Feature",geometry:{type:"Point",coordinates:coords},properties:{kind,label}});
    const features = [];
    if (state.start) features.push(point(state.start,"start","起点"));
    if (state.end) features.push(point(state.end,"end","终点"));
    const pair = state.result?.alternatives?.[0];
    if (state.result?.legs?.length === 3 && pair) {
      features.push(point(state.result.legs[0].geometry.coordinates.at(-1),"pickup","取车"));
      features.push(point(state.result.legs[1].geometry.coordinates.at(-1),"dropoff","还车"));
    }
    map.getSource("journey-points").setData({type:"FeatureCollection",features});
    map.getSource("journey-legs").setData({type:"FeatureCollection",features:(state.result?.legs || []).map(leg =>
      ({type:"Feature",geometry:leg.geometry,properties:{kind:leg.kind}}))});
  }
  function renderEstimate(point, title) {
    const data = state.result?.context?.[point] || {};
    const rows = [];
    for (const [metric, name] of [["PM25","PM₂.₅"],["PM10","PM₁₀"],["NO2","NO₂"],["O3","O₃"]]) {
      const sources = data[metric] || {};
      const values = [["LAQN",sources.laqn],["Breathe London",sources.breathe]]
        .filter(([,value]) => value?.status === "estimated")
        .map(([source,value]) => `${source} ${value.value} ${esc(value.unit || "µg/m³")}（${value.station_count} 站，最近 ${km(value.nearest_m)}，最新 ${esc(time(value.latest_at))}）`);
      const observed = Object.values(sources).reduce((total,value) => total + (value?.station_count || 0), 0);
      rows.push(`<li><b>${name}</b> ${values.length ? values.join("；") : `周边有效站点不足（${observed} 站）`}</li>`);
    }
    return `<details class="journey-context"><summary>${title}周边浓度估计</summary><ul>${rows.join("")}</ul><p>同来源监测点反距离加权估计；不代表街道或路线实测浓度。</p></details>`;
  }
  function renderContext() {
    if (!state.result) return "";
    const daqi = state.result.context?.daqi || {};
    let advice = "官方 DAQI 暂无有效数据，不自动判断是否适合外出。";
    if (daqi.status === "fresh") {
      advice = daqi.index <= 3 ? "普通人群可按通常方式安排户外活动；敏感人群仍应遵循个人健康计划。" :
        daqi.index <= 6 ? "普通人群可照常活动；有心肺疾病者若症状加重，应调整户外体力活动。" :
        "建议减少污染暴露；老年人及心肺疾病敏感人群应调整较费力的户外活动。";
    }
    return `<section class="journey-environment"><h3>出行环境参考</h3><p><b>大伦敦官方 DAQI：</b>${daqi.status === "fresh" ? `${daqi.index} · ${esc(daqi.band)}（${esc(time(daqi.observed_at))}）` : "缺失或过期"}</p><p>${advice}</p><p><a href="https://www.gov.uk/government/publications/health-effects-of-air-pollution/health-advice-for-the-daily-air-quality-index-daqi" target="_blank" rel="noopener">查看 Defra 分人群指导</a> · 地区等级并非所选地点的浓度。</p>
      ${renderEstimate("start","起点")}${renderEstimate("end","终点")}</section>`;
  }
  function render() {
    const result = state.result;
    if (!result) return;
    let html = "";
    if (result.routing_error) html += `<p class="journey-warning">${esc(result.routing_error)}</p>`;
    if (result.message) html += `<p class="journey-warning">${esc(result.message)}</p>`;
    if (result.legs?.length) {
      const totalDistance = result.legs.reduce((sum,leg) => sum+leg.distance_m,0);
      const totalTime = result.legs.reduce((sum,leg) => sum+leg.duration_s,0);
      html += `<div class="journey-total"><strong>${minutes(totalTime)}</strong><span>${km(totalDistance)} · ${result.legs.length} 段路线</span></div>`;
      if (result.legs.length === 3) {
        const pair = result.alternatives[0];
        html += `<p><b>取车：</b>${esc(pair.pickup.name)} · ${pair.pickup.bikes} 辆<br><b>还车：</b>${esc(pair.dropoff.name)} · ${pair.dropoff.empty_docks} 空位</p>`;
      }
      html += result.legs.map(leg => `<details class="journey-leg" ${leg.kind === "cycle" || result.legs.length === 1 ? "open" : ""}><summary>${labels[leg.kind]} · ${km(leg.distance_m)} · ${minutes(leg.duration_s)}</summary><ol>${(leg.steps || []).map(step => `<li>${esc(step.instruction)}</li>`).join("")}</ol></details>`).join("");
      if (result.alternatives.length > 1) html += `<div class="journey-alternatives"><h3>备选站点</h3>${result.alternatives.slice(1).map(pair => `<button type="button" data-pickup="${esc(pair.pickup.id)}" data-dropoff="${esc(pair.dropoff.id)}">${esc(pair.pickup.name)} → ${esc(pair.dropoff.name)} · 约 ${minutes(pair.duration_s)}</button>`).join("")}</div>`;
      if (result.alerts.length) html += `<div class="journey-alerts"><h3>路线附近道路事件</h3><ul>${result.alerts.map(a => `<li>${esc(a.name)} · ${esc(a.category)} · 距路线约 ${km(a.distance_m)}</li>`).join("")}</ul></div>`;
    }
    if (result.bike_info?.captured_at && result.mode === "shared_bike") html += `<p class="muted">站点快照：${esc(time(result.bike_info.captured_at))}。车辆和空位未预留，出发前请重新规划。</p>`;
    html += renderContext();
    $("journeyResult").innerHTML = html;
    draw();
    if (result.legs?.length) {
      const coords = result.legs.flatMap(leg => leg.geometry.coordinates);
      const xs = coords.map(p => p[0]), ys = coords.map(p => p[1]);
      city.getMap()?.fitBounds?.([[Math.min(...xs),Math.min(...ys)],[Math.max(...xs),Math.max(...ys)]],
        {padding:70,maxZoom:15,duration:650});
    }
  }
  async function plan(pair) {
    if (!state.start || !state.end || state.request) return;
    const request = new AbortController();
    state.request = request;
    state.result = null;
    updatePlanButton();
    draw();
    $("journeyResult").textContent = "正在检查站点并计算道路路线…";
    try {
      const payload = {start:state.start,end:state.end,mode:$("journeyMode").value,
        max_walk_m:Number($("maxWalk").value)};
      if (pair) { payload.pickup_id = pair[0]; payload.dropoff_id = pair[1]; }
      const response = await fetch("/api/journeys/plan", {method:"POST",cache:"no-store",
        headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal:request.signal});
      const data = await response.json();
      if (state.request !== request) return;
      if (!response.ok) throw new Error(data.error || "规划失败");
      state.result = data;
      render();
    } catch (error) {
      if (state.request !== request) return;
      state.result = null;
      draw();
      $("journeyResult").textContent = error.message;
    } finally {
      if (state.request === request) {
        state.request = null;
        updatePlanButton();
      }
    }
  }
  function bindMap() {
    const map = city.getMap();
    if (!map || map.__airwatchJourneyBound) return;
    map.__airwatchJourneyBound = true;
    map.on("style.load", draw);
    map.on("click", event => {
      if ($("journeyPage").hidden || !state.picking) return;
      state[state.picking] = [event.lngLat.lng,event.lngLat.lat];
      state.picking = null;
      resetPlan();
    });
    draw();
  }
  $("pickStart").addEventListener("click", () => {state.picking="start";city.showPage("journey");setLabels();bindMap();});
  $("pickEnd").addEventListener("click", () => {state.picking="end";city.showPage("journey");setLabels();bindMap();});
  $("swapJourney").addEventListener("click", () => {
    [state.start, state.end] = [state.end, state.start];
    resetPlan();
  });
  $("clearJourney").addEventListener("click", () => {
    state.start = state.end = state.picking = null;
    resetPlan();
  });
  $("journeyMode").addEventListener("change", resetPlan);
  $("maxWalk").addEventListener("change", resetPlan);
  $("planJourney").addEventListener("click", () => plan());
  $("journeyResult").addEventListener("click", event => {
    const button = event.target.closest("[data-pickup]");
    if (button) plan([button.dataset.pickup,button.dataset.dropoff]);
  });
  document.querySelector('[data-page="journey"]')?.addEventListener("click", () => {
    bindMap();
    setTimeout(bindMap,0);
    requestAnimationFrame(() => { if (state.result) render(); else city.fitLondonView?.(); });
  });
  setLabels();
})();
